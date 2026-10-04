"""Telnyx adapter + signed webhook route (offline; no network, no credits needed)."""

import base64
import json
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from app.adapters.telephony.telnyx_sip import (
    TelnyxSipTelephony,
    from_client_state,
    to_client_state,
)
from app.adapters.telephony.webhook_signature import (
    TelnyxSignatureError,
    verify_telnyx_signature,
)
from app.demo_support import build_isolated_app
from app.errors import SafetyBlocked, UpstreamError


@pytest.fixture
def telnyx_keys() -> tuple[SigningKey, str]:
    signing_key = SigningKey.generate()
    return signing_key, base64.b64encode(bytes(signing_key.verify_key)).decode()


def _sign(
    signing_key: SigningKey, body: bytes, timestamp: int | None = None
) -> dict[str, str]:
    at = timestamp if timestamp is not None else int(time.time())
    signature = signing_key.sign(str(at).encode() + body).signature
    return {
        "telnyx-signature-ed25519": base64.b64encode(signature).decode(),
        "telnyx-timestamp": str(at),
    }


def _event_body(event_type: str, **payload: object) -> bytes:
    return json.dumps(
        {
            "data": {
                "record_type": "event",
                "event_type": event_type,
                "id": "evt_test_1",
                "occurred_at": "2026-01-01T00:00:00Z",
                "payload": payload,
            }
        }
    ).encode()


# ------------------------------------------------------------------ client_state


def test_client_state_roundtrip() -> None:
    assert from_client_state(to_client_state("call_abc123")) == "call_abc123"
    assert from_client_state(None) is None
    assert from_client_state("") is None
    assert from_client_state("!!!-not-base64-!!!") is None


# ------------------------------------------------------------------ signatures


def test_signature_valid(telnyx_keys: tuple[SigningKey, str]) -> None:
    signing_key, public_key = telnyx_keys
    body = b'{"data":{"event_type":"call.initiated"}}'
    headers = _sign(signing_key, body)
    verify_telnyx_signature(
        public_key, body, headers["telnyx-timestamp"], headers["telnyx-signature-ed25519"]
    )


def test_signature_rejects_wrong_key(telnyx_keys: tuple[SigningKey, str]) -> None:
    signing_key, _ = telnyx_keys
    wrong_public = base64.b64encode(bytes(SigningKey.generate().verify_key)).decode()
    body = b"{}"
    headers = _sign(signing_key, body)
    with pytest.raises(TelnyxSignatureError):
        verify_telnyx_signature(
            wrong_public, body, headers["telnyx-timestamp"], headers["telnyx-signature-ed25519"]
        )


def test_signature_rejects_stale_timestamp(telnyx_keys: tuple[SigningKey, str]) -> None:
    signing_key, public_key = telnyx_keys
    body = b"{}"
    headers = _sign(signing_key, body, timestamp=int(time.time()) - 600)
    with pytest.raises(TelnyxSignatureError):
        verify_telnyx_signature(
            public_key, body, headers["telnyx-timestamp"], headers["telnyx-signature-ed25519"]
        )


def test_signature_requires_public_key_and_headers() -> None:
    with pytest.raises(TelnyxSignatureError):
        verify_telnyx_signature("", b"{}", "1", "sig")
    with pytest.raises(TelnyxSignatureError):
        verify_telnyx_signature("a2V5", b"{}", None, None)

# ------------------------------------------------------------------ adapter

Handler = Callable[[httpx.Request], httpx.Response]


def _adapter(handler: Handler) -> TelnyxSipTelephony:
    return TelnyxSipTelephony(
        api_key="KEY_test",
        connection_id="3063114307173614924",
        from_number="+18089005823",
        sip_uri="sip:test@sip.x.ai",
        public_base_url="https://public.example.ngrok-free.dev",
        allowed=frozenset({"+15555550100"}),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_dial_builds_the_call_control_request() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"data": {"call_control_id": "cc_1"}})

    result = _adapter(handler).dial("call_123", "+15555550100")
    assert result.provider_call_ref == "cc_1"
    assert captured["path"] == "/v2/calls"
    assert captured["auth"] == "Bearer KEY_test"
    body = captured["body"]
    assert body["to"] == "+15555550100"
    assert body["connection_id"] == "3063114307173614924"
    assert body["from"] == "+18089005823"
    assert body["webhook_url"] == "https://public.example.ngrok-free.dev/voice/telnyx/webhook"
    assert body["webhook_url_method"] == "POST"
    assert from_client_state(body["client_state"]) == "call_123"


def test_dial_refuses_numbers_outside_the_allowlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP call must be made for a blocked number")

    with pytest.raises(SafetyBlocked):
        _adapter(handler).dial("call_x", "+19999999999")


def test_dial_maps_http_errors_to_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": []})

    with pytest.raises(UpstreamError):
        _adapter(handler).dial("call_1", "+15555550100")


