from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from claimshield_contracts.api import HandoffIn, VoiceEventIn, VoiceEventResult, VoiceStartIn
from claimshield_contracts.models import CallSession, HumanIntervention

from ..adapters.telephony import webhook_signature
from ..auth import ServiceIdentity, require_scopes
from ..container import Container
from ..deps import get_container
from ..services import telnyx_service

router = APIRouter(prefix="/voice", tags=["voice"])


@router.post("/telnyx/webhook")
async def telnyx_webhook(
    request: Request,
    container: Container = Depends(get_container),
) -> dict[str, bool]:
    """Signed call events from Telnyx (Ed25519; no bearer token).

    Headers: ``Telnyx-Timestamp`` (unix seconds) and ``Telnyx-Signature-Ed25519``
    (base64 Ed25519 over ``timestamp`` + raw body; account public key from Mission
    Control). ``call.answered`` for the insurer leg bridges the call to xAI's SIP
    URI (transfer); ``call.hangup`` ends the Core call. Acked 200 per the Telnyx
    delivery contract.
    """
    try:
        return await run_in_threadpool(
            telnyx_service.process_telnyx_event,
            container,
            await request.body(),
            timestamp=request.headers.get(webhook_signature.TIMESTAMP_HEADER),
            signature=request.headers.get(webhook_signature.SIGNATURE_HEADER),
        )
    except webhook_signature.TelnyxSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_signature", "message": str(exc)},
        ) from exc


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
