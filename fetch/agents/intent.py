"""Rule-based intent routing for chat messages (no LLM needed).

An LLM fallback can be added for UNKNOWN intents later; the rules cover the demo.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

# VERIFY against current docs: the real FinchNode patient-id format.
PATIENT_ID = re.compile(r"\bfn_pat_[A-Za-z0-9_]+\b")
CLAIM_ID = re.compile(r"\bCLM-[A-Za-z0-9-]+\b", re.IGNORECASE)


class IntentKind(StrEnum):
    GREETING = "greeting"
    REVIEW_BILL = "review_bill"
    ANSWER = "answer"
    APPROVE = "approve"
    REJECT = "reject"
    DONE = "done"
    CANCEL = "cancel"
    STATUS = "status"
    HELP = "help"
    RESTART = "restart"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Intent:
    kind: IntentKind
    answer: str | None = None
    patient_id: str | None = None
    claim_id: str | None = None


def _has(text: str, *phrases: str) -> bool:
    return any(re.search(rf"\b{re.escape(p)}\b", text) for p in phrases)


def parse_intent(text: str) -> Intent:
    """Map a chat message to an intent plus any ids it contains."""
    raw = text.strip()
    lowered = raw.lower().replace("’", "'")
    patient = PATIENT_ID.search(raw)
    claim = CLAIM_ID.search(raw)
    ids = {
        "patient_id": patient.group(0) if patient else None,
        "claim_id": claim.group(0).upper() if claim else None,
    }
    if lowered in ("restart", "start over", "reset"):
        return Intent(IntentKind.RESTART)
    if lowered in ("help", "?", "what can you do"):
        return Intent(IntentKind.HELP)
    if lowered in ("status", "update", "where are we"):
        return Intent(IntentKind.STATUS)
    if _has(lowered, "not sure", "unsure", "don't know", "dont know", "no idea"):
        return Intent(IntentKind.ANSWER, answer="NOT_SURE", **ids)
    if _has(lowered, "approve", "approved"):
        return Intent(IntentKind.APPROVE, **ids)
    if _has(lowered, "reject", "rejected", "decline"):
        return Intent(IntentKind.REJECT, **ids)
    if _has(lowered, "done", "verified", "finished"):
        return Intent(IntentKind.DONE, **ids)
    if _has(lowered, "cancel", "hang up"):
        return Intent(IntentKind.CANCEL, **ids)
    if re.match(r"^(yes|yeah|yep|y|sure|correct|i consent|i agree|ok|okay)\b", lowered):
        return Intent(IntentKind.ANSWER, answer="YES", **ids)
    if re.match(r"^(no|nope|n|i did not|i didn't|never)\b", lowered):
        return Intent(IntentKind.ANSWER, answer="NO", **ids)
    if ids["patient_id"] or ids["claim_id"] or _has(lowered, "review", "bill", "claim", "charge"):
        return Intent(IntentKind.REVIEW_BILL, **ids)
    if _has(lowered, "hi", "hello", "hey"):
        return Intent(IntentKind.GREETING)
    return Intent(IntentKind.UNKNOWN, **ids)
