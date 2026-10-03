## What does this change?

<!-- One or two sentences. Link the BRANCH_TASKS.md checklist item if relevant. -->

## Branch / ownership

- [ ] `main` (Core, contracts, CI) — Member 1
- [ ] `branch-1-relay` (relay/) — Member 2
- [ ] `branch-2-fetch` (fetch/, finchnode/, intelligence/) — Members 3 + 4

## Contract impact

- [ ] No changes under `shared/`
- [ ] Changes `shared/` contracts → bumped `CONTRACTS_VERSION` / `AGENT_PROTOCOL_VERSION`, ran `make contracts`, updated `docs/CONTRACTS.md`, and requested review from Member 1

## Safety checklist

- [ ] No real PHI, real member IDs, phone numbers, or credentials in code, fixtures, logs, or screenshots
- [ ] No new code path can start an insurer call without an APPROVED dispute + `patient_authorization_id`
- [ ] Sensitive-info requests (SSN / PIN / password / auth code / DOB verification) still route to `request_human`
- [ ] New sponsor API assumptions are isolated in an adapter and tagged `# VERIFY against current docs` (and listed in `docs/VERIFY_LIST.md`)

## Verification

- [ ] `make lint`
- [ ] `make test`
- [ ] `make smoke`
