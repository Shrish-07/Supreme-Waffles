"""TELEPHONY_MODE=live: Telnyx Call Control bridged to xAI's SIP endpoint.

Flow (mapped from Telnyx Voice API v2 docs; VERIFY before going live):

1. ``dial()`` POSTs /v2/calls ("Dial") with the Voice API connection, the
   allowlisted insurer number as ``to`` (E.164), TELNYX_PHONE_NUMBER as
   ``from``, ``client_state=base64(call_id)`` (Telnyx echoes it on every
   webhook event, which is how we correlate) and
   ``webhook_url={PUBLIC_BASE_URL}/voice/telnyx/webhook``.
   Expected webhooks: ``call.initiated``, ``call.answered`` | ``call.hangup``.
2. When the insurer answers, the webhook route
   (``services/telnyx_service.process_telnyx_event``) calls
   ``transfer_to_sip()``: POST /v2/calls/{call_control_id}/actions/transfer
   with ``to=<XAI_SIP_URI>`` (a sip: URI) and the same client_state. Expected
   webhooks: ``call.initiated`` (the xAI leg), ``call.bridged``, then
   ``call.answered`` | ``call.hangup`` -- the insurer leg is bridged to xAI.
   Telnyx has no TwiML equivalent: the bridge is imperative, so the live path
   cannot work without the webhook route and PUBLIC_BASE_URL.
3. Media flows insurer <-> Telnyx <-> xAI over SIP; audio never touches Core.
   Core keeps the control WebSocket (adapters/xai_voice/live.py).
4. ``hangup()`` POSTs /v2/calls/{call_control_id}/actions/hangup.

# VERIFY against current docs:
# - custom SIP ``headers`` on transfer when ``to`` is a sip: URI (used to pass
#   the call id to xAI);
# - the SIP URI format xAI expects (and whether its SIP endpoint exists);
# - how xAI attaches the realtime session of this SIP call (call-id header?).
Webhook authentication does NOT use the service bearer tokens: Telnyx signs
every event with the account's Ed25519 key pair (headers ``Telnyx-Timestamp``
and ``Telnyx-Signature-Ed25519``; public key from Mission Control -> API keys
-> Public key, env var TELNYX_PUBLIC_KEY). See webhook_signature.py.

Check credentials without placing a call (read-only, no credits needed):
    cd core && python -m app.adapters.telephony.telnyx_sip --check
"""

import argparse
import base64
import logging

import httpx

from ...errors import UpstreamError
from .base import DialResult, TelephonyAdapter, ensure_allowed

log = logging.getLogger("claimshield.telephony.telnyx")

TELNYX_API = "https://api.telnyx.com/v2"
WEBHOOK_PATH = "/voice/telnyx/webhook"
CALL_ID_SIP_HEADER = "X-ClaimShield-Call-Id"  # VERIFY: custom SIP header on transfer


def to_client_state(call_id: str) -> str:
    """``client_state`` value Telnyx echoes back on every webhook event."""
    return base64.b64encode(call_id.encode()).decode()


def from_client_state(client_state: str | None) -> str | None:
    """Call id from a webhook's ``client_state`` (None when absent/malformed)."""
    if not client_state:
        return None
    try:
        return base64.b64decode(client_state.encode(), validate=True).decode()
    except (ValueError, UnicodeDecodeError):
        return None


class TelnyxSipTelephony(TelephonyAdapter):
    """Telnyx Call Control: places/ends the PSTN leg; the webhook route bridges."""

    mode = "live"

    def __init__(
        self,
        *,
        api_key: str,
        connection_id: str,
        from_number: str,
        sip_uri: str,
        public_base_url: str,
        allowed: frozenset[str],
        http: httpx.Client | None = None,
    ) -> None:
        self._connection_id = connection_id
        self._from = from_number
        self._sip_uri = sip_uri
        self._webhook_url = public_base_url.rstrip("/") + WEBHOOK_PATH
        self._allowed = allowed
        self._http = http or httpx.Client(timeout=10.0)
        self._http.headers.update({"Authorization": f"Bearer {api_key}"})


    def _post(self, path: str, body: dict) -> dict:
        try:
            response = self._http.post(f"{TELNYX_API}{path}", json=body)
        except httpx.TransportError as exc:
            raise UpstreamError(
                "Telnyx is unavailable right now.", code="telnyx_unavailable"
            ) from exc
        if response.status_code >= 400:
            raise UpstreamError(
                f"Telnyx rejected the request (HTTP {response.status_code}).",
                code="telnyx_request_failed",
            )
        result = response.json()
        return result if isinstance(result, dict) else {}

    def dial(self, call_id: str, to_number: str) -> DialResult:
        ensure_allowed(to_number, self._allowed)
        body = self._post(
            "/calls",
            {
                "connection_id": self._connection_id,
                "from": self._from,
                "to": to_number,
                # Echoed back on every webhook event; how the route finds the Core call.
                "client_state": to_client_state(call_id),
                "webhook_url": self._webhook_url,
                "webhook_url_method": "POST",
                "timeout_secs": 60,
            },
        )
        data = body.get("data") or {}
        try:
            return DialResult(
                provider_call_ref=str(data["call_control_id"]),
                status=str(data.get("status") or "queued"),
            )
        except KeyError as exc:
            raise UpstreamError(
                "Telnyx returned an unexpected dial response.", code="telnyx_bad_response"
            ) from exc

    def transfer_to_sip(self, provider_call_ref: str, call_id: str) -> None:
        """Bridge the answered insurer leg to xAI's SIP endpoint.

        Called by services/telnyx_service on the ``call.answered`` webhook of
        the insurer leg. Telnyx's equivalent of Twilio's TwiML <Dial><Sip>.
        """
        self._post(
            f"/calls/{provider_call_ref}/actions/transfer",
            {
                "to": self._sip_uri,
                "client_state": to_client_state(call_id),
                # VERIFY: custom SIP headers on transfer to a sip: URI.
                "headers": [{"name": CALL_ID_SIP_HEADER, "value": call_id}],
            },
        )

    def hangup(self, provider_call_ref: str) -> None:
        self._post(f"/calls/{provider_call_ref}/actions/hangup", {})

    def active_calls(self) -> dict:
        """Read-only account check used by ``--check`` (no call is placed)."""
        try:
            response = self._http.get(
                f"{TELNYX_API}/connections/{self._connection_id}/active_calls"
            )
        except httpx.TransportError as exc:
            raise UpstreamError("Telnyx is unreachable.", code="telnyx_unavailable") from exc
        return {"status": response.status_code, "body": response.json()}


def _check() -> None:
    """Read-only credential check: list active calls on the connection."""
    from ...config import get_settings

    settings = get_settings()
    if not (settings.telnyx_api_key and settings.telnyx_connection_id):
        raise SystemExit(
            "Set TELNYX_API_KEY and TELNYX_CONNECTION_ID in .env first "
            "(docs/SETUP_KEYS.md#5-telephony)."
        )
    adapter = TelnyxSipTelephony(
        api_key=settings.telnyx_api_key,
        connection_id=settings.telnyx_connection_id,
        from_number=settings.telnyx_phone_number,
        sip_uri=settings.xai_sip_uri,
        public_base_url=settings.public_base_url or "http://localhost:8080",
        allowed=settings.allowed_numbers(),
    )
    print(adapter.active_calls())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Telnyx credential check")
    parser.add_argument(
        "--check",
        action="store_true",
        help="list active calls on the connection (read-only, places no call)",
    )
    if parser.parse_args().check:
        _check()
