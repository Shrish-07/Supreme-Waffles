/** Handlers end to end against a stub Core that mimics Core's notification flow. */
import { describe, expect, it } from "vitest";

import { loadConfig } from "../src/config";
import type { CaseDetail, PatientNotification, Schemas } from "../src/coreClient";
import type { CoreApi, HandlerContext } from "../src/handlers/context";
import { handleIncoming } from "../src/handlers/incomingMessage";
import { onCoreNotify } from "../src/handlers/humanHandoff";
import { MockTranscriber } from "../src/handlers/transcription";
import { MockRelayAdapter } from "../src/relayAdapter/mock";
import { ConversationStore } from "../src/store";
import type { InboundEvent } from "../src/webhook";

const CASE = "case_1";

class StubCore implements CoreApi {
  state = "CREATED";
  notes: PatientNotification[] = [];
  events: Schemas["RelayWebhookEvent"][] = [];
  dispute: Record<string, unknown> | null = null;
  calls: Record<string, unknown>[] = [];

  private note(kind: PatientNotification["kind"], text: string, extra: Partial<PatientNotification> = {}) {
    this.notes.push({
      notification_id: `ntf_${this.notes.length + 1}`,
      case_id: CASE,
      kind,
      text,
      created_at: new Date().toISOString(),
      ...extra,
    });
  }

  async createCase() {
    return { case_id: CASE } as Schemas["Case"];
  }
  async recordConsent(_caseId: string, body: Schemas["ConsentCreate"]) {
    return { consent_id: `cns_${body.consent_type}` } as Schemas["Consent"];
  }
  async createVisit() {
    return { visit_id: "vis_1" } as Schemas["Visit"];
  }
  async completeVisit() {
    this.state = "EVIDENCE_EXTRACTED";
    return { evidence: [{ description: "lorazepam (dose 1 mg)", negated: false }] } as Schemas["VisitEvidenceResult"];
  }
  async ingestClaim() {
    this.state = "CLAIM_RECEIVED";
    return { claim_id: "clm_1" } as Schemas["Claim"];
  }
  async analyzeClaim() {
    this.state = "AWAITING_PATIENT_INPUT";
    this.note("confirmation_question", "Did you receive anesthesia?", { intervention_id: "hin_1" });
    return {} as Schemas["ClaimAnalysisResult"];
  }
  async notifications() {
    return this.notes;
  }
  async getCase() {
    return { case_id: CASE, state: this.state, dispute: this.dispute, calls: this.calls, findings: [] } as unknown as CaseDetail;
  }
  async prepareDispute() {
    this.state = "AWAITING_APPROVAL";
    this.dispute = { dispute_id: "dsp_1", letter_text: "Dear insurer...", patient_authorization_id: null };
    this.note("approval_request", "Your dispute package is ready.", { dispute_id: "dsp_1" });
    return {} as Schemas["DisputePreparation"];
  }
  async startCall() {
    this.state = "HUMAN_REQUIRED";
    this.calls.push({ call_id: "call_1" });
    this.note("human_handoff", "The representative asked for your SSN.", {
      intervention_id: "hin_call",
      buttons: [
        { id: "DONE", label: "Done" },
        { id: "CANCEL", label: "Cancel call" },
      ],
    });
    return {} as Schemas["CallSession"];
  }
  async sendSignedEvent(event: Schemas["RelayWebhookEvent"]) {
    this.events.push(event);
    if (event.intervention_id === "hin_1") {
      this.state = "FINDINGS_CONFIRMED";
      this.note("status_update", "Thanks, preparing your dispute.");
    } else if (event.dispute_id === "dsp_1" && event.choice === "APPROVE") {
      this.state = "APPROVED";
      this.dispute = { ...this.dispute, patient_authorization_id: "hdc_1" };
    } else if (event.intervention_id === "hin_call" && event.choice === "DONE") {
      this.state = "RESOLVED";
      this.note("final_report", "Resolved. Reference MOCK-REF-48213.");
    }
    return { status: "processed", event_id: event.event_id } as Schemas["RelayWebhookResult"];
  }
  async resolveIntervention(): Promise<never> {
    throw new Error("the relay flow uses signed events");
  }
  async approveDispute(): Promise<never> {
    throw new Error("the relay flow uses signed events");
  }
  async rejectDispute(): Promise<never> {
    throw new Error("the relay flow uses signed events");
  }
}

