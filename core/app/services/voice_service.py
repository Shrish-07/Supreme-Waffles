"""Core's voice call manager. Owns insurer calls end to end (Fetch only requests them).

``VoiceCallManager`` implements the adapter's ``VoiceEventHandler``:

- every insurer utterance goes through ``security_policy.detect_sensitive_request``
  (in code, independent of the model) -> HumanIntervention + HUMAN_REQUIRED + pause;
- every agent utterance goes through ``check_agent_utterance``;
- model tool calls go through ``ToolCallDispatcher`` with case/call ids injected.

Each callback opens its own DB transaction, so the same code serves the
synchronous mock session and the threaded live session.
"""

import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from claimshield_contracts.api import VoiceEventIn, VoiceEventResult
from claimshield_contracts.enums import (
    CallSpeaker,
    CallStatus,
    CaseState,
    DisputeStatus,
    HumanReason,
    InterventionStatus,
    ServiceName,
    TurnAction,
    VoiceEventType,
)
from claimshield_contracts.ids import new_id
from claimshield_contracts.models import CallSession, CallTurn, HumanIntervention, InsurerResponse
from claimshield_contracts.tool_api import (
    TOOL_SPECS,
    CoreToolError,
    RecordInsurerResponseRequest,
    ToolName,
)

from .. import mappers
from ..adapters.xai_voice.base import GuardrailVerdict, TurnDecision, VoiceSessionConfig
from ..adapters.xai_voice.dispatcher import ToolCallDispatcher
from ..adapters.xai_voice.prompts import VOICE_TOOL_SCHEMAS, build_instructions
from ..audit import mask_phone, record_audit
from ..auth import identity_for
from ..db import utcnow
from ..errors import Conflict, DomainError, InvalidInput, NotFound, SafetyBlocked, UpstreamError
from ..models_db import CallRow, CaseRow, InterventionRow
from . import notification_service
from .case_service import get_case_row, latest_dispute, transition
from .claim_service import claim_lines, get_case_claim_row
from .dispute_service import dispute_case_view
from .human_service import HANDOFF_OPTIONS, create_intervention, open_call_interventions
from .security_policy import (
    HOLD_LINE,
    SAFE_FALLBACK_LINE,
    AllowedFacts,
    check_agent_utterance,
    detect_sensitive_request,
    reject_sensitive_notes,
)

if TYPE_CHECKING:
    from ..container import Container

SENSITIVE_PROMPTS = {
    "ssn": "The representative asked for your Social Security number to verify your identity.",
    "dob_verification": "The representative asked to verify your date of birth.",
    "pin": "The representative asked for a PIN.",
    "password": "The representative asked for a password.",
    "auth_code": "The representative asked for a verification code.",
    "payment_card": "The representative asked for card details.",
    "bank_account": "The representative asked for bank account details.",
}
LIVE_CALL_STATUSES = (CallStatus.IN_PROGRESS.value, CallStatus.PAUSED_FOR_HUMAN.value)
HANDOFF_REASONS = frozenset(HumanReason) - {HumanReason.PATIENT_CONFIRMATION}
log = logging.getLogger("claimshield.voice")


def _get_call(db: Session, call_id: str) -> CallRow:
    call = db.get(CallRow, call_id)
    if call is None:
        raise NotFound(f"Call {call_id} not found")
    return call


def _append_turn(
    call: CallRow, speaker: CallSpeaker, text: str, flags: list[str] | None = None
) -> None:
    turn = CallTurn(speaker=speaker, text=text, at=utcnow(), flags=flags or [])
    call.turns = [*call.turns, mappers.dump(turn)]


