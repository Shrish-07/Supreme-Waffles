from fastapi import APIRouter, Depends, status

from claimshield_contracts.api import HandoffIn, VoiceEventIn, VoiceEventResult, VoiceStartIn
from claimshield_contracts.models import CallSession, HumanIntervention

from ..auth import ServiceIdentity, require_scopes
from ..container import Container
from ..deps import get_container

router = APIRouter(prefix="/voice", tags=["voice"])


@router.post("/start", response_model=CallSession, status_code=status.HTTP_201_CREATED)
def start_call(
    body: VoiceStartIn,
    identity: ServiceIdentity = Depends(require_scopes("voice:start")),
    container: Container = Depends(get_container),
) -> CallSession:
    """Start the insurer call. 409 unless the dispute is APPROVED with stored authorization."""
    return container.voice_manager.start_call(body.case_id, actor=identity.actor)


@router.get("/{call_id}", response_model=CallSession)
def get_call(
    call_id: str,
    _: ServiceIdentity = Depends(require_scopes("voice:read")),
    container: Container = Depends(get_container),
) -> CallSession:
    return container.voice_manager.get_call(call_id)


@router.post("/{call_id}/event", response_model=VoiceEventResult)
def call_event(
    call_id: str,
    body: VoiceEventIn,
    identity: ServiceIdentity = Depends(require_scopes("voice:events")),
    container: Container = Depends(get_container),
) -> VoiceEventResult:
    """Insurer transcript or status event from a telephony/voice bridge."""
    return container.voice_manager.handle_event(call_id, body, actor=identity.actor)


@router.post(
    "/{call_id}/human-handoff",
    response_model=HumanIntervention,
    status_code=status.HTTP_201_CREATED,
)
def human_handoff(
    call_id: str,
    body: HandoffIn,
    identity: ServiceIdentity = Depends(require_scopes("voice:handoff")),
    container: Container = Depends(get_container),
) -> HumanIntervention:
    """Pause the call and bring in the patient (e.g. the patient wants to take over)."""
    return container.voice_manager.request_handoff(
        call_id, reason=body.reason, detail=body.detail, actor=identity.actor
    )
