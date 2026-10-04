"""Core settings. One ``*_MODE`` flag per integration; live modes fail fast.

Values come from the environment and the repo-root ``.env`` (see .env.example).
Empty values are ignored, so a blank ``.env`` means "all defaults, all mock".
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from claimshield_contracts.enums import ServiceName

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_PATH = REPO_ROOT / ".data" / "claimshield.db"

# Well-known development credentials. Used only when APP_ENV is dev/test and
# the real variable is empty. Mirrored in relay/src/config.ts and fetch/agents/settings.py.
DEV_SERVICE_TOKENS: dict[ServiceName, str] = {
    ServiceName.RELAY: "dev-relay-token",
    ServiceName.FETCH: "dev-fetch-token",
    ServiceName.VOICE: "dev-voice-token",
}
DEV_CORE_WEBHOOK_SECRET = "dev-core-webhook-secret"


class ConfigError(RuntimeError):
    """Raised at startup when a live integration is missing required settings."""


class Settings(BaseSettings):
    """All Core configuration. Field names map 1:1 to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # --- core
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    database_url: str = ""
    core_base_url: str = "http://localhost:8080"
    relay_service_token: str = ""
    fetch_service_token: str = ""
    voice_service_token: str = ""
    core_webhook_secret: str = ""
    confirmation_threshold: float = 0.9
    notify_mode: Literal["outbox", "relay_service"] = "outbox"
    relay_service_url: str = ""

    # --- voice + telephony
    xai_voice_mode: Literal["mock", "live"] = "mock"
    xai_api_key: str = ""
    xai_realtime_url: str = "wss://api.x.ai/v1/realtime"
    xai_voice_model: str = ""
    xai_voice_name: str = ""
    telephony_mode: Literal["mock", "live"] = "mock"
    telnyx_api_key: str = ""
    telnyx_connection_id: str = ""
    telnyx_phone_number: str = ""
    telnyx_public_key: str = ""
    public_base_url: str = ""
    xai_sip_uri: str = ""
    telephony_allowed_numbers: str = "+15555550100"

    # --- fetch
    fetch_mode: Literal["local_direct", "agentverse"] = "local_direct"
    orchestrator_url: str = "http://localhost:8000"

    # --- finchnode + intelligence
    finchnode_mode: Literal["mock", "demo", "sandbox"] = "mock"
    finchnode_api_key: str = ""
    finchnode_base_url: str = ""
    extraction_mode: Literal["rules", "ollama"] = "rules"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:1.7b"

    # ------------------------------------------------------------------ derived

    @property
    def resolved_database_url(self) -> str:
        """DATABASE_URL, or a local SQLite file when it is empty."""
        return self.database_url or f"sqlite:///{DEFAULT_SQLITE_PATH}"

    @property
    def allows_dev_defaults(self) -> bool:
        return self.app_env in ("dev", "test")

    def service_tokens(self) -> dict[ServiceName, str]:
        """Bearer token per caller, with dev fallbacks outside prod."""
        configured = {
            ServiceName.RELAY: self.relay_service_token,
            ServiceName.FETCH: self.fetch_service_token,
            ServiceName.VOICE: self.voice_service_token,
        }
        if self.allows_dev_defaults:
            return {name: value or DEV_SERVICE_TOKENS[name] for name, value in configured.items()}
        return configured

    def webhook_secret(self) -> str:
        """HMAC secret for POST /relay/webhook."""
        if self.core_webhook_secret:
            return self.core_webhook_secret
        return DEV_CORE_WEBHOOK_SECRET if self.allows_dev_defaults else ""

    def allowed_numbers(self) -> frozenset[str]:
        return frozenset(n.strip() for n in self.telephony_allowed_numbers.split(",") if n.strip())

    def modes(self) -> dict[str, str]:
        return {
            "FINCHNODE_MODE": self.finchnode_mode,
            "NOTIFY_MODE": self.notify_mode,
            "XAI_VOICE_MODE": self.xai_voice_mode,
            "TELEPHONY_MODE": self.telephony_mode,
            "EXTRACTION_MODE": self.extraction_mode,
            "FETCH_MODE": self.fetch_mode,
        }

    def using_dev_credentials(self) -> list[str]:
        """Names of credentials currently falling back to dev defaults."""
        names = [
            f"{name.value.upper()}_SERVICE_TOKEN"
            for name, value in {
                ServiceName.RELAY: self.relay_service_token,
                ServiceName.FETCH: self.fetch_service_token,
                ServiceName.VOICE: self.voice_service_token,
            }.items()
            if not value
        ]
        if not self.core_webhook_secret:
            names.append("CORE_WEBHOOK_SECRET")
        return names if self.allows_dev_defaults else []

    def missing_live_settings(self) -> list[str]:
        """Human-readable list of settings required by the selected live modes."""
        missing: list[str] = []

        def need(flag: str, *pairs: tuple[str, str]) -> None:
            missing.extend(f"{flag} requires {name}" for name, value in pairs if not value)

        # demo is the public fake API and has a built-in base URL. No key.
        if self.finchnode_mode == "sandbox":
            need(
                "FINCHNODE_MODE=sandbox",
                ("FINCHNODE_BASE_URL", self.finchnode_base_url),
                ("FINCHNODE_API_KEY", self.finchnode_api_key),
            )
        if self.notify_mode == "relay_service":
            need("NOTIFY_MODE=relay_service", ("RELAY_SERVICE_URL", self.relay_service_url))
        if self.xai_voice_mode == "live":
            need(
                "XAI_VOICE_MODE=live",
                ("XAI_API_KEY", self.xai_api_key),
                ("XAI_REALTIME_URL", self.xai_realtime_url),
            )
        if self.telephony_mode == "live":
            need(
                "TELEPHONY_MODE=live",
                ("TELNYX_API_KEY", self.telnyx_api_key),
                ("TELNYX_CONNECTION_ID", self.telnyx_connection_id),
                ("TELNYX_PHONE_NUMBER", self.telnyx_phone_number),
                ("TELNYX_PUBLIC_KEY", self.telnyx_public_key),
                ("PUBLIC_BASE_URL", self.public_base_url),
                ("XAI_SIP_URI", self.xai_sip_uri),
            )
        if self.fetch_mode == "agentverse":
            need("FETCH_MODE=agentverse", ("ORCHESTRATOR_URL", self.orchestrator_url))
        if self.extraction_mode == "ollama":
            need(
                "EXTRACTION_MODE=ollama",
                ("OLLAMA_URL", self.ollama_url),
                ("OLLAMA_MODEL", self.ollama_model),
            )
        if self.app_env == "prod":
            need(
                "APP_ENV=prod",
                ("RELAY_SERVICE_TOKEN", self.relay_service_token),
                ("FETCH_SERVICE_TOKEN", self.fetch_service_token),
                ("VOICE_SERVICE_TOKEN", self.voice_service_token),
                ("CORE_WEBHOOK_SECRET", self.core_webhook_secret),
            )
        return missing

    def validate_for_startup(self) -> None:
        """Raise ConfigError naming every missing variable for the selected modes."""
        missing = self.missing_live_settings()
        if missing:
            lines = "\n".join(f"  - {item}" for item in missing)
            raise ConfigError(
                "ClaimShield Core cannot start: live integrations are missing settings.\n"
                f"{lines}\n"
                "Fill these in .env (see docs/SETUP_KEYS.md) or switch the flag back to mock."
            )


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings (tests build their own Settings instead)."""
    return Settings()