def record_insurer_response(
    db: Session, req: RecordInsurerResponseRequest, *, actor: str
) -> InsurerResponse:
    """Persist what the insurer agreed to. The case resolves when the call ends."""
    call = _get_call(db, req.call_id)
    if call.case_id != req.case_id:
        raise InvalidInput("call_id does not belong to case_id")
    if call.status not in LIVE_CALL_STATUSES:
        raise Conflict("The call is not active.", code="call_not_active")
    claim = get_case_claim_row(db, req.case_id)
    line_ids = {line.id for line in claim_lines(db, claim.id)} if claim else set()
    referenced = set(req.removed_line_ids) | set(req.itemization_requested_line_ids)
    if not referenced <= line_ids:
        raise InvalidInput("Response references claim lines that are not on this claim")
    for text in (req.notes, req.representative_name, req.reference_number):
        reject_sensitive_notes(text)
    response = InsurerResponse(
        outcome=req.outcome,
        reference_number=req.reference_number,
        removed_line_ids=req.removed_line_ids,
        itemization_requested_line_ids=req.itemization_requested_line_ids,
        representative_name=req.representative_name,
        notes=req.notes,
        recorded_at=utcnow(),
    )
    call.insurer_response = mappers.dump(response)
    _append_turn(call, CallSpeaker.SYSTEM, f"Insurer response recorded: {req.outcome.value}")
    record_audit(
        db,
        actor=actor,
        action="voice.insurer_response_recorded",
        case_id=req.case_id,
        resource_type="call",
        resource_id=call.id,
        details={"outcome": req.outcome.value, "removed": len(req.removed_line_ids)},
    )
    return response


class VoiceToolsBackend:
    """Runs voice tool calls through ToolService with the VOICE identity."""

    def __init__(self, container: "Container") -> None:
        from .tool_service import ToolService

        self._tools = ToolService(container, identity_for(ServiceName.VOICE))

    def call(self, tool: ToolName, payload: dict[str, Any]) -> dict[str, Any]:
        request_model, _ = TOOL_SPECS[tool]
        try:
            request = request_model.model_validate(payload)
            result = getattr(self._tools, tool.value)(request)
        except ValidationError as exc:
            raise CoreToolError(422, "invalid_arguments", str(exc.errors()[:3])) from exc
        except DomainError as exc:
            raise CoreToolError(exc.status_code, exc.code, exc.message) from exc
        return result.model_dump(mode="json")


