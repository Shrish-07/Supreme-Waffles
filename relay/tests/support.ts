/** Test support: a stub Core that mimics Core's notification flow, plus a handler context. */
import { loadConfig } from "../src/config";
import type { CaseDetail, PatientNotification, Schemas } from "../src/coreClient";
import type { CoreApi, HandlerContext } from "../src/handlers/context";
import { handleIncoming } from "../src/handlers/incomingMessage";
import { MockTranscriber, type Transcriber } from "../src/handlers/transcription";
import { MockRelayAdapter } from "../src/relayAdapter/mock";
import { ConversationStore } from "../src/store";
import type { InboundEvent } from "../src/webhook";

export const CASE = "case_1";

export class StubCore implements CoreApi {
  state = "CREATED";
  notes: PatientNotification[] = [];
  events: Schemas["RelayWebhookEvent"][] = [];
  dispute: Record<string, unknown> | null = null;
  calls: Record<string, unknown>[] = [];
  ingested = 0;
  visits: Schemas["VisitCreate"][] = [];
  transcripts: Schemas["VisitComplete"][] = [];

  note(kind: PatientNotification["kind"], text: string, extra: Partial<PatientNotification> = {}) {
    this.notes.push({
      notification_id: `ntf_${this.notes.length + 1}`,
      case_id: CASE,
      kind,
      text,
      created_at: new Date().toISOString(),
      ...extra,
    });
  }

  async createCase(_body?: Schemas["CaseCreate"]) {
    return { case_id: CASE } as Schemas["Case"];
  }
  async recordConsent(_caseId: string, body: Schemas["ConsentCreate"]) {
    return { consent_id: `cns_${body.consent_type}` } as Schemas["Consent"];
  }
  async createVisit(body: Schemas["VisitCreate"]) {
    this.visits.push(body);
    return { visit_id: "vis_1" } as Schemas["Visit"];
  }
  async completeVisit(_visitId: string, body: Schemas["VisitComplete"]) {
    this.transcripts.push(body);
    this.state = "EVIDENCE_EXTRACTED";
    return { evidence: [{ description: "lorazepam (dose 1 mg)", negated: false }] } as Schemas["VisitEvidenceResult"];
  }
  async ingestClaim() {
    this.ingested += 1;
    this.state = "CLAIM_RECEIVED";
    return { claim_id: "clm_1" } as Schemas["Claim"];
  }
  async analyzeClaim() {
    if (this.state === "CLAIM_RECEIVED") {
      this.state = "AWAITING_PATIENT_INPUT";
      this.note("confirmation_question", "Did you receive anesthesia?", { intervention_id: "hin_1" });
    }
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

export function setup(options: { voiceMemos?: boolean; transcriber?: Transcriber; dataDir?: string } = {}) {
  const core = new StubCore();
  const relay = new MockRelayAdapter(false, !options.voiceMemos);
  const ctx: HandlerContext = {
    config: loadConfig(options.dataDir ? { RELAY_DATA_DIR: options.dataDir } : {}),
    core,
    relay,
    store: new ConversationStore(),
    transcriber: options.transcriber ?? new MockTranscriber(),
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
