"""Patient identity (tasks 5/19): users own cases; IDs are never credentials."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.demo_support import DATA_CONSENT, build_isolated_app, service_headers
from app.errors import Forbidden
from app.models_db import FinchLinkRow
from app.services.identity_service import (
    PATIENT_TOKEN_HEADER,
    create_demo_session,
)
from claimshield_contracts.enums import ServiceName


def _session(client: TestClient, subject: str) -> dict[str, str]:
    relay = service_headers(client.app.state.container.settings, ServiceName.RELAY)  # type: ignore[attr-defined]
    result = client.post("/auth/session", json={"subject": subject}, headers=relay).json()
    return {"patient_token": result["patient_token"], "user_id": result["user_id"]}


def test_auth_session_issues_an_opaque_token(client: TestClient) -> None:
    session = _session(client, "relay:patient-one")
    assert session["patient_token"].startswith("pt_")
    assert session["user_id"].startswith("user_")


def test_sessions_are_only_issued_to_an_authenticated_service(
    client: TestClient, fetch: dict[str, str]
) -> None:
    """Nobody can mint a token for someone else's handle without the Relay token."""
    anonymous = client.post("/auth/session", json={"subject": "relay:mary"})
    assert anonymous.status_code == 401
    other_service = client.post("/auth/session", json={"subject": "relay:mary"}, headers=fetch)
    assert other_service.status_code == 403


def test_relay_handles_get_sessions_in_production(tmp_path: Path) -> None:
    """Relay is the identity provider for relay:<handle> subjects, so prod works."""
    app = build_isolated_app(tmp_path / "prod.db")
    relay = service_headers(app.state.container.settings, ServiceName.RELAY)
    app.state.container.settings.app_env = "prod"  # only this endpoint's policy is under test
    client = TestClient(app)
    issued = client.post("/auth/session", json={"subject": "relay:+15550001111"}, headers=relay)
    assert issued.status_code == 201 and issued.json()["patient_token"].startswith("pt_")
    demo = client.post("/auth/session", json={"subject": "demo-user"}, headers=relay)
    assert demo.status_code == 403


def test_a_case_belongs_to_the_authenticated_user(
    client: TestClient, relay: dict[str, str]
) -> None:
    mary = _session(client, "relay:mary")
    headers_mary = {**relay, PATIENT_TOKEN_HEADER: mary["patient_token"]}
    case = client.post(
        "/cases",
        json={"patient": {"finchnode_patient_id": "fn_pat_demo_001"}, "channel": "relay"},
        headers=headers_mary,
    ).json()
    detail = client.get(f"/cases/{case['case_id']}", headers=headers_mary)
    assert detail.status_code == 200
    bob = _session(client, "relay:bob")
    headers_bob = {**relay, PATIENT_TOKEN_HEADER: bob["patient_token"]}
    assert client.get(f"/cases/{case['case_id']}", headers=headers_bob).status_code == 403
    # A service token alone does not prove the patient: owned cases need the session.
    assert client.get(f"/cases/{case['case_id']}", headers=relay).status_code == 403


def test_anonymous_cases_stay_service_reachable(client: TestClient, relay: dict[str, str]) -> None:
    """Legacy/demo flows without a patient token keep working (user NULL)."""
    case = client.post(
        "/cases",
        json={"patient": {"finchnode_patient_id": "fn_pat_demo_001"}, "channel": "relay"},
        headers=relay,
    ).json()
    assert client.get(f"/cases/{case['case_id']}", headers=relay).status_code == 200


def test_demo_sessions_are_disabled_in_production(
    client: TestClient,
) -> None:
    """create_demo_session refuses outside dev/test/staging (production uses the IDP)."""
    with (
        client.app.state.container.session_scope() as db,  # type: ignore[attr-defined]
        pytest.raises(Forbidden),
    ):
        create_demo_session(db, app_env="prod", subject="relay:x")


