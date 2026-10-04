# VERIFY list

Every sponsor detail the scaffold could not confirm is isolated in an adapter and tagged
`# VERIFY against current docs` (or `// VERIFY` in TypeScript). Check each item against
the sponsor's **current** documentation before flipping that integration's `*_MODE` to live.

Find them all: `grep -rn VERIFY core/app fetch finchnode intelligence relay/src .env.example`

Legend: ☐ = still to verify · ☑ = verified while building the scaffold (how is noted).

## Fetch.ai — uAgents / Agentverse / ASI:One (Member 3)

| | Item | Where |
|---|---|---|
| ☑ | `uagents==0.25.5` exists on PyPI (0.26.0 is newer; re-verify before upgrading) | `fetch/requirements.txt` |
| ☑ | `uagents.Model` is `uagents_core.models.Model`, built on `pydantic.v1.BaseModel`; schema digests are identical with our shim (checked in the installed package; pinned in `agent_message_digests.json`) | `shared/python/claimshield_contracts/agent_messages.py` |
| ☑ | Chat protocol import path `uagents_core.contrib.protocols.chat` (`ChatMessage`, `ChatAcknowledgement`, `TextContent`, `EndSessionContent`, `chat_protocol_spec`, v0.3.0) | `fetch/agents/orchestrator.py:22` |
| ☑ | `Agent(..., mailbox=True, readme_path, description, handle, publish_agent_details)`, `include(..., publish_manifest=True)`, `on_rest_post`, `on_interval`, `ctx.storage` exist in 0.25.5; REST stays served when `mailbox=True`; a Bureau merges agents' REST handlers | `fetch/agents/orchestrator.py`, `core/app/adapters/fetch_bridge/live.py:4` |
| ☑ | Agents in one process dispatch locally (no Almanac needed); covered by `fetch/tests/test_uagents_bureau.py` | `fetch/agents/run_all.py` |
| ☐ | Agentverse inspector → **Connect mailbox** flow, and where the README/manifest appear | `fetch/agentverse/README_AGENTVERSE.md:34` |
| ☐ | Handle rules for `ORCHESTRATOR_HANDLE` (allowed characters, uniqueness, approval) | `README_AGENTVERSE.md:38`, `.env.example` |
| ☐ | ASI:One discovery: how a published agent becomes discoverable/searchable (README keywords? category?) | `fetch/agentverse/agent_profiles/orchestrator.md` |
| ☐ | Separate-process mode: mailbox agents resolving each other without explicit endpoints | `fetch/agents/run_all.py:18`, `README_AGENTVERSE.md:64` |
| ☐ | Whether ASI:One sends `StartSessionContent` / expects `EndSessionContent` (we end the session on terminal replies) | `fetch/agents/orchestrator.py` |
| ☐ | Real FinchNode patient-id format for chat parsing (assumed `fn_pat_…`) | `fetch/agents/intent.py:10` |

## FinchNode (Member 4)

| | Item | Where |
|---|---|---|
| ☐ | Demo (keyless) and sandbox base URLs | `finchnode/client.py:5`, `.env.example` |
| ☐ | Endpoint paths (`/fhir/Patient/{id}`, `/fhir/{ResourceType}?patient=`) and whether responses are FHIR R4 Bundles | `finchnode/client.py:76-77` |
| ☐ | Auth header for sandbox (`Authorization: Bearer ck_test_…`) and the key prefix | `finchnode/client.py:33,102` |
| ☐ | Pagination (`Bundle.link[relation=next]`) | `finchnode/client.py:138` |
| ☐ | Which resource types exist for claims (Claim vs ExplanationOfBenefit) and medications (MedicationAdministration vs MedicationStatement/Request); `normalize.py` already accepts all of them | `finchnode/normalize.py`, `finchnode/tools.py` |
| ☐ | Coverage → payer name/phone/member id location | `finchnode/normalize.py::normalize_patient` |
| ☐ | Whether an MCP server exists and is worth using | `finchnode/mcp_optional.md` |

