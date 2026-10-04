"""TELEPHONY_MODE=live: Twilio outbound call bridged to xAI's SIP endpoint.

# VERIFY against current docs: the SIP URI format xAI expects, how a per-call id
# is passed (SIP header vs URI parameter), and whether xAI initiates a webhook /
# sideband WebSocket for SIP calls. Twilio's Calls REST resource
# (POST /2010-04-01/Accounts/{sid}/Calls.json with To, From, Twiml) is stable.

Flow: Core -> Twilio REST "create call" to the insurer (allowlisted) with TwiML
that <Dial><Sip>s the xAI SIP URI. Media: insurer <-> Twilio <-> xAI. Core keeps
the control WebSocket (adapters/xai_voice/live.py).

TODO(live, Member 1): Twilio status callbacks are form-encoded and signed with
X-Twilio-Signature. Add a dedicated route that validates that signature and
translates to VoiceEventIn, instead of pointing Twilio at /voice/{id}/event.
"""

from xml.sax.saxutils import escape

import httpx

from .base import DialResult, TelephonyAdapter, ensure_allowed

TWILIO_API = "https://api.twilio.com/2010-04-01"


class TwilioSipTelephony(TelephonyAdapter):
    mode = "live"

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        sip_uri: str,
        allowed: frozenset[str],
        http: httpx.Client | None = None,
    ) -> None:
        self._sid = account_sid
        self._auth = (account_sid, auth_token)
        self._from = from_number
        self._sip_uri = sip_uri
        self._allowed = allowed
        self._http = http or httpx.Client(timeout=10.0)

    def twiml(self, call_id: str) -> str:
        """TwiML bridging the answered call to xAI. VERIFY the call-id parameter name."""
        target = f"{self._sip_uri}?X-ClaimShield-Call-Id={call_id}"
        return f"<Response><Dial><Sip>{escape(target)}</Sip></Dial></Response>"

    def dial(self, call_id: str, to_number: str) -> DialResult:
        ensure_allowed(to_number, self._allowed)
        response = self._http.post(
            f"{TWILIO_API}/Accounts/{self._sid}/Calls.json",
            data={"To": to_number, "From": self._from, "Twiml": self.twiml(call_id)},
            auth=self._auth,
        )
        response.raise_for_status()
        body = response.json()
        return DialResult(provider_call_ref=body["sid"], status=body.get("status", "queued"))

    def hangup(self, provider_call_ref: str) -> None:
        self._http.post(
            f"{TWILIO_API}/Accounts/{self._sid}/Calls/{provider_call_ref}.json",
            data={"Status": "completed"},
            auth=self._auth,
        ).raise_for_status()