def test_patient_context_tool_returns_masked_identifiers(
    client: TestClient, relay: dict[str, str], fetch: dict[str, str]
) -> None:
    """task 6: agents/LLMs ask Core for the authorized context; no raw identifiers."""
    case = client.post(
        "/cases",
        json={
            "patient": {
                "finchnode_patient_id": "fn_pat_demo_001",
                "member_ref": "SYN-MBR-000123",
                "display_name": "Mary Doe",
            },
            "channel": "relay",
        },
        headers=relay,
    ).json()
    context = client.post(
        "/internal/tools/get_patient_context", json={"case_id": case["case_id"]}, headers=fetch
    ).json()
    assert context["case_id"] == case["case_id"]
    assert context["member_ref_masked"] == "•••123"
    assert "SYN-MBR-000123" not in str(context)
    assert context["has_health_data_consent"] is False
    # Relay's identity lacks the tool scope; patient surfaces never call tools.
    assert (
        client.post(
            "/internal/tools/get_patient_context", json={"case_id": case["case_id"]}, headers=relay
        ).status_code
        == 403
    )


def test_other_patients_cannot_act_on_an_owned_case(
    client: TestClient, relay: dict[str, str]
) -> None:
    """Ownership holds on every Relay route, not only /cases/*."""
    mary = _session(client, "relay:mary")
    bob = _session(client, "relay:bob")
    as_mary = {**relay, PATIENT_TOKEN_HEADER: mary["patient_token"]}
    as_bob = {**relay, PATIENT_TOKEN_HEADER: bob["patient_token"]}
    case = client.post(
        "/cases",
        json={"patient": {"finchnode_patient_id": "fn_pat_demo_001"}, "channel": "relay"},
        headers=as_mary,
    ).json()
    claim = {"case_id": case["case_id"], "source": "finchnode", "finchnode_claim_id": "CLM-1"}
    assert client.post("/claims", json=claim, headers=as_bob).status_code == 403
    only_case = {"case_id": case["case_id"]}
    assert client.post("/disputes", json=only_case, headers=as_bob).status_code == 403
    assert client.post("/voice/start", json=only_case, headers=as_bob).status_code == 403
    visit = {
        "case_id": case["case_id"],
        "recording_consent_id": "cns_x",
        "recording_ref": "mock://x",
    }
    assert client.post("/visits", json=visit, headers=relay).status_code == 403  # no session


def test_asi_one_never_adopts_a_signed_in_patients_case(
    client: TestClient, relay: dict[str, str], fetch: dict[str, str]
) -> None:
    """ASI:One has no patient session: typing a patient id must not reach Mary's case."""
    mary = _session(client, "relay:mary")
    owned = client.post(
        "/cases",
        json={"patient": {"finchnode_patient_id": "fn_pat_demo_001"}, "channel": "relay"},
        headers={**relay, PATIENT_TOKEN_HEADER: mary["patient_token"]},
    ).json()
    found = client.post(
        "/internal/tools/find_or_create_case",
        json={"finchnode_patient_id": "fn_pat_demo_001", "channel": "asi1"},
        headers=fetch,
    ).json()
    assert found["created"] is True and found["case"]["case_id"] != owned["case_id"]


def test_granted_health_consent_links_finch_on_owned_case(
    client: TestClient, relay: dict[str, str]
) -> None:
    """Regression (CI relay demo 500): granting health-data consent on a
    user-owned case must mint the FinchLink with the registered id kind --
    identity_service once passed the *prefix* ("flink") to new_id instead of
    the *kind* ("finch_link"), so every consent on an owned case 500'd."""
    mary = _session(client, "relay:mary")
    headers = {**relay, PATIENT_TOKEN_HEADER: mary["patient_token"]}
    case = client.post(
        "/cases",
        json={"patient": {"finchnode_patient_id": "fn_pat_demo_001"}, "channel": "relay"},
        headers=headers,
    ).json()
    granted = client.post(
        f"/cases/{case['case_id']}/consents",
        json={
            "consent_type": "health_data_access",
            "consent_text": DATA_CONSENT,
            "channel": "relay",
        },
        headers=headers,
    )
    assert granted.status_code == 201
    with client.app.state.container.session_scope() as db:  # type: ignore[attr-defined]
        link = db.scalar(select(FinchLinkRow).where(FinchLinkRow.user_id == mary["user_id"]))
        assert link is not None
        assert link.id.startswith("flink_")
        assert link.finchnode_patient_id == "fn_pat_demo_001"
        assert link.status == "linked"
