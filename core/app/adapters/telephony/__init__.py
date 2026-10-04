"""Telephony adapters: mock | live (Twilio Elastic SIP -> xAI SIP)."""

from ...config import Settings
from .base import TelephonyAdapter


def build_telephony(settings: Settings) -> TelephonyAdapter:
    if settings.telephony_mode == "live":
        from .twilio_sip import TwilioSipTelephony

        return TwilioSipTelephony(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.twilio_from_number,
            sip_uri=settings.xai_sip_uri,
            allowed=settings.allowed_numbers(),
        )
    from .mock import MockTelephony

    return MockTelephony(settings.allowed_numbers())
