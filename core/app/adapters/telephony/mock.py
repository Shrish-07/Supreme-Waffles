"""TELEPHONY_MODE=mock: pretends to dial; records what it would have done."""

from .base import DialResult, TelephonyAdapter, ensure_allowed


class MockTelephony(TelephonyAdapter):
    mode = "mock"

    def __init__(self, allowed: frozenset[str]) -> None:
        self._allowed = allowed
        self.dialed: list[str] = []
        self.hung_up: list[str] = []

    def dial(self, call_id: str, to_number: str) -> DialResult:
        ensure_allowed(to_number, self._allowed)
        self.dialed.append(call_id)
        return DialResult(provider_call_ref=f"MOCK-CA-{call_id[-8:]}", status="initiated")

    def hangup(self, provider_call_ref: str) -> None:
        self.hung_up.append(provider_call_ref)