def test_transfer_to_sip_targets_xai_and_carries_the_call_id() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {}})

    _adapter(handler).transfer_to_sip("cc_1", "call_123")
    assert captured["path"] == "/v2/calls/cc_1/actions/transfer"
    assert captured["body"]["to"] == "sip:test@sip.x.ai"
    assert captured["body"]["headers"] == [
        {"name": "X-ClaimShield-Call-Id", "value": "call_123"}
    ]
    assert from_client_state(captured["body"]["client_state"]) == "call_123"


def test_hangup_posts_the_action() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json={"data": {}})

    _adapter(handler).hangup("cc_1")
    assert captured["path"] == "/v2/calls/cc_1/actions/hangup"


# ------------------------------------------------------------------ webhook route


def _post_event(app, signing_key: SigningKey, body: bytes) -> httpx.Response:
    with TestClient(app) as client:
        return client.post("/voice/telnyx/webhook", content=body, headers=_sign(signing_key, body))


def _stub_live_telephony(app, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Swap the mock adapter for a live Telnyx adapter on a MockTransport."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json={"data": {}})

    adapter = TelnyxSipTelephony(
        api_key="KEY_test",
        connection_id="c1",
        from_number="+18089005823",
        sip_uri="sip:test@sip.x.ai",
        public_base_url="https://public.example.ngrok-free.dev",
        allowed=frozenset(),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(app.state.container, "telephony", adapter)
    return captured


def test_webhook_rejects_a_bad_signature(tmp_path: Path, telnyx_keys) -> None:
    _, public_key = telnyx_keys
    app = build_isolated_app(tmp_path / "core.db", telnyx_public_key=public_key)
    body = _event_body("call.initiated", to="+15555550100", client_state=to_client_state("call_1"))
    response = _post_event(app, SigningKey.generate(), body)  # signed with the wrong key
    assert response.status_code == 401


def test_webhook_rejects_malformed_json(tmp_path: Path, telnyx_keys) -> None:
    signing_key, public_key = telnyx_keys
    app = build_isolated_app(tmp_path / "core.db", telnyx_public_key=public_key)
    response = _post_event(app, signing_key, b"this is not json")
    assert response.status_code == 422


def test_webhook_ignores_unrelated_events(tmp_path: Path, telnyx_keys) -> None:
    signing_key, public_key = telnyx_keys
    app = build_isolated_app(tmp_path / "core.db", telnyx_public_key=public_key)
    body = _event_body(
        "call.initiated",
        to="+15555550100",
        call_control_id="cc_1",
        client_state=to_client_state("call_1"),
    )
    response = _post_event(app, signing_key, body)
    assert response.status_code == 200
    assert response.json() == {"received": False}


def test_webhook_answered_is_ignored_in_mock_mode(tmp_path: Path, telnyx_keys) -> None:
    signing_key, public_key = telnyx_keys
    app = build_isolated_app(tmp_path / "core.db", telnyx_public_key=public_key)
    body = _event_body(
        "call.answered",
        to="+15555550100",
        call_control_id="cc_1",
        client_state=to_client_state("call_1"),
    )
    response = _post_event(app, signing_key, body)
    assert response.status_code == 200
    assert response.json() == {"received": False}


def test_webhook_answered_bridges_the_insurer_leg_to_xai(
    tmp_path: Path, telnyx_keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    signing_key, public_key = telnyx_keys
    app = build_isolated_app(tmp_path / "core.db", telnyx_public_key=public_key)
    captured = _stub_live_telephony(app, monkeypatch)
    body = _event_body(
        "call.answered",
        to="+15555550100",
        call_control_id="cc_1",
        client_state=to_client_state("call_123"),
    )
    response = _post_event(app, signing_key, body)
    assert response.status_code == 200
    assert response.json() == {"received": True}
    assert captured["path"] == "/v2/calls/cc_1/actions/transfer"  # the SIP bridge


def test_webhook_ignores_the_xai_sip_leg(
    tmp_path: Path, telnyx_keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    signing_key, public_key = telnyx_keys
    app = build_isolated_app(tmp_path / "core.db", telnyx_public_key=public_key)
    captured = _stub_live_telephony(app, monkeypatch)
    body = _event_body(
        "call.answered",
        to="sip:test@sip.x.ai",
        call_control_id="cc_1",
        client_state=to_client_state("call_123"),
    )
    response = _post_event(app, signing_key, body)
    assert response.status_code == 200
    assert response.json() == {"received": False}
    assert "path" not in captured  # no transfer for the xAI leg


def test_webhook_hangup_for_an_unknown_call_is_ignored(tmp_path: Path, telnyx_keys) -> None:
    signing_key, public_key = telnyx_keys
    app = build_isolated_app(tmp_path / "core.db", telnyx_public_key=public_key)
    body = _event_body(
        "call.hangup",
        to="+15555550100",
        call_control_id="cc_1",
        client_state=to_client_state("call_missing"),
    )
    response = _post_event(app, signing_key, body)
    assert response.status_code == 200
    assert response.json() == {"received": False}

