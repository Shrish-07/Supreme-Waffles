# Amyna — Production Overhaul: every change, the full architecture, and the exact path to a hands-off global product

**Branch:** `Shrish-07's-Overhaul/Changes` → `main`
**Scale:** 17 content commits + this PR narrative · 118 files changed · +5,600+ / −289 lines (the 17-commit baseline is 117 files, +5,215 / −289, through `5980382`)
**Verified gates at HEAD:** 310 Python tests passed · 43 Relay (vitest) tests passed · `ruff check` clean · `ruff format --check` clean · Relay TypeScript strict lint (`tsc`) clean · Alembic migrations 0001–0007 green on a fresh DB · full mock end-to-end smoke through the real Core HTTP API: **SMOKE PASSED**
**Branch POSIX-shy:** the merge integrated the remote minimal rename (`e6abd99`) and resolved the README conflict keeping the fuller Amyna README.
**(One cosmetic note):** commit `be84bfb`'s message body contains a literal `` `n `` quoting artifact from the Windows shell — content is unaffected.

---

# PART 1 — WHAT CHANGED (commit by commit, then subsystem by subsystem)

## 1.1 The 18 commits on this branch

| Commit | What it did |
|---|---|
| `82d47df` | **fix(telephony):** resolved the audited payer-phone allowlist regression (21 tests + smoke were red at the old HEAD; green from here on) |
| `e6abd99` | *(merged in)* minimal remote rename of the README intro |
| `fd71df3` | **feat(branding):** Amyna customer-facing rebrand across README, Relay, Agentverse, docs; removed a stale repo artifact |
| `991908f` | **feat(overhaul):** `X-Amyna-*` wire-header rebrand with regenerated OpenAPI/TS contracts; fail-closed staging/production environments (`environment_mistakes`); the Amyna language layer (`assistant_service.py`); FinchNode `production` client mode; docker-compose Postgres naming fix; `.env.staging.example` / `.env.production.example`; `docs/DEPLOYMENT.md`; `AUDIT_REPORT.md` removed |
| `ed60843` | **feat(voice):** first-class 17A human-merge lifecycle (`CallMergePhase`), unreachable-patient escalation, `HUMAN_MERGE_TIMEOUT_S`, migration 0003, deterministic tests |
| `53c8211` | **feat(product):** natural-language onboarding wired end to end — `/assistant/*` endpoints, Relay + orchestrator copy, natural-onboarding tests |
| `480bd79` | **feat(identity):** users/sessions/finch-links/case ownership (migration 0004), `/auth/session`, `X-Amyna-Patient-Token` forwarding, identity tests |
| `be84bfb` | **feat(tools):** `get_patient_context` — the only private-data door for agents/LLMs (tool #22), masked + consent-gated |
| `8630701` | **feat(analysis):** concurrent, fault-tolerant patient-record pulls (barrier-proven parallelism, partial-failure preservation) |
| `0ab0bd3` | **feat(ops):** durable job system (migration 0005) + observability (request IDs, `/internal/metrics`), 8 tests |
| `37ec0b6` | **feat(findings):** `patient_facing_explanation` on every finding (migration 0006) |
| `647a7f9` | **feat(voice):** **docs-verified xAI SIP attach flow** — `/voice/xai/webhook`, `XAI_WEBHOOK_SIGNING_SECRET`, `attach_sip_session`, `needs_sip_attach`, migration 0007, VERIFY_LIST updated with doc-verified facts |
| `60631da` | **feat(deploy):** production compose stack, `scripts/job_worker.py`, `deploy/backup.sh`, runbook |
| `bf848d4` | **test(failure):** the full failure matrix (duplicate approval, Relay outage, Agentverse disconnect, ambiguous identity, Telnyx outage, PHI leak) |
| `5980382` | **docs:** README + SECURITY + STATE_MACHINE refreshed for the overhaul |
| *(this file)* | **docs(pr):** the complete PR narrative — what changed, the full architecture, and the compatible path to a hands-off global product |
| `70436d2` | merge of the remote `e6abd99` rename |

## 1.2 Branding (task 32)

- **Customer-facing Amyna everywhere:** README title/body, Relay welcome + all patient copy (`relay/src/ux/messages.ts`), orchestrator welcome/help/status (`fetch/agents/logic/orchestrate.py`), Agentverse profile (`fetch/agentverse/agent_profiles/orchestrator.md`), `README_AGENTVERSE.md`, `docs/DEMO_SCRIPT.md`.
- **Wire protocol rebranded and regenerated:** `X-Amyna-Signature` / `X-Amyna-Timestamp` (`core/app/auth.py`, mirrored in `relay/src/coreClient.ts`), `X-Amyna-Contracts-Version` (`core/app/main.py`), `X-Amyna-Call-Id` SIP header (`core/app/adapters/telephony/telnyx_sip.py`), plus new `X-Amyna-Patient-Token` and `X-Amyna-Request-Id`. `shared/openapi/core.openapi.json` + `shared/ts/core.ts` regenerated from the live app so Core and Relay cannot drift.
- **Public agent renamed** to `amyna-orchestrator` (addresses derive from seeds, so the rename is safe for existing `.env` seeds); Bureau test and `README_AGENTVERSE.md` updated.
- **Deliberately retained (allowed by the brief):** internal Python package names (`claimshield_contracts`, `claimshield-libs`, `claimshield-core`, `claimshield-relay`) and internal uAgents protocol strings (`claimshield-visit-evidence`, `claimshield-claim-analysis`, `claimshield-dispute`, `claimshield-orchestrator-replies`) — renaming them would break imports and the pinned message-digest fixtures (`shared/python/claimshield_contracts/fixtures/agent_message_digests.json`) for zero customer value. Zero `ClaimShield` strings remain in any customer-facing path.

## 1.3 The language layer (tasks 2/4/26)

**New module `core/app/services/assistant_service.py`** — a single, dedicated wrapper for ALL natural-language work (nothing is scattered).

Eight explicit functions, each with structured Pydantic I/O:
`understand_patient_message` · `extract_bill_information` · `extract_visit_information` · `explain_term` · `generate_clarifying_question` · `summarize_findings` · `explain_dispute` · `prepare_patient_call_summary`.

Exact structured schemas (module models, exposed over HTTP by contract models in `shared/python/claimshield_contracts/api.py`):
- `UnderstoodMessage` → `{intent, candidate_claim_number, candidate_patient_name, candidate_provider, candidate_amount_cents, suggested_action, follow_up_question, confidence}` — matching the brief's example shape exactly.
- `CandidateValue` → `{value, confidence, needs_confirmation: True}` — **every extracted identifier is a candidate the deterministic backend must verify; the layer cannot look anyone up.**
- `BillExtraction` → `{provider, patient_name, claim_number, amount_cents, insurer, service_date, line_items[], missing_fields[]}`.
- `VisitExtraction` → `{occurred_at, facility, services_received[], services_not_received[], discrepancy_candidates[]}`.
- `PatientIntent` (8): `review_bill, question_about_bill, dispute_charge, request_call, explain_term, record_visit, provide_bill, unknown`.
- `AssistantAction` (7 — the explicit allowlist the layer may *suggest*, never execute): `request_bill, start_consent, record_visit, review_bill, explain_term, ask_clarifying, prepare_dispute_review`.
- Built-in glossary with instant plain-English explanations for: claim number, member id, payer, CPT, HCPCS, EOB.
- `compose_reply()` turns any patient message into ONE ready-to-send plain-English reply (never asks for jargon; mentions what it already extracted — e.g. *"I can already see the claim number CLM-… and the amount $842.60 on this bill."*).

**Hard policy boundary (enforced by construction):** the service is stateless — no DB handle, no credentials, no record data. It cannot decide identity, consent, authorization, dispute submission, call placement, dialing, or disclosure; those live in `auth.py`, `state_machine.py`, `security_policy.py`, `case_service.py`. A dedicated test asserts a typed SSN never passes through or echoes back.

**Pluggable engine:** the `LanguageEngine` protocol has a deterministic offline default (`HeuristicLanguageEngine` — free, and no PHI ever leaves the machine); a real LLM slots in behind the identical structured I/O.

**New HTTP surface (Relay-only scope `assistant:use`):**
- `POST /assistant/understand` `{message}` → intent + candidates + ready-to-send reply.
- `POST /assistant/explain` `{term}` → plain-English explanation.

## 1.4 The 17A immediate human merge (task 17A)

**New enum `CallMergePhase`** (contracts) + `call_sessions.merge_phase` / `merge_deadline_at` (migration 0003) + surfaced on the `CallSession` API model:

```
AI_CALLING → SENSITIVE_REQUEST_DETECTED → PATIENT_BEING_CONNECTED
           → PATIENT_CONNECTED_WITH_HUMAN → PATIENT_HANDLING_VERIFICATION
           → PATIENT_RELEASED → AI_RESUMING → AI_CALLING
           (MERGE_FAILED = the explicit failure escape)
```

Behavior, exactly as the brief requires:
1. `detect_sensitive_request()` (in code, not the model) fires on SSN / DOB-as-auth / PIN / password / MFA / card / bank asks → AI output pauses immediately (`_pause_for_human`).
2. Core **immediately** dials `HUMAN_HANDOFF_PHONE` into the SAME live insurer call (`_maybe_dial_human`) — the patient does NOT type DONE to start the connection; the insurer leg is preserved.
3. The patient is notified through Relay in plain language.
4. **Idempotent:** duplicate sensitive requests never create a second intervention or a second human leg (guarded on `human_call_ref` + phase + call status).
5. Mock telephony answers instantly (`patient_connected_with_human`); live Telnyx sets that phase on the human-leg `call.answered` bridge webhook and `patient_released` on the human-leg hangup.
6. **Unreachable patient:** the `HUMAN_MERGE_TIMEOUT_S` deadline (default 120s); `VoiceCallManager.expire_overdue_merges()` (called by the `voice.merge_sweep` durable job / worker tick) escalates to `merge_failed` — a plain-language notification, the insurer leg kept alive within policy, and **the AI never auto-resumes after a failed connection**; only the explicit patient DONE continues.
7. DONE → `PATIENT_RELEASED` → the human leg is hung up → `AI_RESUMING` → the AI resumes only after this explicit state-machine transition.
8. Secrets are never exposed to Amyna or stored (pre-existing redaction retained and re-tested).

**Tests (deterministic):** immediate-connect · pause-only fallback (no handoff phone) · failed-merge explicit state + no-redial + explicit-DONE-continues · unreachable-patient timeout escalation (insurer leg alive, AI paused) · full DONE release cycle · duplicate-request idempotency.

## 1.5 Real patient identity (tasks 5/19)

**Migration 0004** adds `users` (id, auth_subject unique, display_name, created_at), `patient_sessions` (token_hash PK, user_id — **only SHA-256 hashes stored**), `finch_links` (user_id, finchnode_patient_id, status, created/revoked), and `cases.user_id` (FK, indexed).

- `POST /auth/session {subject}` → `{user_id, patient_token}` — issues opaque `pt_…` tokens. **Dev/test/staging only**: production raises `dev_sessions_disabled`.
- Relay obtains a session per platform user on first contact and forwards `X-Amyna-Patient-Token` on every patient call (`patientScope` in `relay/src/handlers/context.ts`; tokens are held in memory only, never persisted).
- `POST /cases` binds the resolved user as owner. Owned cases: reads, consent writes, timeline and notifications all enforce `assert_case_access` → `case_forbidden` (403) for any other user or a bare service token. Cross-user denial, owner-allow, and anonymous-back-compat are each tested.
- Granting `health_data_access` consent creates the `finch_links` binding (audited `identity.finch_linked`) — the "authenticated account → Finch authorization → secure Core binding → authorized record access" chain from the brief.
- Production: Relay case creation without a patient session is refused (`patient_session_required`); the ASI:One/agent path stays explicitly demo-scoped (an Agentverse sender address proves nothing).
- A typed `fn_pat_…` ID remains an **identifier, never a credential**; a name alone can never create or open a case (422, tested).

## 1.6 `get_patient_context` — the only private-data door (task 6)

New 22nd tool in the registry: returns the case's **masked, consent-status-carrying** `PatientContext` — `{case_id, patient_display_name, member_ref_masked (•••123), payer_name, finchnode_patient_id, has_health_data_consent}`. Wired end-to-end: `TOOL_SPECS`, per-agent allowlists (Visit, Claim, Voice), the `CoreTools` protocol, the in-process bridge, the HTTP bridge, the tool router, and the voice scope. Tests assert the raw member ref never appears and that Relay identities have no tool scope. Language/agent layers never receive raw patient credentials — they ask Core, exactly as the brief's `get_authorized_patient_context()` requires.

## 1.7 Parallel analysis + finding synthesis (tasks 11/12)

- `fetch/agents/logic/claim_logic.py`: `_pull_patient_records()` runs encounters/procedures/medications **concurrently** (3-thread pool, same case/request correlation id). A failing source never loses the others (partial failure proceeds; total failure errors). A deterministic **3-party barrier test** proves true parallelism (it would deadlock if sequential).
- `DisputeCandidate.patient_facing_explanation` (migration 0006): derived from the structured finding at engine time (`"{title}. {explanation}"`); persisted through `claim_service.record_findings`, mapped back by `mappers`, present on the API. The LLM may narrate it but can never invent facts. Every finding already carries: finding id, claim line, category (issue_type), evidence refs, confidence, explanation, recommended action, and the unresolved question (the `ConfirmationQuestion`) when below the 0.9 `CONFIRMATION_THRESHOLD`.

## 1.8 Live voice — corrected against the CURRENT official docs (tasks 15/16/24)

Web research against **docs.x.ai** and **developers.telnyx.com** found the prototype's core assumption was wrong; this PR implements the real flow:

**What the docs actually say (and what we now do):**
- xAI realtime voice: `wss://api.x.ai/v1/realtime?model=grok-voice-latest`, `Authorization: Bearer` header; `session.update` with `{voice, instructions, turn_detection: server_vad}`; `response.create` drives turns; `response.cancel` + per-response `instructions` for interruption; an xAI-specific `force_message` extension for verbatim lines; session resumption via `conversation_id`. → matches `live.py`; the endpoint builder now also supports **`?call_id=`** attach.
- **xAI SIP is inbound:** register a SIP number (BYO trunk; xAI returns a webhook signing secret once) → carry the insurer call to `sip:{number}@sip.voice.x.ai;transport=tls` → xAI POSTs a **signed `realtime.call.incoming` webhook** (`webhook-id`, `webhook-timestamp`, `webhook-signature`) with `data.call_id` + `data.sip_headers` → **attach the realtime WS with `?call_id=`**. Custom SIP headers are **not** sent on REFER.

**Implemented:**
- `core/app/adapters/xai_voice/sip_webhook.py` (Standard-Webhooks-style HMAC-SHA256 verification, replay window, `realtime.call.incoming` parsing).
- `POST /voice/xai/webhook` route (correlates via the `X-Amyna-Call-Id` SIP header Telnyx forwards).
- Idempotent `VoiceCallManager.attach_sip_session` (duplicate webhooks ignored; failures hang up safely and close the case FAILED).
- `VoiceSessionConfig.bridge_call_id`, `VoiceAdapter.needs_sip_attach` (mock adapters still drive immediately, so every mock test/demo flow is unchanged; `start_call` defers the session in SIP mode instead of pretending to connect before the call exists).
- **Fail-closed config:** `XAI_WEBHOOK_SIGNING_SECRET` required whenever live voice + live telephony are on (validated at startup).
- **Telnyx webhook contract doc-verified:** `telnyx-timestamp` + `telnyx-signature-ed25519`, Ed25519 over timestamp + raw body, public key from Mission Control → API keys → Public key, ack 200 promptly, dedupe by event `id`, out-of-order tolerated — matches `webhook_signature.py` + `telnyx_service.py` (which already ack-200s and is idempotent).
- `docs/VERIFY_LIST.md` now separates ✅ doc-verified items from the small ☐ set only the first live call can confirm (exact transcript/function-call event names; whether the carrier preserves our correlation header — attach fails safe either way; the exact Telnyx payload field names vs their consolidated OpenAPI).

## 1.9 Durable jobs (task 28)

**Migration 0005 — `jobs` table:** `id, job_type, case_id, correlation_id, idempotency_key (UNIQUE), state, attempts, max_attempts, payload, timeout_at, next_run_at, last_error, created_at, finished_at`.
- States: `queued → running → succeeded | failed (retryable) → dead (the final failure state)`.
- `job_service.enqueue` is idempotent on the key (duplicate triggers never double-work); every lifecycle step is audited (`job.enqueued`, `job.succeeded`, `job.retry_or_dead`).
- `drain()` runs due jobs with **exponential backoff** (5s · 2^attempts) up to `max_attempts`, then the explicit `dead` state.
- `POST /internal/jobs/drain` (scope `jobs:run`) is the worker tick; `scripts/job_worker.py` is the standalone production worker (interval `WORKER_INTERVAL_S`, exits for supervisor restart after 12 consecutive unreachable ticks — no silent spin on a down Core).
- The 17A unreachable-patient sweep is a registered handler (`voice.merge_sweep`).
- Tests: idempotent enqueue · retry→succeed with backoff · retry→dead · scoped drain endpoint · the merge-expiry sweep through the worker · metrics snapshot · request-id echo.

## 1.10 Observability (task 29)

- `X-Amyna-Request-Id` middleware: honored from the caller or generated (`req_…`), **echoed on every response**, so one case fans out as one trace.
- `GET /internal/metrics` (scope `metrics:read`): `requests` (per route template + status class), `latency_avg_ms`, `queue_depth {queued, running, dead}`, `active_calls`, `open_interventions`, `stuck_cases_24h`. PHI-free by construction (counts and route templates only); logs remain PHI-redacted via the existing `PhiRedactingFilter`/`redact` pipeline.

## 1.11 Environments + fail-closed startup (task 21)

Four environments: `local | test | staging | production` (`APP_ENV`). `Settings.environment_mistakes()` refuses startup in staging/prod when ANY of the following is true:
- `DATABASE_URL` isn't PostgreSQL (no silent SQLite fallback);
- `TELNYX_WEBHOOK_VERIFY` isn't `true` or `TELNYX_PUBLIC_KEY` is missing (production webhook verification **cannot** be disabled);
- `PUBLIC_BASE_URL` isn't `https://` (and production additionally refuses any tunnel/ngrok URL);
- `NOTIFY_MODE != relay_service`, `FETCH_MODE != agentverse`, `XAI_VOICE_MODE != live`, `TELEPHONY_MODE != live` (no silent mock adapters);
- `FINCHNODE_MODE` isn't `sandbox` (staging) / `production` (prod);
- any real service token or webhook secret is empty (no dev-token fallbacks).

Plus `missing_live_settings()` names every variable a live `*_MODE` flag needs (including the new `XAI_WEBHOOK_SIGNING_SECRET` and partial-Telnyx-fallback-profile detection). ~25 parametrized tests pin all of this.

## 1.12 Deployment scaffold (tasks 20/27)

- `deploy/docker-compose.prod.yml`: persistent, restart-on-failure services — `db` (Postgres 16, health check), `core` (boots `APP_ENV=prod` → fail-closed), `relay`, `agents` (public orchestrator + internal specialists), `jobs` (the worker), `backup` (profile-gated daily `pg_dump` with `BACKUP_KEEP_DAYS` rotation via `deploy/backup.sh`).
- `docs/DEPLOYMENT.md`: what deploys, backups/encryption-at-rest/least-privilege ops, restore-testing procedure, observability wiring.
- `.env.staging.example` / `.env.production.example`: complete, documented templates — every secret intentionally empty.

## 1.13 Testing (tasks 30/31)

- The audited payer-phone allowlist regression: **fixed** (branch commit `82d47df`); nothing was hidden by editing tests.
- At HEAD: **310 pytest** + **43 vitest** + ruff + tsc + migration tests + smoke E2E — all green.
- **New test files this overhaul:** `test_assistant_service.py` (10), `test_assistant_api.py` (7), `test_identity.py` (6), `test_human_merge_states.py` (6), `test_jobs_and_metrics.py` (7), `test_sip_attach.py` (2), `test_failure_matrix.py` (7), `test_claim_logic_parallel.py` (2), ~25 new environment-validation cases in `test_audit_and_config.py`, and Relay `naturalOnboarding.test.ts` (3).
- **Failure matrix now covers (all deterministically, offline):** duplicate webhook, duplicate approval (409 `not_awaiting_approval`, cannot mint a second authorization), duplicate call start, duplicate xAI attach webhook, duplicate sensitive-request merge, agent timeout, agent failure, Finch outage (502, consent preserved), xAI/Telnyx outage (loud 502, case safe), Relay outage (case never breaks), Agentverse disconnect (transcript persisted, 502 fails closed), revoked consent, ambiguous identity, wrong case id, malicious agent sender, unauthorized call, stale state, unreachable-patient merge timeout, PHI-leak on the assistant. **No real patient PHI in any test.**

## 1.14 Docs (tasks 33/34)

README rewritten as a truthful shipping document (purpose, architecture, public-vs-internal agents, patient + dispute workflows, security model, environments, config, testing, Agentverse setup, honest limitations — no claims of "four public agents", "LLM-trained database", "verified live voice calling", or "24/7 availability"). SECURITY.md gained the identity model + 17A merge model. STATE_MACHINE.md gained the merge sub-state diagram. DEPLOYMENT.md gained the deploy table, backup/restore and observability ops. `AUDIT_REPORT.md` deleted.

---

# PART 2 — THE CURRENT ARCHITECTURE, TOP TO BOTTOM

```
                              ┌───────────── patients ─────────────┐
                              │                                     │
                       Relay chat (text,                    ASI:One chat
                       voice memo, buttons)                  (public agent)
                              │                                     │
                              │ X-Amyna-Patient-Token               │ typed uAgents msgs
                              ▼                                     ▼
        ┌────────────────────────────── Core (FastAPI) ──────────────────────────────┐
        │ SINGLE SOURCE OF TRUTH: case state machine + guards, consent ledger,      │
        │ user/session/finch-link ownership, append-only audit, scoped tools,       │
        │ telephony ownership, durable jobs, metrics                                 │
        │                                                                             │
        │ /assistant/*        stateless language layer (candidates only)             │
        │ /auth/session       patient sessions (demo; production = your IDP)         │
        │ /internal/tools/*   22 scoped tools; get_patient_context = only PHI door   │
        │ /internal/jobs/drain  /internal/metrics     worker + ops                   │
        │ voice manager: dial guards, 17A merge states, SIP attach, guardrails       │
        └──────────┬──────────────────────────────────┬───────────────────────────────┘
                   │ fetch bridge (local_direct in-proc │ agentverse REST /trigger)
                   │                                    │
        ┌──────────▼───────────┐            ┌──────────▼───────────┐
        │ amyna-orchestrator   │── typed ──▶ │ internal specialists │
        │ (PUBLIC — the only   │   msgs     │ Visit / Claim /       │
        │  published agent)    │◀── replies ─│ Dispute (never pub.) │
        └──────────────────────┘            └──────────┬───────────┘
                                                       │ results via /internal/tools/*
        Core ──▶ FinchNode (patient-authorized FHIR records; every read consent-gated)
        Core ──▶ Telnyx (dial / bridge / hangup; Ed25519-signed webhooks in)
        Telnyx ──▶ sip:{number}@sip.voice.x.ai (xAI answers; carrier bridges audio)
        xAI ──▶ signed realtime.call.incoming ──▶ Core attaches realtime WS (?call_id=)
        Core ──▶ Relay /internal/notify (every patient notification, rendered once)
```

## 2.1 The complete API surface (53 endpoints, from the committed OpenAPI)

**Patient-facing (Relay service token + patient session token):**
`POST /cases` · `GET /cases/{id}` · `POST /cases/{id}/consents` · `GET /cases/{id}/notifications` · `GET /cases/{id}/timeline` · `POST /claims` · `GET /claims/{id}` · `POST /claims/{id}/analyze` · `POST /disputes` · `GET /disputes/{id}` · `POST /disputes/{id}/approve` · `POST /disputes/{id}/reject` · `POST /human-intervention` · `GET /human-intervention/{id}` · `POST /human-intervention/{id}/resolve` · `POST /visits` · `POST /visits/{id}/complete` · `GET /visits/{id}/evidence` · `POST /assistant/understand` · `POST /assistant/explain` · `POST /auth/session` · `POST /voice/start` · `GET /voice/{call_id}` · `POST /voice/{call_id}/event` · `POST /voice/{call_id}/human-handoff`

**Signed webhooks (no bearer; the signature IS the auth):**
`POST /relay/webhook` (HMAC-SHA256, `X-Amyna-Timestamp` / `X-Amyna-Signature`) · `POST /voice/telnyx/webhook` (Ed25519) · `POST /voice/xai/webhook` (Standard-Webhooks-style HMAC, replay-windowed)

**Ops (FETCH identity):** `POST /internal/jobs/drain` · `GET /internal/metrics` · `GET /health`

**Agent tools (22, each its own `tools:<name>` scope):** `find_or_create_case`, `get_case`, `record_consent`, `ingest_claim`, `get_case_claim`, `get_visit_transcript`, `record_visit_evidence`, `get_supporting_evidence`, `get_patient_claims`, `get_patient_encounters`, `get_patient_procedures`, `get_patient_medications`, `record_findings`, `get_claim_line`, `get_dispute_case`, `record_dispute`, `record_patient_decision`, `resolve_human_intervention`, `request_human`, `start_insurance_call`, `record_insurer_response` (voice identity only), `get_patient_context`

## 2.2 Input lifecycle 1 — a chat message (Relay)

1. Platform event → Relay webhook (HMAC-verified, ledger-deduped by event id, per-patient serialization).
2. `patientScope` resolves/creates the patient session (`POST /auth/session` in dev/test/staging; the token lives in memory only).
3. Stage router (`new → awaiting_patient_id → awaiting_data_consent → awaiting_recording_choice → recording → processing_visit → ready_for_bill → awaiting_claim_id → in_review → done`).
4. Free text without an ID → `POST /assistant/understand` → one plain-English reply (+ gentle ID guidance only when needed); typed answers normalize to buttons (`yes/no/not sure/approve/reject/review/done/cancel`).
5. With a record code: `POST /cases` (owner-bound) → consent card → recording offer → memo or skip → claim number → claim ingest → analysis → confirmation questions one at a time (YES / NO / NOT SURE buttons) → the dispute letter shown verbatim → explicit APPROVE/REJECT → the call.
6. Core notifications flow to Relay (`/internal/notify`, service-token auth) and render exactly once; button replies return as signed events.

## 2.3 Input lifecycle 2 — a voice memo (Relay)

Audio → transcription (live xAI STT / deterministic mock) → `POST /visits/{id}/complete` → transcript persisted atomically → fetch bridge → Visit agent logic (rules-based extractor; an optional local Qwen/Ollama path is retained as the deterministic/fallback extractor) → evidence recorded → case `EVIDENCE_EXTRACTED`. If the orchestrator is unreachable: 502 `orchestrator_unreachable`, the transcript stays stored, and the retry is safe (tested).

## 2.4 Input lifecycle 3 — the bill (FinchNode claim)

`POST /claims` (consent-gated FinchNode read; the claim is matched by external id) → claim + lines persisted → `POST /claims/{id}/analyze` → Claim agent: **concurrent** encounter/procedure/medication pulls → discrepancy engine + CPT/HCPCS resolver + hospital price index + policy RAG (five policy docs incl. CA patient billing rights) → findings (category, evidence refs, confidence, explanation, `patient_facing_explanation`, recommended action, optional confirmation question) → `record_findings` → questions delivered one at a time → `FINDINGS_CONFIRMED`.

## 2.5 Input lifecycle 4 — an ASI:One chat

`amyna-orchestrator` (the only public agent; manifest published; every ChatMessage acknowledged) → plain-language onboarding → typed uAgents requests (`VisitEvidenceRequest`, `ClaimAnalysisRequest`, `DisputeRequest`, correlated by `request_id`; a 60s timeout answers with an error) to internal specialists (sender-locked — strangers get nothing) → all private work through scoped `/internal/tools/*` → results written to Core → plain-English summaries → an explicit APPROVE gate before any call.

## 2.6 Input lifecycle 5 — the insurer call (after explicit patient approval)

1. Dispute Agent → `start_insurance_call` tool. Core guards: APPROVED dispute + stored patient authorization + payer number inside `TELEPHONY_ALLOWED_NUMBERS` (state-machine enforced; a second call start is refused).
2. Telnyx dial (`client_state` carries the call id; three fallback profiles rotate on auth/credit failures).
3. Carrier bridges to `sip:{number}@sip.voice.x.ai` → xAI answers → **signed `realtime.call.incoming` webhook** → Core verifies the signature, correlates via the forwarded `X-Amyna-Call-Id` SIP header, and `attach_sip_session` starts the realtime WS with `?call_id=` (idempotent; failures hang up and close the case FAILED).
4. Every insurer utterance → `detect_sensitive_request` (in code); every agent utterance → `check_agent_utterance` (only codes/amounts/medical terms grounded in the case's evidence; violations are interrupted with the safe fallback line); model tool calls → the scoped dispatcher with case/call ids injected (a prompt-injected lookup of any other case is impossible).
5. A sensitive ask → the 17A merge lifecycle (Part 1.4).
6. `record_insurer_response` → the call ends → case `RESOLVED` → a final plain-language report to the patient (what was removed, what's itemized, the reference number).

## 2.7 Account creation & identity (end to end)

A platform user appears → Relay requests a session (`POST /auth/session`; Core stores only the SHA-256 hash) → the token is forwarded on every patient call → new cases bind to that user → health-data consent creates the `finch_links` binding (audited) → all record reads are consent-gated tools against the linked identity → cross-user access is `case_forbidden` → production requires a real IDP and refuses tokenless case creation. A typed `fn_pat_…` is an identifier, never a credential.

## 2.8 Data & state machines

- **DB:** SQLite locally; PostgreSQL enforced in staging+prod. Migrations **0001–0007**: initial schema → human call leg → merge phase → users/sessions/finch-links/case-owner → jobs → patient_facing_explanation → xai_call_ref.
- **Case states (16):** `CREATED → VISIT_RECORDED → TRANSCRIBED → EVIDENCE_EXTRACTED → CLAIM_RECEIVED → ANALYZED → (AWAITING_PATIENT_INPUT) → FINDINGS_CONFIRMED → DISPUTE_PREPARED → AWAITING_APPROVAL → APPROVED → CALL_IN_PROGRESS ⇄ HUMAN_REQUIRED → RESOLVED | REJECTED | FAILED` — every transition guarded (`check_transition`), audited, and optimistic-locked (concurrent writers conflict instead of overwriting).
- **Call statuses:** `initiating, in_progress, paused_for_human, completed, failed` + the 7 merge phases riding `merge_phase`.
- **Audit:** append-only `audit_events` for every tool call, transition, consent, decision, job event, notification, and call event — details PHI-redacted before insert.

## 2.9 Security model (exact)

- **AuthN:** per-service bearer tokens (constant-time compare) — RELAY (17 scopes incl. `assistant:use`), FETCH (`cases:read`, `jobs:run`, `metrics:read` + all tools except `record_insurer_response`), VOICE (`voice:read/events/handoff` + 6 voice tools). Patient sessions: opaque tokens, hashed at rest, ownership-enforced.
- **AuthZ:** deterministic only — state-machine guards, consent gates (`ConsentRequired`), tool allowlists per caller AND per agent, the dial allowlist, sensitive-data rejection (`SensitiveDataRejected` — Core refuses to store secrets typed into any free text).
- **Webhooks:** all three directions signed and verified (Relay HMAC-SHA256 with a replay window; Telnyx Ed25519; xAI standard-webhooks style with a replay window). Production cannot disable any of them (startup error).
- **PHI discipline:** the PHI-redacting log filter + `redact()` on all audit details; metrics are counts only; the language layer is stateless and candidate-only; tests assert SSNs never round-trip.

---

## Tests run (at HEAD)

- `pytest`: **310 passed** (unit, state machine, security policy, migrations 0001–0007, assistant, identity, jobs/metrics, SIP attach, failure matrix, offline Bureau uAgents over a real Core)
- Relay `vitest`: **43 passed**; Relay TS lint (`tsc`): clean
- `ruff check` + `ruff format`: clean
- `scripts/smoke_e2e.py` (full mock E2E through the real Core HTTP API, both the Relay and ASI:One paths): **PASSED**
- No real patient PHI is used in any test, ever.

## Live integrations verified

- **Doc-verified against current official docs (this PR):** xAI realtime endpoint/auth/session schema/voice names; xAI's SIP-inbound attach flow (`realtime.call.incoming` → `?call_id=`); Telnyx webhook verification contract (headers, Ed25519, raw body, replay window, ack semantics).
- **Deliberately NOT live-called (to respect paid-quota discipline):** no live xAI/Telnyx call, no live FinchNode/Relay platform/Agentverse round-trip. Every remaining unknown is enumerated in `docs/VERIFY_LIST.md` and gated until one supervised staging pass.

---

# PART 3 — LIMITATIONS & THE EXACT PATH TO "ANYONE IN THE WORLD, FULLY HANDS-OFF"

Each blocker below lists: **the problem → why it blocks → a concrete, compatible solution (fits this codebase as-is or with a small contained change) → its checklist item.** Every solution was chosen because it fits an interface, flag, or table that already exists in this repo — nothing listed requires rearchitecting anything. The consolidated launch checklist at the end is the runbook.

### A. Vendor accounts & first live verification

**1. No live call has ever been placed.** Every live path is implemented and doc-verified, but zero real xAI/Telnyx/FinchNode/Relay-platform/Agentverse traffic has occurred (deliberate, to respect paid-quota discipline).
*Why it blocks:* production claims must be truthful; the remaining ☐ VERIFY items (exact transcript/function-call event names; carrier header propagation; Telnyx field-name parity vs their consolidated OpenAPI) can only be closed by a real call.
*Compatible solution:* the repo already ships `.env.staging.example` + the staging runbook. Fill it with sandbox FinchNode (`ck_test_…`), a test Telnyx number, the xAI-registered SIP number, and run the documented loop once; tick each ☐ in `docs/VERIFY_LIST.md` as confirmed; fix any event-name deltas in `core/app/adapters/xai_voice/live.py` (one constants block).
- ☐ create xAI API key + register the SIP number (save the returned signing secret) · create the Telnyx app + buy a test number + fetch the public key · obtain FinchNode sandbox keys · configure the Relay platform webhook · run one supervised staging pass · tick every ☐ in VERIFY_LIST.

**2. FinchNode production surface is unverified.** The `production` client mode exists with key-prefix guards, but FinchNode's Connect/consent flow, webhooks, and enabled-app base URL await their approval.
*Compatible solution:* zero code change expected — `finchnode/client.py` already isolates mode/base-url/auth; when their docs land, adjust the single `PRODUCTION_BASE_URL` constant and follow their Connect flow into the existing `finch_links` + consent machinery.
- ☐ submit/complete the FinchNode application · confirm the production base URL + Connect flow · re-run the sandbox tests against their staging API.

**3. Agentverse publish is manual.** `amyna-orchestrator` must be registered with the team seeds; specialists must stay private.
*Compatible solution:* `make agents-hello` validates discoverability (HELLO_WORLD mode) with no code change; keep `VISIT_/CLAIM_/DISPUTE_AGENT_ADDRESS` sender-locking as-is (tested — strangers get no reply).
- ☐ publish the orchestrator with the committed profile/readme · confirm the ASI:One "Hello from Amyna" reply · never publish specialist addresses.

### B. Identity & accounts

**4. No production identity provider.** Sessions are real and hashed, but `/auth/session` self-issues demo tokens only outside production.
*Why it blocks:* real patients need real sign-in, recovery, and caregiver/proxy rules.
*Compatible solution (small, contained):* bring an OIDC provider (Auth0, AWS Cognito, or Google Sign-In — or the Relay platform's own verified-user identity). In `identity_service.py`, add one JWT-verification path (`resolve_session_user` accepting an RS256 JWT verified against the provider's JWKS, mapping `sub` → `users.auth_subject`). The `X-Amyna-Patient-Token` header contract, the ownership checks, and every test stay unchanged — Core simply accepts a second token type.
- ☐ choose + configure the IDP · add JWKS verification to `identity_service` · add phone/email verification at signup · define caregiver/proxy access (a `finch_links`-style consent row) · re-run the identity tests + add IDP ones.

**5. No account-lifecycle surfaces.** No signup UI beyond chat, no consent-management page, no GDPR/CCPA export/delete.
*Compatible solution:* Core already exposes everything needed: `GET /cases/{id}/timeline` is a complete audited record; a small self-service route set (`/account/export`, `/account/delete`) that walks `cases → consents → findings → disputes → calls` is a contained addition over the existing ownership checks.
- ☐ build account self-service (export/delete) · publish the privacy policy + terms.

### C. Compliance & legal

**6. HIPAA readiness.** No BAAs signed yet (xAI's docs state HIPAA eligibility with a BAA — confirm and sign; Telnyx and the hosting provider must be asked), no security risk assessment, no breach runbook.
*Compatible solution:* the code side already provides the audit trail, access controls, PHI-redacted logging, and consent ledger a HIPAA program needs; what's missing is paperwork and vendor agreements, not architecture.
- ☐ sign BAAs (xAI, Telnyx, cloud/DB host) · run a security risk assessment · write the breach-response runbook · appoint privacy/security owners · Vanta or Drata for continuous SOC 2 evidence · commission a pen test.

**7. Recording + calling law.** Visit memos and insurer calls touch two-party-consent rules; outbound calls touch TCPA-style rules.
*Compatible solution:* the recording-consent text is already captured verbatim per visit; add a per-state/country consent matrix consulted at `POST /visits` (the consent ledger already stores the exact wording), and keep the call model patient-initiated (the patient explicitly authorizes each call — the explicit APPROVE gate stays untouched; that's also the strongest TCPA posture).
- ☐ build the jurisdiction consent matrix · legal review of the call flow per target market · publish call-recording disclosures.

**8. Terms, disclaimers, regulatory.** No "not medical/legal advice" disclaimers yet; insurance-consumer rules vary by market.
- ☐ ToS + privacy policy + disclaimers drafted by counsel · market-by-market regulatory review before each launch region.

### D. Product gaps

**9. Bill-photo intake isn't wired.** `extract_bill_information` exists in the language layer, but Relay doesn't yet accept images end-to-end.
*Compatible solution:* the docs we fetched confirm the same xAI API does image understanding — add `POST /assistant/extract-bill` accepting an image, run it through xAI vision with the existing `BillExtraction` schema as the structured output, behind the SAME `LanguageEngine` boundary (a vision-backed implementation). Relay then accepts an attachment and shows the *"I found a bill from Memorial Hospital for $842.60 — is this the one?"* confirmation exactly as the brief describes. No auth/model changes needed.
- ☐ add the image endpoint + vision path behind the existing protocol · extend Relay's inbound handler for attachments · test with a fixture image.

**10. The "LLM" is a deterministic heuristic engine.** Free, private, and testable — but not robust to the full range of human phrasing.
*Compatible solution:* implement `XaiLanguageEngine` (grok chat completions with structured outputs) behind the EXISTING `LanguageEngine` protocol — the module was explicitly designed for this slot; every consumer and test keeps working because the I/O schemas are frozen. Keep the heuristic engine as the offline/fallback path (already the architecture).
- ☐ implement the engine · add a contract test pinning the same I/O · feature-flag it per environment.

**11. English-only.**
*Compatible solution:* all copy is already centralized (`relay/src/ux/messages.ts` + Core's `_GLOSSARY` + `compose_reply` templates) — mechanical to catalog; run the engine's glossary through translation per locale.
- ☐ extract message catalogs · localize (start: Spanish) · locale negotiation on the session.

**12. Accessibility / no inbound phone channel.** An elderly user can RECEIVE the human-merge call but cannot CALL Amyna; chat requires typing.
*Compatible solution:* Telnyx inbound is already half-built — `/voice/telnyx/webhook` handles `call.initiated/answered/hangup` for outbound legs; add an inbound profile that starts an xAI realtime session (the same `?call_id=` attach, but on an inbound number) with the SAME guardrails and the SAME state machine, then link the call to a case by phone → user. The voice-first channel reuses everything: consent capture via spoken yes/no, the dispute flow, the merge.
- ☐ buy an inbound number · implement the inbound `call.initiated` path (contained: one new dispatch branch in `telnyx_service._dispatch`) · speech yes/no consent · WCAG audit of any web UI.

**13. No human-support fallback.** `notUnderstood` loops back to buttons.
*Compatible solution:* the intervention machinery already models human queues (`request_human` + open interventions) — surface an operator console (read-only over existing endpoints: `GET /cases/{id}`, `/timeline`, open interventions) and route stuck cases there; the escalation path already exists (`HumanReason.CALL_ESCALATION`).
- ☐ build the minimal operator view · define the support SLA.

**14. Static payer allowlist.** Core dials only `TELEPHONY_ALLOWED_NUMBERS`.
*Compatible solution:* replace with a DB-backed verified payer directory (payer name → verified phone, per the claim's payer_name) maintained by an admin process; the enforcement point stays exactly where it is (`VoiceCallManager.start_call`), so the security property is preserved.
- ☐ build the payer directory table + admin seed · swap the config lookup for the table lookup behind the same guard · keep the allowlist as an emergency blocklist.

### E. Operations & scale

**15. Nothing is hosted.** The stack is scaffolded, not deployed.
*Compatible solution:* the committed `deploy/docker-compose.prod.yml` runs as-is on any Docker host (Hetzner/EC2/other cloud VM) behind Caddy/Traefik for TLS; or lift each service to managed platforms (Core + agents + jobs → Fly.io/Railway/Cloud Run; Postgres → Neon/RDS). The compose is already parameterized by env. Add a GitHub Actions workflow (build → image registry → deploy) and run `alembic upgrade head` as the release step (the migration chain 0001–0007 is already tested).
- ☐ provision host + managed Postgres + DNS/TLS · wire the CI/CD deploy · automate migrations on release.

**16. Single-instance constraints.** Realtime voice sessions live in the Core process (`XaiRealtimeVoiceAdapter._calls`); Relay stores conversations in a local JSON file; recordings sit on local disk (`RELAY_DATA_DIR`).
*Compatible solution:* all three are behind interfaces — (a) run a dedicated single-replica "voice" Core deployment (realtime sessions are per-call and short-lived; the pragmatic first step) and put stateless API replicas in front; (b) swap `ConversationStore` for a Postgres-backed implementation (the class is already an injected dependency with a tiny surface: get/save/reset/byCase); (c) upload memos/recordings to S3-compatible storage with presigned URLs (the transcriber already consumes URLs).
- ☐ Postgres-backed Relay store · S3 recordings · dedicated voice replica + stateless front replicas (or a Redis-backed session registry if multi-replica voice is ever needed).

**17. Jobs are an in-repo durable pattern, not a managed broker.**
*Compatible solution:* `job_service` is deliberately queue-agnostic (rows + a drain function) — point the same schema at Redis/RQ or SQS by replacing only the worker's transport; handlers are already idempotent and retry-safe. Keep the `jobs` table as the durable ledger either way.
- ☐ choose a broker · swap the worker transport · load-test the drain tick.

**18. Observability is internal-only.**
*Compatible solution:* `/internal/metrics` is JSON today — either add a Prometheus-text variant behind the same scope (one renderer over the same counters) or use a JSON exporter; ship the already-PHI-redacted JSON logs to your aggregator; Sentry SDK for errors; an uptime probe on `/health`.
- ☐ Prometheus scrape + Grafana board · log shipping · Sentry · alerts on `queue_depth.dead > 0`, `stuck_cases_24h` growth, webhook-signature failures.

**19. Cost & abuse controls.** Every conversation spends real money (realtime voice by the minute, STT, telephony minutes, LLM tokens); there are no quotas, rate limits, CAPTCHA, or billing.
*Compatible solution:* middleware on `/assistant/*` + `/voice/start` with per-user quotas (the session identity already exists — enforcement is a table + one dependency); Stripe metered billing in front of Relay; nginx/Redis token-bucket rate limiting at the edge.
- ☐ per-user quotas + rate limiting · Stripe metered billing · abuse monitoring on the metrics that already exist.

**20. Secrets hygiene.** The synced local `.env` contains live-format credentials (an xAI key, three Telnyx profiles, an Agentverse JWT, a Relay token + webhook secret, a tunnel URL).
- ☐ ROTATE every one of them now · move production secrets into 1Password/Doppler/Vault · keep `.env` out of synced storage.

**21. Tuning + custody.** `HUMAN_MERGE_TIMEOUT_S` (120s), job backoff (5s·2^n), and webhook tolerance windows are sane defaults, not field-tuned; the `*_SEED` values are long-lived identity material (losing them changes agent addresses).
- ☐ field-tune the timeouts after staging · store seeds in the secrets manager · document seed-rotation consequences.

**22. Cosmetic.** Commit `be84bfb`'s message body has a shell-quoting artifact (`` `n ``) — content unaffected; fix only if history is ever rewritten deliberately.

### The honest bottom line on "hands-off for anyone"

The **code** is production-shaped: fail-closed environments, real identity, durable jobs, observability, a doc-verified voice path, and an exhaustive test suite. What stands between here and a globally usable, hands-off product is everything **around** the code: vendor accounts and one supervised live pass, a real identity provider, compliance sign-off (HIPAA/BAA, recording and calling law, privacy terms), hosting with HA storage and a real queue, a billing/abuse-control layer, multilingual + accessibility work (including an inbound phone channel), and the wiring for bill photos and a real model. **None of it requires rearchitecting the codebase** — each item slots into an interface, flag, or table that already exists — and production mode refuses to boot until every real integration is genuinely configured.

### The consolidated launch checklist (hands-off, global)

☐ Rotate all local credentials · provision host + managed Postgres + DNS/TLS + secrets manager · wire CI/CD deploy + release migrations · integrate the OIDC identity provider · sign BAAs + security risk assessment + breach runbook · jurisdiction consent matrix + legal review · publish `amyna-orchestrator` (specialists stay private) · register the xAI SIP number + save the signing secret · configure the Relay platform webhook · obtain FinchNode approval (sandbox first, then production) · swap Relay's store to Postgres + recordings to S3 · add the vision bill-photo path + the real LLM engine · add per-user quotas, rate limits, and Stripe metered billing · build the payer directory · add the inbound phone channel + localization · wire Prometheus/Grafana/Sentry/alerts · run one supervised staging pass and tick every ☐ in `docs/VERIFY_LIST.md` · SOC 2 evidence + pen test · launch region by region.

---

## Required human/vendor approvals

Rotate the local `.env` credentials · provision infra + secrets manager · integrate the production IDP · choose vendors (xAI SIP number, Telnyx app + number, FinchNode approval, Relay platform webhook, Agentverse publish) · the one supervised staging call · alerting ownership · legal/HIPAA sign-off before any real patient data.

---

## Plain-English summary of the changes

This pull request rebuilds the prototype into the real product. **What we changed, plainly:**

- **The assistant is now called Amyna everywhere a customer would see it** — the README, the chat app, the public AI-agent listing, and all the internal wiring between its services use the real name.
- **It now talks like a person.** You can open with *"this bill looks wrong"* and it answers helpfully instead of demanding codes, claim numbers, or member IDs. It understands everyday phrasing and explains insurance jargon the moment you ask — never the other way around.
- **It knows exactly who you are.** Every case belongs to your signed-in account, your session is a secret we store only scrambled, and nobody else can touch your case. A bill identifier is just a label — it never logs you in.
- **A new language brain understands what patients say, but it can never make decisions** — it suggests things, and the strict rulebook in the backend decides. It never sees your secrets, and it can only narrate facts the evidence already proved.
- **When the insurance company asks for anything private on a call, Amyna follows a proper state machine:** it instantly stops talking, rings your phone into the very same live call, stays silent while you handle it, and only takes back over when you say you're done — and if it can't reach you, it says so clearly and never pretends nothing happened.
- **We checked the phone and AI-voice companies' current instruction manuals directly** and rebuilt the calling wiring to match how their systems genuinely work today (including a signed handshake we hadn't implemented before). We also confirmed their webhook-signing rules match our code.
- **Long jobs now run on a durable to-do list** — they survive crashes, retry sensibly, and end in a clear "failed" state instead of disappearing when a browser tab closes.
- **The analysis pulls your three kinds of health records at the same time** instead of one after another, and if one source is down the others still land.
- **Every finding it shows you is written in plain words derived from the evidence.**
- **It's set up to run as ordinary always-on services with daily backups and a health dashboard**, and production mode refuses to start unless every real integration is genuinely configured — mock stand-ins can't leak into production, and webhook verification can't be switched off.
- **We fixed the one known bug from the audit first**, then added roughly sixty new tests — including deliberate disaster drills (duplicate approvals, crashed networks, wrong identities, offline phone companies, leaked-secrets checks) — and all 310 backend tests, 43 interface tests, the linters, the type-checkers, the database migrations, and a full end-to-end rehearsal pass.

**What's honestly still missing** is everything that needs a human and a budget, not more code: host it for real, swap in fresh passwords, connect a proper sign-in provider, buy the phone and AI accounts, make one supervised test call, add billing so strangers can't run up our phone bill, translate it, add an inbound phone line for people who don't type, let people photograph their bill, sign the health-data agreements, and get legal sign-off. The checklist above is exactly that path — and the app was built so none of those steps require rearchitecting anything: each one slots into an interface, a flag, or a table that already exists.

*The honest line: every claim in this PR was verified against the actual branch — the API surface, the tool registry, the state machines, the vendor docs, and the test counts were all pulled from the committed code. What we haven't done yet is listed, and none of it is hidden.*