class VoiceCallManager:
    """Starts, supervises, pauses, resumes and closes insurer calls."""

    def __init__(self, container: "Container") -> None:
        self._c = container
        self._backend = VoiceToolsBackend(container)

    # ------------------------------------------------------------------ lifecycle

    def start_call(self, case_id: str, *, actor: str) -> CallSession:
        """Guarded start: APPROVED dispute + patient authorization + allowlisted number."""
        with self._c.session_scope() as db:
            case = get_case_row(db, case_id)
            dispute = latest_dispute(db, case_id)
            if dispute is None:
                raise Conflict("No dispute exists for this case.", code="no_dispute")
            number = case.payer_phone
            if not number:
                raise InvalidInput("No insurer phone number is on file for this case.")
            if number not in self._c.settings.allowed_numbers():
                raise SafetyBlocked(
                    "Insurer number is not in TELEPHONY_ALLOWED_NUMBERS; refusing to dial.",
                    code="number_not_allowed",
                )
            call = CallRow(
                id=new_id("call"),
                case_id=case.id,
                dispute_id=dispute.id,
                status=CallStatus.INITIATING.value,
                to_number_masked=mask_phone(number),
                turns=[],
            )
            db.add(call)
            transition(db, case, CaseState.CALL_IN_PROGRESS, actor=actor)
            record_audit(
                db, actor=actor, action="voice.call_started", case_id=case.id, resource_id=call.id
            )
            call_id = call.id

        try:
            dial = self._c.telephony.dial(call_id, number)
        except DomainError:
            self._end(call_id, "dial refused", failed=True)
            raise
        except Exception as exc:
            self._end(call_id, f"dial failed: {type(exc).__name__}", failed=True)
            raise UpstreamError("Telephony provider failed to place the call.") from exc

        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            call.provider_call_ref = dial.provider_call_ref
            call.status = CallStatus.IN_PROGRESS.value
            view = dispute_case_view(db, case_id)
        config = VoiceSessionConfig(
            call_id=call_id,
            case_id=case_id,
            instructions=build_instructions(view),
            tools=VOICE_TOOL_SCHEMAS,
            case_context=view,
            model=self._c.settings.xai_voice_model or None,
            voice=self._c.settings.xai_voice_name or None,
        )
        try:
            self._c.voice.start_session(config, self)
        except Exception as exc:
            log.exception("voice session failed to start", extra={"fields": {"call_id": call_id}})
            self._quiet_hangup(dial.provider_call_ref)
            self._end(call_id, f"voice session failed: {type(exc).__name__}", failed=True)
            raise UpstreamError("The voice session could not be started.") from exc
        return self.get_call(call_id)

    def get_call(self, call_id: str) -> CallSession:
        with self._c.session_scope() as db:
            return mappers.call_to_model(_get_call(db, call_id))

    def resume_after_human(self, call_id: str, *, actor: str) -> None:
        """Called after a call-linked intervention resolves with DONE."""
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            if (
                open_call_interventions(db, call_id)
                or call.status != CallStatus.PAUSED_FOR_HUMAN.value
            ):
                return
            case = get_case_row(db, call.case_id)
            call.status = CallStatus.IN_PROGRESS.value
            call.open_intervention_id = None
            _append_turn(
                call, CallSpeaker.HUMAN, "Member verified directly with the representative."
            )
            transition(db, case, CaseState.CALL_IN_PROGRESS, actor=actor)
        try:
            self._c.voice.resume_session(call_id, self)
        except Exception as exc:
            log.exception("voice session could not resume", extra={"fields": {"call_id": call_id}})
            self._end(call_id, f"voice session lost: {type(exc).__name__}", failed=True)
            raise UpstreamError("The call could not be resumed and was closed.") from exc

    def cancel_after_human(self, call_id: str, *, actor: str) -> None:
        """Called after a call-linked intervention resolves with CANCEL."""
        with self._c.session_scope() as db:
            ref = _get_call(db, call_id).provider_call_ref
        try:
            self._c.voice.end_session(call_id, "cancelled by patient")
        except Exception:
            log.exception(
                "voice session did not end cleanly", extra={"fields": {"call_id": call_id}}
            )
        finally:
            self._quiet_hangup(ref)
            self.on_call_ended(call_id, "cancelled by patient")

    def _quiet_hangup(self, provider_call_ref: str | None) -> None:
        """Best-effort hangup; failures are logged, never left to strand a case."""
        if not provider_call_ref:
            return
        try:
            self._c.telephony.hangup(provider_call_ref)
        except Exception:
            log.exception("telephony hangup failed", extra={"fields": {"ref": provider_call_ref}})

    def request_handoff(
        self, call_id: str, *, reason: HumanReason, detail: str, actor: str
    ) -> HumanIntervention:
        """Pause an active call for a human (model tool call or explicit API request)."""
        if reason not in HANDOFF_REASONS:
            reason = HumanReason.CALL_ESCALATION  # pausing is always the safe default
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            if call.status == CallStatus.PAUSED_FOR_HUMAN.value and call.open_intervention_id:
                existing = open_call_interventions(db, call_id)
                if existing:
                    return mappers.intervention_to_model(existing[0])
            if call.status != CallStatus.IN_PROGRESS.value:
                raise Conflict("The call is not in progress.", code="call_not_active")
            row = self._pause_for_human(db, call, reason=reason, prompt=detail, actor=actor)
            result = mappers.intervention_to_model(row)
        self._c.voice.pause_session(call_id)
        return result

    def handle_event(self, call_id: str, event: VoiceEventIn, *, actor: str) -> VoiceEventResult:
        """External events: insurer transcripts or status callbacks from bridges."""
        decision = TurnDecision(action=TurnAction.CONTINUE)
        if event.type == VoiceEventType.INSURER_UTTERANCE:
            decision = self.on_insurer_utterance(call_id, event.text or "")
            if decision.action == TurnAction.PAUSE_FOR_HUMAN:
                self._c.voice.pause_session(call_id)
        elif event.type == VoiceEventType.CALL_STATUS:
            with self._c.session_scope() as db:
                _append_turn(_get_call(db, call_id), CallSpeaker.SYSTEM, f"status: {event.status}")
        elif event.type == VoiceEventType.CALL_ENDED:
            self.on_call_ended(call_id, event.status or "ended by provider")
            decision = TurnDecision(action=TurnAction.END)
        return VoiceEventResult(
            action=decision.action,
            call=self.get_call(call_id),
            intervention_id=decision.intervention_id,
        )

    # ------------------------------------------------------------------ VoiceEventHandler

    def on_insurer_utterance(self, call_id: str, text: str) -> TurnDecision:
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            sensitive = detect_sensitive_request(text)
            flags = [f"sensitive:{sensitive.category}"] if sensitive else []
            _append_turn(call, CallSpeaker.INSURER, text, flags)
            if sensitive is None or call.status != CallStatus.IN_PROGRESS.value:
                return TurnDecision(action=TurnAction.CONTINUE)
            row = self._pause_for_human(
                db,
                call,
                reason=sensitive.reason,
                prompt=SENSITIVE_PROMPTS.get(sensitive.category, "The representative needs you."),
                actor="core:security_policy",
            )
            return TurnDecision(
                action=TurnAction.PAUSE_FOR_HUMAN,
                say=HOLD_LINE,
                intervention_id=row.id,
            )

    def on_agent_utterance(self, call_id: str, text: str) -> GuardrailVerdict:
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            check = check_agent_utterance(
                text, AllowedFacts.from_view(dispute_case_view(db, call.case_id))
            )
            spoken = text if check.allowed else SAFE_FALLBACK_LINE
            _append_turn(
                call, CallSpeaker.AGENT, spoken, [f"blocked:{v}" for v in check.violations]
            )
            if not check.allowed:
                record_audit(
                    db,
                    actor="core:security_policy",
                    action="voice.guardrail_blocked",
                    case_id=call.case_id,
                    resource_id=call.id,
                    details={"violations": list(check.violations)},
                )
            return GuardrailVerdict(allowed=check.allowed, text=spoken, violations=check.violations)

    def on_tool_call(self, call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            case_id = call.case_id
            _append_turn(call, CallSpeaker.SYSTEM, f"tool:{name}")
        dispatcher = ToolCallDispatcher(self._backend, case_id=case_id, call_id=call_id)
        return dispatcher.dispatch(name, arguments)

    def on_call_ended(self, call_id: str, reason: str) -> None:
        self._end(call_id, reason, failed=False)

    # ------------------------------------------------------------------ internals

    def _pause_for_human(
        self, db: Session, call: CallRow, *, reason: HumanReason, prompt: str, actor: str
    ) -> InterventionRow:
        case = get_case_row(db, call.case_id)
        row = create_intervention(
            db,
            case_id=case.id,
            reason=reason,
            prompt=prompt,
            options=HANDOFF_OPTIONS,
            actor=actor,
            call_id=call.id,
        )
        call.status = CallStatus.PAUSED_FOR_HUMAN.value
        call.open_intervention_id = row.id
        _append_turn(call, CallSpeaker.SYSTEM, f"Paused for human: {reason.value}")
        transition(db, case, CaseState.HUMAN_REQUIRED, actor=actor)
        notification_service.notify_handoff(db, self._c, case, row)
        return row

    def _end(self, call_id: str, reason: str, *, failed: bool) -> None:
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            if call.ended_at is not None:
                return
            case = get_case_row(db, call.case_id)
            dispute = latest_dispute(db, case.id)
            call.ended_at = utcnow()
            call.end_reason = reason
            for row in open_call_interventions(db, call_id):
                row.status = InterventionStatus.CANCELLED.value
            resolved = bool(call.insurer_response) and not failed
            call.status = (CallStatus.COMPLETED if resolved else CallStatus.FAILED).value
            if dispute is not None:
                dispute.status = (
                    DisputeStatus.RESOLVED if resolved else DisputeStatus.FAILED
                ).value
            target = CaseState.RESOLVED if resolved else CaseState.FAILED
            transition(db, case, target, actor="core:voice", reason=reason)
            record_audit(
                db,
                actor="core:voice",
                action="voice.call_ended",
                case_id=case.id,
                resource_id=call.id,
                details={"reason": reason, "status": call.status},
            )
            notification_service.notify_status(
                db, self._c, case, _final_message(case, call), final=True
            )


def _final_message(case: CaseRow, call: CallRow) -> str:
    payer = case.payer_name or "your insurer"
    if not call.insurer_response:
        return f"The call with {payer} ended without a resolution ({call.end_reason})."
    response = InsurerResponse.model_validate(call.insurer_response)
    removed = len(response.removed_line_ids)
    itemized = len(response.itemization_requested_line_ids)
    parts = [f"Resolved with {payer}: {response.outcome.value.replace('_', ' ')}."]
    if removed:
        parts.append(f"{removed} charge(s) will be removed.")
    if itemized:
        parts.append(f"Itemization requested for {itemized} charge(s).")
    if response.reference_number:
        parts.append(f"Reference {response.reference_number}.")
    return " ".join(parts)
