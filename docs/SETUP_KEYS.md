# Going live: per-sponsor key checklist

Everything runs in mock mode with an empty `.env`. To go live, an owner fills in the
keys for **one** integration, flips **one** flag, and runs the check listed for it.
If a live flag is set without its keys, the process exits at startup with a message
naming the missing variable.

Before going live, confirm every item for that integration in
[VERIFY_LIST.md](VERIFY_LIST.md). Unverified sponsor details all live in adapter
files tagged `# VERIFY against current docs`.

---

## 0. Core service tokens (Member 1) — do this first for any shared deployment

| Create | Env var |
|---|---|
| Random token for the Relay service | `RELAY_SERVICE_TOKEN` |
| Random token for the Fetch agents | `FETCH_SERVICE_TOKEN` |
| Random token for voice/telephony callbacks | `VOICE_SERVICE_TOKEN` |
| Random HMAC secret for Relay service → Core events | `CORE_WEBHOOK_SECRET` |

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set `APP_ENV=prod` to disable the dev fallback tokens. All services read the same `.env`.

---

## 1. Fetch.ai — Agentverse + ASI:One (Member 3) — **do this first, it is the riskiest**

| Step | Env var / flag |
|---|---|
| Create an Agentverse account and an API key | `AGENTVERSE_API_KEY` |
| Generate four seeds (`python -c "import secrets; print(secrets.token_hex(32))"`) | `ORCHESTRATOR_SEED`, `VISIT_AGENT_SEED`, `CLAIM_AGENT_SEED`, `DISPUTE_AGENT_SEED` |
| Print the derived addresses: `make agent-addresses` | `VISIT_AGENT_ADDRESS`, `CLAIM_AGENT_ADDRESS`, `DISPUTE_AGENT_ADDRESS`, `ORCHESTRATOR_ADDRESS` (only needed with `run_all --processes`; the Bureau derives it) |
| Optional public handle for the orchestrator | `ORCHESTRATOR_HANDLE` |
| Discoverability check: `HELLO_WORLD=1`, then `make agents-hello`, connect the mailbox from the Agentverse inspector link, and chat with the agent in ASI:One | `HELLO_WORLD=1` |
| Full workflow through agents | `FETCH_MODE=agentverse`, `HELLO_WORLD=0`, then `make agents` |

Step-by-step: [`fetch/agentverse/README_AGENTVERSE.md`](../fetch/agentverse/README_AGENTVERSE.md).

---

## 2. FinchNode (Member 4)

| Step | Env var / flag |
|---|---|
| Keyless synthetic demo API | `FINCHNODE_MODE=demo`, `FINCHNODE_BASE_URL=<demo URL>` (VERIFY) |
| Sandbox: create a sandbox key (`ck_test_…`) | `FINCHNODE_MODE=sandbox`, `FINCHNODE_API_KEY`, `FINCHNODE_BASE_URL` |
| Check | `python -m finchnode.client --patient <id>` prints normalized records |

Core reads FinchNode only through `core/app/adapters/finchnode/*`, and only after a
`health_data_access` consent on the case.

---

## 3. Relay (Member 2)

| Step | Env var / flag |
|---|---|
| Create the Relay agent / app and get its token | `RELAY_AGENT_TOKEN` |
| Configure the webhook URL → `https://<public-host>/webhook/relay` and copy its signing secret | `RELAY_WEBHOOK_SECRET` |
| API base URL (if the SDK/API needs one) | `RELAY_API_BASE_URL` |
| Flip the Relay service to the platform | `RELAY_MODE=live` |
| Let Core push notifications to the Relay service | `NOTIFY_MODE=relay_service`, `RELAY_SERVICE_URL` |
| Check | `make relay`, then send "hi" from the Relay app; the service logs the verified event |

Expose the local service with a tunnel (for example `cloudflared tunnel --url http://localhost:3000`).

---

## 4. xAI Grok Voice (Member 1)

| Step | Env var / flag |
|---|---|
| Create an xAI API key with realtime voice access | `XAI_API_KEY` |
| Confirm the realtime endpoint, model and voice names | `XAI_REALTIME_URL`, `XAI_VOICE_MODEL`, `XAI_VOICE_NAME` (VERIFY) |
| Flip the flag | `XAI_VOICE_MODE=live` |
| Check | `python -m app.adapters.xai_voice.live --check` (from `core/`) opens a session, sends `session.update`, prints the server events, and closes |

---

## 5. Telephony — Twilio Elastic SIP → xAI SIP (Member 1)

| Step | Env var / flag |
|---|---|
| Twilio account SID and auth token | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` |
| Buy/verify a caller number | `TWILIO_FROM_NUMBER` |
| Create an Elastic SIP trunk and point its origination/termination at xAI's SIP bridge; copy the SIP URI xAI gives you | `XAI_SIP_URI` (VERIFY format) |
| Allowlist **only** your own test line | `TELEPHONY_ALLOWED_NUMBERS=+1…` |
| Flip the flag | `TELEPHONY_MODE=live` |

How the bridge works (skeleton in `core/app/adapters/telephony/twilio_sip.py`):

1. Core asks Twilio's REST API to create an outbound call to the insurer number
   (must be in the allowlist), with TwiML that `<Dial><Sip>`s the xAI SIP URI and
   passes `call_id` as a SIP header/parameter.
2. Media flows insurer ⇄ Twilio ⇄ xAI over SIP; audio never touches Core.
3. Core keeps a control WebSocket to the xAI realtime session for that call
   (instructions, tools, transcripts, tool calls).
4. Twilio status callbacks → `POST /voice/{call_id}/event` (voice token).

Fallback if SIP is not available: Twilio Media Streams to a Core WebSocket that
relays μ-law audio to the realtime API (not built; documented as an alternative).

---

## 6. Local Qwen3 via Ollama (Member 4, optional)

| Step | Env var / flag |
|---|---|
| Install Ollama and pull a model: `ollama pull qwen3:1.7b` | `OLLAMA_URL`, `OLLAMA_MODEL` |
| Flip the flag | `EXTRACTION_MODE=ollama` |
| Check | `python -m intelligence.extraction.qwen_ollama --demo` |

If Ollama is unreachable or returns invalid JSON, extraction falls back to the
deterministic rules extractor and logs a warning.
