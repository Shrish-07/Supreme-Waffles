"""PHI redaction and fail-fast configuration."""

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.audit import PhiRedactingFilter, mask_member_ref, mask_phone, redact, redact_text
from app.config import ConfigError, Settings
from app.demo_support import build_isolated_app, clean_env


@pytest.fixture(autouse=True)
def _no_repo_env() -> Iterator[None]:
    """A configured developer .env (loaded into the process env by fetch imports) must
    never poison these configuration tests, including the direct Settings() below."""
    with clean_env():
        yield


def test_redact_text_masks_phi_patterns() -> None:
    text = "SSN 123-45-6789, phone +1 555-555-0100, mail jo@example.org, member SYN-MBR-000123"
    redacted = redact_text(text)
    for secret in ("123-45-6789", "555-0100", "jo@example.org", "SYN-MBR-000123"):
        assert secret not in redacted
    assert "[REDACTED-SSN]" in redacted


def test_redact_keeps_ids_and_drops_phi_keys() -> None:
    payload = {"case_id": "case_1234567890abcdef", "notes": "anything", "nested": [{"text": "x"}]}
    assert redact(payload) == {
        "case_id": "case_1234567890abcdef",
        "notes": "[REDACTED]",
        "nested": [{"text": "[REDACTED]"}],
    }


def test_masking_helpers() -> None:
    assert mask_phone("+15555550100").endswith("0100") and "5555" not in mask_phone("+15555550100")
    assert mask_member_ref("SYN-MBR-000123") == "•••123"


def test_log_filter_redacts_messages() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "ssn=%s", ("123-45-6789",), None)
    PhiRedactingFilter().filter(record)
    assert "123-45-6789" not in record.getMessage()


def _settings(**overrides: object) -> Settings:
    with clean_env():  # a configured developer .env must never leak into these checks
        return Settings(_env_file=None, app_env="test", **overrides)  # type: ignore[arg-type]


def test_default_settings_are_all_mock_and_valid() -> None:
    settings = _settings()
    settings.validate_for_startup()
    assert set(settings.modes().values()) <= {"mock", "outbox", "rules", "local_direct"}


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({"xai_voice_mode": "live"}, "XAI_API_KEY"),
        ({"telephony_mode": "live"}, "TELNYX_API_KEY"),
        ({"finchnode_mode": "sandbox"}, "FINCHNODE_API_KEY"),
        ({"notify_mode": "relay_service", "relay_service_url": ""}, "RELAY_SERVICE_URL"),
        ({"app_env": "prod"}, "RELAY_SERVICE_TOKEN"),
    ],
)
def test_live_modes_fail_fast_naming_the_variable(overrides: dict, missing: str) -> None:
    settings = Settings(_env_file=None, **{"app_env": "test", **overrides})  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match=missing):
        settings.validate_for_startup()


def test_dev_fallback_tokens_only_outside_prod() -> None:
    assert all(_settings().service_tokens().values())
    prod = Settings(_env_file=None, app_env="prod")  # type: ignore[arg-type]
    assert not any(prod.service_tokens().values())
    assert prod.webhook_secret() == ""


def test_isolated_apps_ignore_a_configured_repo_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression test: fetch imports load the repo .env into the process env; isolated
    apps must still run fully in mock mode (and the environment must be restored)."""
    monkeypatch.setenv("FINCHNODE_MODE", "sandbox")
    monkeypatch.setenv("FINCHNODE_API_KEY", "ck_test_x")
    monkeypatch.setenv("FINCHNODE_BASE_URL", "https://example.invalid")
    app = build_isolated_app(tmp_path / "core.db")
    assert app.state.container.finchnode.mode == "mock"
    assert os.environ["FINCHNODE_MODE"] == "sandbox"  # clean_env() restored it


def test_a_partial_telnyx_fallback_profile_is_named() -> None:
    """Fallback Telnyx profiles must be complete key + connection + number triplets."""
    settings = _settings(
        telephony_mode="live",
        telnyx_api_key="K",
        telnyx_connection_id="c1",
        telnyx_phone_number="+15555550199",
        public_base_url="https://example.ngrok-free.dev",
        xai_sip_uri="sip:test@sip.x.ai",
        telnyx_webhook_verify=False,
        telnyx_api_key_2="KEY_v2",
        telnyx_phone_number_2="+10000000002",
    )  # TELNYX_CONNECTION_ID_2 missing
    with pytest.raises(ConfigError, match="TELNYX_CONNECTION_ID_2"):
        settings.validate_for_startup()
