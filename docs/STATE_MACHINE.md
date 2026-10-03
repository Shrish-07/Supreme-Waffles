# Case State Machine

Implementation: [`core/app/state_machine.py`](../core/app/state_machine.py) (pure, no DB).
Tests: [`core/tests/test_state_machine.py`](../core/tests/test_state_machine.py).

Every state change goes through `check_transition(current, target, GuardContext)`.
Illegal transitions raise `IllegalTransition` (HTTP 409, code `illegal_transition`).
Legal transitions whose guard fails raise `GuardFailed` (HTTP 409, code `guard_failed`).
The service layer builds the `GuardContext` from the database right before the change
and records every transition in the audit log.

```mermaid
stateDiagram-v2
    [*] --> CREATED
    CREATED --> VISIT_RECORDED: POST /visits
    CREATED --> CLAIM_RECEIVED: claim-only case
    VISIT_RECORDED --> TRANSCRIBED: POST /visits/{id}/complete
    TRANSCRIBED --> EVIDENCE_EXTRACTED: Visit Agent records evidence
    EVIDENCE_EXTRACTED --> CLAIM_RECEIVED: POST /claims
    CLAIM_RECEIVED --> ANALYZED: Claim Agent records findings
    ANALYZED --> AWAITING_PATIENT_INPUT: uncertain findings
    ANALYZED --> FINDINGS_CONFIRMED: nothing to confirm
    ANALYZED --> RESOLVED: no discrepancies
    AWAITING_PATIENT_INPUT --> FINDINGS_CONFIRMED: all answered, something to dispute
    AWAITING_PATIENT_INPUT --> RESOLVED: all answered, nothing to dispute
    FINDINGS_CONFIRMED --> DISPUTE_PREPARED: Dispute Agent records package
    DISPUTE_PREPARED --> AWAITING_APPROVAL: approval request sent
    AWAITING_APPROVAL --> APPROVED: patient approves
    AWAITING_APPROVAL --> REJECTED: patient rejects
    APPROVED --> CALL_IN_PROGRESS: start_insurance_call
    CALL_IN_PROGRESS --> HUMAN_REQUIRED: sensitive request / handoff
    HUMAN_REQUIRED --> CALL_IN_PROGRESS: intervention resolved
    CALL_IN_PROGRESS --> RESOLVED: insurer response recorded
    HUMAN_REQUIRED --> RESOLVED: insurer response recorded
    RESOLVED --> [*]
    REJECTED --> [*]
    FAILED --> [*]
    note right of FAILED
        Any non-terminal state may move to FAILED.
    end note
```

## Transition table

| From | To | Guard (enforced in code) |
|---|---|---|
| CREATED | VISIT_RECORDED | a visit exists |
| CREATED | CLAIM_RECEIVED | a claim exists |
| VISIT_RECORDED | TRANSCRIBED | a transcript exists |
| TRANSCRIBED | EVIDENCE_EXTRACTED | a transcript exists (evidence list may be empty) |
| EVIDENCE_EXTRACTED | CLAIM_RECEIVED | a claim exists |
| CLAIM_RECEIVED | ANALYZED | a claim exists |
| ANALYZED | AWAITING_PATIENT_INPUT | ≥ 1 open patient-confirmation intervention |
| ANALYZED | FINDINGS_CONFIRMED | 0 open confirmations and ≥ 1 disputable finding |
| ANALYZED | RESOLVED | 0 findings |
| AWAITING_PATIENT_INPUT | FINDINGS_CONFIRMED | 0 open confirmations and ≥ 1 disputable finding |
| AWAITING_PATIENT_INPUT | RESOLVED | 0 open confirmations and 0 disputable findings |
| FINDINGS_CONFIRMED | DISPUTE_PREPARED | a dispute exists |
| DISPUTE_PREPARED | AWAITING_APPROVAL | dispute status `awaiting_approval` |
| AWAITING_APPROVAL | APPROVED | dispute status `approved` (set only by a patient decision) |
| AWAITING_APPROVAL | REJECTED | dispute status `rejected` |
| APPROVED | CALL_IN_PROGRESS | dispute `approved` **and** `patient_authorization_id` present |
| CALL_IN_PROGRESS | HUMAN_REQUIRED | ≥ 1 open call-linked human intervention |
| HUMAN_REQUIRED | CALL_IN_PROGRESS | 0 open call-linked interventions **and** approval + authorization still present |
| CALL_IN_PROGRESS / HUMAN_REQUIRED | RESOLVED | an insurer response is recorded |
| any non-terminal | FAILED | always allowed |

"Disputable" = finding status `confirmed` or `needs_clarification`.
Terminal states: `RESOLVED`, `REJECTED`, `FAILED` (no outgoing transitions).

## Who triggers what

| Transition | Endpoint / tool |
|---|---|
| → VISIT_RECORDED | `POST /visits` (requires a `recording` consent) |
| → TRANSCRIBED → EVIDENCE_EXTRACTED | `POST /visits/{id}/complete` (Core runs the Visit logic via `fetch_bridge`) or tool `record_visit_evidence` |
| → CLAIM_RECEIVED | `POST /claims` or tool `ingest_claim` (FinchNode source requires `health_data_access` consent) |
| → ANALYZED → AWAITING_PATIENT_INPUT | `POST /claims/{id}/analyze` or tool `record_findings` |
| → FINDINGS_CONFIRMED | last confirmation answered (`POST /human-intervention/{id}/resolve`, signed `POST /relay/webhook`, or tool `resolve_human_intervention`) |
| → DISPUTE_PREPARED → AWAITING_APPROVAL | `POST /disputes` or tool `record_dispute` |
| → APPROVED / REJECTED | `POST /disputes/{id}/approve` · `/reject`, or tool `record_patient_decision` |
| → CALL_IN_PROGRESS | `POST /voice/start` or tool `start_insurance_call` |
| → HUMAN_REQUIRED | security policy hit on an insurer utterance, tool `request_human` with a `call_id`, or `POST /voice/{call_id}/human-handoff` |
| → RESOLVED | tool `record_insurer_response` then the call ends |
