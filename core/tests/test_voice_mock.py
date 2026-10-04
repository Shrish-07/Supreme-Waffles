"""Mock voice call: SSN handoff, injected sensitive requests, guardrails, allowlist."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.xai_voice.dispatcher import ToolCallDispatcher
from app.demo_support import APPROVAL_TEXT
from app.models_db import CallRow, CaseRow, InterventionRow
from claimshield_contracts.tool_api import ToolName

APPROVAL = {"channel": "relay", "authorization_text": APPROVAL_TEXT, "authorize_call": True}


@pytest.fixture
def paused_call(
    client: TestClient, relay: dict[str, str], awaiting_approval: dict[str, Any]
) -> dict[str, Any]:
    client.post(
        f"/disputes/{awaiting_approval['dispute']['dispute_id']}/approve",
        json=APPROVAL,
        headers=relay,
    )
    call = client.post(
        "/voice/start", json={"case_id": awaiting_approval["case_id"]}, headers=relay
    ).json()
    return {**awaiting_approval, "call": call}


def test_ssn_request_pauses_and_notifies(
    client: TestClient, relay: dict[str, str], paused_call: dict[str, Any]
) -> None:
    call = paused_call["call"]
    assert call["status"] == "paused_for_human"
    flagged = [t for t in call["turns"] if "sensitive:ssn" in t["flags"]]
    assert flagged and flagged[0]["speaker"] == "insurer"
    intervention = client.get(
        f"/human-intervention/{call['open_intervention_id']}", headers=relay
    ).json()
    assert intervention["reason"] == "identity_verification" and intervention["options"] == [
        "DONE",
        "CANCEL",
    ]
    case = client.get(f"/cases/{paused_call['case_id']}", headers=relay).json()
    assert case["state"] == "HUMAN_REQUIRED"
    notes = client.get(f"/cases/{paused_call['case_id']}/notifications", headers=relay).json()
    handoff = [n for n in notes if n["kind"] == "human_handoff"]
    assert handoff and "never shares your SSN" in handoff[0]["text"]
    agent_lines = [t["text"] for t in call["turns"] if t["speaker"] == "agent"]
    assert not any(any(ch.isdigit() for ch in line) and "SSN" in line for line in agent_lines)


def test_resolution_notes_with_ssn_are_rejected(
    client: TestClient, relay: dict[str, str], paused_call: dict[str, Any]
) -> None:
    response = client.post(
        f"/human-intervention/{paused_call['call']['open_intervention_id']}/resolve",
        json={"choice": "DONE", "notes": "my ssn is 123-45-6789", "channel": "relay"},
        headers=relay,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "sensitive_data_rejected"


def test_cancel_ends_call_as_failed(
    client: TestClient, relay: dict[str, str], paused_call: dict[str, Any]
) -> None:
    response = client.post(
        f"/human-intervention/{paused_call['call']['open_intervention_id']}/resolve",
        json={"choice": "CANCEL", "channel": "relay"},
        headers=relay,
    ).json()
    assert response["case_state"] == "FAILED" and response["call"]["status"] == "failed"


def test_resolving_handoff_completes_call(
    client: TestClient, relay: dict[str, str], voice: dict[str, str], paused_call: dict[str, Any]
) -> None:
    call_id = paused_call["call"]["call_id"]
    client.post(
        f"/human-intervention/{paused_call['call']['open_intervention_id']}/resolve",
        json={"choice": "DONE", "channel": "relay"},
        headers=relay,
    )
    call = client.get(f"/voice/{call_id}", headers=voice).json()
    assert call["status"] == "completed"
    assert call["insurer_response"]["outcome"] == "partially_removed"
    assert not any(t["flags"] and t["flags"][0].startswith("blocked") for t in call["turns"])


def test_event_endpoint_detects_pin_requests(
    client: TestClient,
    relay: dict[str, str],
    voice: dict[str, str],
    paused_call: dict[str, Any],
    app: Any,
) -> None:
    call_id = paused_call["call"]["call_id"]
    container = app.state.container
    with container.session_scope() as db:  # put the call back in progress, as a live bridge would
        call = db.get(CallRow, call_id)
        db.get(InterventionRow, call.open_intervention_id).status = "resolved"
        call.status, call.open_intervention_id = "in_progress", None
        db.get(CaseRow, call.case_id).state = "CALL_IN_PROGRESS"
    response = client.post(
        f"/voice/{call_id}/event",
        json={"type": "insurer_utterance", "text": "What is the PIN on the account?"},
        headers=voice,
    ).json()
    assert response["action"] == "pause_for_human" and response["intervention_id"]
    assert response["call"]["status"] == "paused_for_human"


def test_number_outside_allowlist_is_never_dialed(
    client: TestClient, relay: dict[str, str], awaiting_approval: dict[str, Any], app: Any
) -> None:
    client.post(
        f"/disputes/{awaiting_approval['dispute']['dispute_id']}/approve",
        json=APPROVAL,
        headers=relay,
    )
    from app.models_db import CaseRow

    with app.state.container.session_scope() as db:
        db.get(CaseRow, awaiting_approval["case_id"]).payer_phone = "+15555550199"
    response = client.post(
        "/voice/start", json={"case_id": awaiting_approval["case_id"]}, headers=relay
    )
    assert response.status_code == 409 and response.json()["detail"]["code"] == "number_not_allowed"
    assert app.state.container.telephony.dialed == []


class _Backend:
    def __init__(self) -> None:
        self.calls: list[tuple[ToolName, dict]] = []

    def call(self, tool: ToolName, payload: dict) -> dict:
        self.calls.append((tool, payload))
        return {"ok": True}


def test_dispatcher_injects_ids_and_rejects_model_supplied_case_id() -> None:
    backend = _Backend()
    dispatcher = ToolCallDispatcher(backend, case_id="case_real", call_id="call_real")
    result = dispatcher.dispatch("get_dispute_case", {"case_id": "case_other"})
    assert result["error"] == "invalid_arguments" and backend.calls == []
    dispatcher.dispatch("request_human", '{"reason": "credential_request", "detail": "PIN asked"}')
    tool, payload = backend.calls[0]
    assert tool == ToolName.REQUEST_HUMAN
    assert payload["case_id"] == "case_real" and payload["call_id"] == "call_real"
    assert dispatcher.dispatch("delete_everything", {})["error"] == "unknown_tool"
