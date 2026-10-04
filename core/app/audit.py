"""Audit trail and PHI-safe structured logging.

- :func:`redact` / :func:`redact_text` mask PHI-like values (SSNs, phones,
  emails, member ids, DOB phrases) and drop known PHI keys.
- :class:`JsonFormatter` + :class:`PhiRedactingFilter` apply redaction to every
  log record, so a stray f-string cannot leak a transcript line into logs.
- :func:`record_audit` writes an append-only ``audit_events`` row.

Audit details should contain ids, types and counts -- never free text.
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from claimshield_contracts.ids import new_id

from .models_db import AuditRow

REDACTED = "[REDACTED]"

PHI_KEYS = frozenset(
    {
        "address",
        "authorization_text",
        "consent_text",
        "display_name",
        "dob",
        "letter_text",
        "member_ref",
        "name",
        "notes",
        "patient_display_name",
        "payer_phone",
        "phone",
        "prompt",
        "quote",
        "segments",
        "ssn",
        "text",
        "transcript",
    }
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<!\w)\d{3}-\d{2}-\d{4}(?!\w)"), "[REDACTED-SSN]"),
    (re.compile(r"(?<!\w)\d{9}(?!\w)"), "[REDACTED-ID]"),
    (
        re.compile(r"(?<![\w+])(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)"),
        "[REDACTED-PHONE]",
    ),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[REDACTED-EMAIL]"),
    (re.compile(r"\b(?:SYN-)?MBR-\d+\b"), "[REDACTED-MEMBER]"),
    (re.compile(r"(?i)\b(dob|date of birth)\b\s*[:=]?\s*[\w/.-]+"), r"\1 [REDACTED]"),
)


def redact_text(value: str) -> str:
    """Mask PHI-like substrings in free text."""
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def redact(value: Any) -> Any:
    """Recursively redact a JSON-like structure (dict keys in PHI_KEYS are dropped)."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if str(key).lower() in PHI_KEYS else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def mask_phone(number: str | None) -> str | None:
    """``+15555550100`` -> ``+1•••••••0100``."""
    if not number:
        return None
    return f"{number[:2]}{'•' * max(len(number) - 6, 0)}{number[-4:]}"


def mask_member_ref(member_ref: str | None) -> str | None:
    if not member_ref:
        return None
    return f"•••{member_ref[-3:]}"


class PhiRedactingFilter(logging.Filter):
    """Redacts the rendered message and structured ``fields`` of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = ()
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            record.fields = redact(fields)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, msg, plus redacted ``fields``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install JSON + redaction on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in root.handlers:
        if getattr(handler, "_claimshield", False):
            return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(PhiRedactingFilter())
    handler._claimshield = True  # type: ignore[attr-defined]
    root.addHandler(handler)


_log = logging.getLogger("claimshield.audit")


def record_audit(
    db: Session,
    *,
    actor: str,
    action: str,
    case_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditRow:
    """Append a redacted audit event in the caller's transaction."""
    row = AuditRow(
        id=new_id("audit"),
        actor=actor,
        action=action,
        case_id=case_id,
        resource_type=resource_type,
        resource_id=resource_id,
        details=redact(details or {}),
    )
    db.add(row)
    _log.info(
        action,
        extra={"fields": {"actor": actor, "case_id": case_id, "resource_id": resource_id}},
    )
    return row
