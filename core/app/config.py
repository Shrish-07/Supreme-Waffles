"""Core settings. One ``*_MODE`` flag per integration; live modes fail fast.

Values come from the environment and the repo-root ``.env`` (see .env.example).
Empty values are ignored, so a blank ``.env`` means "all defaults, all mock".
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from claimshield_contracts.enums import ServiceName

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_PATH = REPO_ROOT / ".data" / "claimshield.db"

# Well-known development credentials. Used only when APP_ENV is dev/test and
# the real variable is empty. Mirrored in relay/src/config.ts and fetch/agents/settings.py.
DEV_SERVICE_TOKENS: dict[ServiceName, str] = {
    ServiceName.RELAY: "dev-relay-token",
    ServiceName.FETCH: "dev-fetch-token",
    ServiceName.VOICE: "dev-voice-token",
}
DEV_CORE_WEBHOOK_SECRET = "dev-core-webhook-secret"


class ConfigError(RuntimeError):
    """Raised at startup when a live integration is missing required settings."""


class Settings(BaseSettings):
    """All Core configuration. Field names map 1:1 to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_whitespace(cls, value: object) -> object:
        # .env edits easily leave stray spaces (e.g. XAI_VOICE_MODEL=... trailing space).
        return value.strip() if isinstance(value, str) else value

    # --- core
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    database_url: str = ""
    core_base_url: str = "http://localhost:8080"
    relay_service_token: str = ""
    fetch_service_token: str = ""
    voice_service_token: str = ""
    core_webhook_secret: str = ""
    confirmation_threshold: float = 0.9
    notify_mode: Literal["outbox", "relay_service"] = "outbox"
    relay_service_url: str = ""

    # --- voice + telephony
    xai_voice_mode: Literal["mock", "live"] = "mock"
    xai_api_key: str = ""
    xai_realtime_url: str = "wss://api.x.ai/v1/realtime"
    xai_voice_model: str = ""
    xai_voice_name: str = ""
    telephony_mode: Literal["mock", "live"] = "mock"
    telnyx_api_key: str = ""
    telnyx_connection_id: str = ""
    telnyx_phone_number: str = ""
    telnyx_public_key: str = ""
    # false => accept Telnyx webhooks without an Ed25519 signature (demo only,
    # when the Mission Control public key is unavailable). Keep true in prod.
    telnyx_webhook_verify: bool = True
    public_base_url: str = ""
    xai_sip_uri: str = ""
    # Both demo insurers: the original synthetic line (+15555550100) and the
    # demo roster's current payer line from finchnode/fixtures + contracts
    # demo_scenario.json (+12487208903). Comma-separated E.164 list.
    telephony_allowed_numbers: str = "+15555550100,+12487208903"
    # Human merge: a handoff (SSN/PIN request...) dials this phone and
    # bridges the human into the call. Must also be in TELEPHONY_ALLOWED_NUMBERS.
    human_handoff_phone: str = ""
    # Fallback Telnyx accounts, used in order when the primary is rejected
    # (auth/credit). Each must be a complete key + connection + number triplet.
    telnyx_api_key_2: str = ""
    telnyx_connection_id_2: str = ""
    telnyx_phone_number_2: str = ""
    telnyx_api_key_3: str = ""
    telnyx_connection_id_3: str = ""
    telnyx_phone_number_3: str = ""

    # --- fetch
    fetch_mode: Literal["local_direct", "agentverse"] = "local_direct"
    orchestrator_url: str = "http://localhost:8000"

    # --- finchnode + intelligence
    finchnode_mode: Literal["mock", "demo", "sandbox"] = "mock"
    finchnode_api_key: str = ""
    finchnode_base_url: str = ""
    extraction_mode: Literal["rules", "ollama"] = "rules"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:1.7b"

    # ------------------------------------------------------------------ derived

    @property
    def resolved_database_url(self) -> str:
        """DATABASE_URL, or a local SQLite file when it is empty."""
        return self.database_url or f"sqlite:///{DEFAULT_SQLITE_PATH}"

    @property
    def allows_dev_defaults(self) -> bool:
        return self.app_env in ("dev", "test")

    def service_tokens(self) -> dict[ServiceName, str]:
        """Bearer token per caller, with dev fallbacks outside prod."""
        configured = {
            ServiceName.RELAY: self.relay_service_token,
            ServiceName.FETCH: self.fetch_service_token,
            ServiceName.VOICE: self.voice_service_token,
        }
        if self.allows_dev_defaults:
            return {name: value or DEV_SERVICE_TOKENS[name] for name, value in configured.items()}
        return configured

    def webhook_secret(self) -> str:
        """HMAC secret for POST /relay/webhook."""
        if self.core_webhook_secret:
            return self.core_webhook_secret
        return DEV_CORE_WEBHOOK_SECRET if self.allows_dev_defaults else ""

    def allowed_numbers(self) -> frozenset[str]:
        return frozenset(n.strip() for n in self.telephony_allowed_numbers.split(",") if n.strip())

    def telnyx_fallback_profiles(self) -> tuple[tuple[str, str, str], ...]:
        """Complete (API key, connection id, caller number) fallback triplets."""
        profiles: list[tuple[str, str, str]] = []
        for key, connection_id, phone in (
            (self.telnyx_api_key_2, self.telnyx_connection_id_2, self.telnyx_phone_number_2),
            (self.telnyx_api_key_3, self.telnyx_connection_id_3, self.telnyx_phone_number_3),
        ):
            if key and connection_id and phone:
                profiles.append((key, connection_id, phone))
        return tuple(profiles)

    def modes(self) -> dict[str, str]:
        return {
            "FINCHNODE_MODE": self.finchnode_mode,
            "NOTIFY_MODE": self.notify_mode,
            "XAI_VOICE_MODE": self.xai_voice_mode,
            "TELEPHONY_MODE": self.telephony_mode,
            "EXTRACTION_MODE": self.extraction_mode,
            "FETCH_MODE": self.fetch_mode,
        }

    def using_dev_credentials(self) -> list[str]:
        """Names of credentials currently falling back to dev defaults."""
        names = [
            f"{name.value.upper()}_SERVICE_TOKEN"
            for name, value in {
                ServiceName.RELAY: self.relay_service_token,
                ServiceName.FETCH: self.fetch_service_token,
                ServiceName.VOICE: self.voice_service_token,
            }.items()
            if not value
        ]
        if not self.core_webhook_secret:
            names.append("CORE_WEBHOOK_SECRET")
        return names if self.allows_dev_defaults else []

    def missing_live_settings(self) -> list[str]:
        """Human-readable list of settings required by the selected live modes."""
        missing: list[str] = []

        def need(flag: str, *pairs: tuple[str, str]) -> None:
            missing.extend(f"{flag} requires {name}" for name, value in pairs if not value)

        # demo is the public fake API and has a built-in base URL. No key.
        if self.finchnode_mode == "sandbox":
            need(
                "FINCHNODE_MODE=sandbox",
                ("FINCHNODE_BASE_URL", self.finchnode_base_url),
                ("FINCHNODE_API_KEY", self.finchnode_api_key),
            )
        if self.notify_mode == "relay_service":
            need("NOTIFY_MODE=relay_service", ("RELAY_SERVICE_URL", self.relay_service_url))
        if self.xai_voice_mode == "live":
            need(
                "XAI_VOICE_MODE=live",
                ("XAI_API_KEY", self.xai_api_key),
                ("XAI_REALTIME_URL", self.xai_realtime_url),
            )
        if self.telephony_mode == "live":
            live_pairs = [
                ("TELNYX_API_KEY", self.telnyx_api_key),
                ("TELNYX_CONNECTION_ID", self.telnyx_connection_id),
                ("TELNYX_PHONE_NUMBER", self.telnyx_phone_number),
                ("PUBLIC_BASE_URL", self.public_base_url),
                ("XAI_SIP_URI", self.xai_sip_uri),
            ]
            if self.telnyx_webhook_verify:
                live_pairs.append(("TELNYX_PUBLIC_KEY", self.telnyx_public_key))
            need("TELEPHONY_MODE=live", *live_pairs)
            for marker in ("2", "3"):
                parts = [
                    (f"TELNYX_API_KEY_{marker}", getattr(self, f"telnyx_api_key_{marker}")),
                    (
                        f"TELNYX_CONNECTION_ID_{marker}",
                        getattr(self, f"telnyx_connection_id_{marker}"),
                    ),
                    (
                        f"TELNYX_PHONE_NUMBER_{marker}",
                        getattr(self, f"telnyx_phone_number_{marker}"),
                    ),
                ]
                if any(value for _, value in parts) and not all(value for _, value in parts):
                    names = ", ".join(name for name, value in parts if not value)
                    missing.append(f"partial Telnyx fallback profile: missing {names}")
        if self.fetch_mode == "agentverse":
            need("FETCH_MODE=agentverse", ("ORCHESTRATOR_URL", self.orchestrator_url))
        if self.extraction_mode == "ollama":
            need(
                "EXTRACTION_MODE=ollama",
                ("OLLAMA_URL", self.ollama_url),
                ("OLLAMA_MODEL", self.ollama_model),
            )
        if self.app_env == "prod":
            need(
                "APP_ENV=prod",
                ("RELAY_SERVICE_TOKEN", self.relay_service_token),
                ("FETCH_SERVICE_TOKEN", self.fetch_service_token),
                ("VOICE_SERVICE_TOKEN", self.voice_service_token),
                ("CORE_WEBHOOK_SECRET", self.core_webhook_secret),
            )
        return missing

    def validate_for_startup(self) -> None:
        """Raise ConfigError naming every missing variable for the selected modes."""
        missing = self.missing_live_settings()
        if missing:
            lines = "\n".join(f"  - {item}" for item in missing)
            raise ConfigError(
                "Amyna Core cannot start: live integrations are missing settings.\n"
                f"{lines}\n"
                "Fill these in .env (see docs/SETUP_KEYS.md) or switch the flag back to mock."
            )


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings (tests build their own Settings instead)."""
    return Settings()