## Relay (Member 2)

| | Item | Where |
|---|---|---|
| ☐ | Official SDK vs HTTPS API; npm package name (none is installed on purpose) | `relay/src/relayAdapter/live.ts:4` |
| ☐ | Send-message endpoint, quick-reply button payload format, auth header | `relay/src/relayAdapter/live.ts:26,36` |
| ☐ | Appointment recording support (start, consent prompt, completion callback), recording download auth | `relay/src/relayAdapter/live.ts:41,47` |
| ☐ | Webhook signature header names + algorithm (assumed `x-relay-timestamp` / `x-relay-signature`, HMAC-SHA256 over `ts.body`) | `relay/src/webhook.ts:11,33` |
| ☐ | Webhook event payload shape (assumed `{id, type, from, text, button, recording}`) | `relay/src/webhook.ts:64` |
| ☐ | Events via webhook or websocket (if websocket, add `subscribe()` to the live adapter) | `relay/src/relayAdapter/live.ts:4` |
| ☐ | Whether Relay transcribes recordings (else choose an STT provider) | `relay/src/handlers/transcription.ts:3` |
| ☐ | How a Relay user is linked to a FinchNode patient (demo asks the patient to type it) | `relay/src/handlers/incomingMessage.ts` |

## xAI Grok Voice (Member 1)

| | Item | Where |
|---|---|---|
| ☐ | Realtime WebSocket URL, model query parameter, auth header | `core/app/adapters/xai_voice/live.py:107,117`, `.env.example` (`XAI_REALTIME_URL`, `XAI_VOICE_MODEL`) |
| ☐ | Voice names (`XAI_VOICE_NAME`) | `.env.example` |
| ☐ | `session.update` schema: instructions, tools, tool_choice, turn detection, input transcription | `core/app/adapters/xai_voice/live.py:93` |
| ☐ | Event names: input transcription completed, output transcript done, function-call arguments done, error | `core/app/adapters/xai_voice/live.py:36` |
| ☐ | Function-call result format (`conversation.item.create` + `function_call_output`) and `response.create` | `core/app/adapters/xai_voice/live.py` |
| ☐ | How to force an exact utterance / interrupt (`response.cancel`, per-response instructions) | `core/app/adapters/xai_voice/live.py:172` |
| ☐ | SIP: whether xAI exposes a SIP endpoint, its URI format, how a call id is passed, and how the control WebSocket attaches to a SIP call | `core/app/adapters/telephony/twilio_sip.py:3,47`, `XAI_SIP_URI` |

## Twilio (Member 1)

| | Item | Where |
|---|---|---|
| ☐ | `<Dial><Sip>` to an external SIP URI from an Elastic SIP trunk / programmable voice number | `core/app/adapters/telephony/twilio_sip.py` |
| ☐ | Status callbacks: form-encoded + `X-Twilio-Signature` validation → needs a dedicated route (TODO noted) | `core/app/adapters/telephony/twilio_sip.py` docstring |

## Ollama / Qwen3 (Member 4)

| | Item | Where |
|---|---|---|
| ☐ | `/api/chat` with `format` = JSON schema (structured outputs) and `think: false` for Qwen3 | `intelligence/extraction/qwen_ollama.py:7` |
| ☐ | Model tag you pulled (`qwen3:1.7b` / `qwen3:0.6b`) | `.env.example` (`OLLAMA_MODEL`) |

## CMS hospital price transparency (Member 4)

| | Item | Where |
|---|---|---|
| ☐ | Current template version and column names (`standard_charge|gross`, `code|1|type`, …) and JSON keys | `intelligence/codes/hospital_ingest.py:7` |

## Data licensing (Member 4)

| | Item | Where |
|---|---|---|
| ☐ | CPT descriptors are AMA-licensed: the sample table uses paraphrases; do not bulk-load CPT text | `intelligence/codes/data/README.md` |
| ☐ | RxNorm ids in the sample table (lorazepam 6470, midazolam 6960) before any real use | `intelligence/codes/data/sample_codes.csv` |
