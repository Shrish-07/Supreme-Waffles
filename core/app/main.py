"""FastAPI application factory.

Run:  uvicorn app.main:create_app --factory --port 8080   (from core/)
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from claimshield_contracts.version import CONTRACTS_VERSION

from . import __version__
from .audit import configure_logging
from .config import Settings, get_settings
from .container import Container, build_container
from .errors import DomainError
from .routers import cases, claims, disputes, health, human, relay_webhook, tools, visits, voice

log = logging.getLogger("claimshield.core")

DESCRIPTION = """
ClaimShield Core owns the case (single source of truth).

* **Public API** — used by the Relay service and other clients (Bearer service token).
* **/internal/tools/** — used by Fetch agents and the voice manager; scoped per caller.
* **/relay/webhook** — signed (HMAC-SHA256) idempotent patient events.

Prototype: synthetic data only, not HIPAA-ready.
"""

TAGS = [
    {"name": "cases", "description": "Cases, consents, timeline and notifications"},
    {"name": "visits", "description": "Recorded visits, transcripts and visit evidence"},
    {"name": "claims", "description": "Claims and discrepancy analysis"},
    {"name": "disputes", "description": "Dispute packages and patient approval"},
    {"name": "human", "description": "Human-in-the-loop questions and handoffs"},
    {"name": "voice", "description": "Insurer calls (Core owns the session)"},
    {"name": "relay", "description": "Signed events from the Relay service"},
    {"name": "internal-tools", "description": "Agent + voice tools (scoped service tokens)"},
    {"name": "health", "description": "Liveness and configured modes"},
]


async def _domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.to_detail()})


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    """Build the app. Fails fast if a live mode is missing its keys."""
    settings = settings or (container.settings if container else get_settings())
    configure_logging(settings.log_level)
    settings.validate_for_startup()
    for name in settings.using_dev_credentials():
        log.warning("%s is empty; using the built-in development value", name)

    app = FastAPI(
        title="ClaimShield Core",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
    )
    app.state.container = container or build_container(settings)
    app.add_exception_handler(DomainError, _domain_error_handler)

    @app.middleware("http")
    async def _contracts_header(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-ClaimShield-Contracts-Version"] = CONTRACTS_VERSION
        return response

    for module in (health, cases, visits, claims, disputes, human, voice, relay_webhook, tools):
        app.include_router(module.router)
    return app