function setup() {
  const core = new StubCore();
  const relay = new MockRelayAdapter();
  const ctx: HandlerContext = {
    config: loadConfig({}),
    core,
    relay,
    store: new ConversationStore(),
    transcriber: new MockTranscriber(),
    log: () => undefined,
  };
  let counter = 0;
  const send = (event: Partial<InboundEvent>) =>
    handleIncoming(ctx, { id: `evt_${(counter += 1)}`, kind: "message", from: "patient", ...event });
  const tap = (suffix: string) => {
    const card = [...relay.outbox].reverse().find((m) => m.message.buttons?.some((b) => b.id.endsWith(suffix)));
    const button = card?.message.buttons?.find((b) => b.id.endsWith(suffix));
    if (!button) throw new Error(`no button ${suffix}`);
    return send({ kind: "button", buttonId: button.id });
  };
  return { core, relay, ctx, send, tap };
}

describe("relay patient flow", () => {
  it("walks consent -> recording -> review -> question -> approval -> handoff -> report", async () => {
    const { core, relay, ctx, send, tap } = setup();
    await send({ text: "hi" });
    await send({ text: "my id is fn_pat_demo_001" });
    await send({ kind: "button", buttonId: "CONSENT_YES" });
    await send({ kind: "button", buttonId: "RECORD_YES" });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/lorazepam/);
    await send({ kind: "button", buttonId: "REVIEW_BILL" });
    await send({ text: "CLM-2026-0915-001" });
    const question = relay.outbox.at(-1)!.message;
    expect(question.text).toMatch(/anesthesia/);
    expect(question.buttons?.map((b) => b.label)).toEqual(["Yes", "No", "Not sure"]);

    await tap(":NO");
    expect(core.events[0]).toMatchObject({ intervention_id: "hin_1", choice: "NO", case_id: CASE });
    expect(relay.outbox.at(-1)?.message.buttons?.map((b) => b.label)).toEqual(["Review", "Approve", "Reject"]);

    await tap(":REVIEW");
    expect(relay.outbox.at(-1)?.message.text).toBe("Dear insurer...");
    await tap(":APPROVE");
    expect(core.calls).toHaveLength(1); // the service started the call after approval
    expect(relay.outbox.at(-1)?.message.text).toMatch(/SSN/);

    await tap(":DONE");
    expect(core.state).toBe("RESOLVED");
    expect(relay.outbox.some((m) => m.message.text.startsWith("Case summary (RESOLVED)"))).toBe(true);
    expect(ctx.store.get("patient").stage).toBe("done");
  });

  it("delivers each Core notification exactly once (push + pull)", async () => {
    const { core, relay, ctx, send } = setup();
    await send({ text: "hi" });
    await send({ text: "fn_pat_demo_001" });
    await send({ kind: "button", buttonId: "CONSENT_YES" });
    await send({ kind: "button", buttonId: "RECORD_SKIP" });
    await send({ kind: "button", buttonId: "REVIEW_BILL" });
    await send({ text: "CLM-2026-0915-001" });
    const shown = relay.outbox.length;
    await onCoreNotify(ctx, { notification: core.notes[0]!, case: { case_id: CASE, channel: "relay", finchnode_patient_id: "x" } });
    expect(relay.outbox.length).toBe(shown);
  });

  it("accepts typed answers for the open card and ignores unknown cases on push", async () => {
    const { core, ctx, send } = setup();
    await send({ text: "hi" });
    await send({ text: "fn_pat_demo_001" });
    await send({ text: "yes" });
    await send({ kind: "button", buttonId: "RECORD_SKIP" });
    await send({ text: "review my bill" });
    await send({ text: "CLM-2026-0915-001" });
    await send({ text: "No" });
    expect(core.events[0]?.choice).toBe("NO");
    const result = await onCoreNotify(ctx, {
      notification: { ...core.notes[0]!, case_id: "case_other" },
      case: { case_id: "case_other", channel: "asi1", finchnode_patient_id: "x" },
    });
    expect(result.delivered).toBe(false);
  });
});
