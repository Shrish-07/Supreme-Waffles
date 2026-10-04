"""Telephony adapters: mock | live (Telnyx Call Control -> xAI SIP)."""

from ...config import Settings
from .base import TelephonyAdapter


def build_telephony(settings: Settings) -> TelephonyAdapter:
    if settings.telephony_mode == "live":
        from .telnyx_sip import TelnyxSipTelephony

        return TelnyxSipTelephony(
            api_key=settings.telnyx_api_key,
            connection_id=settings.telnyx_connection_id,
            from_number=settings.telnyx_phone_number,
            sip_uri=settings.xai_sip_uri,
            public_base_url=settings.public_base_url,
            allowed=settings.allowed_numbers(),
        )
    from .mock import MockTelephony

    return MockTelephony(settings.allowed_numbers())
