/** Entry point for every patient event from the Relay platform. */
import type { Conversation } from "../store";
import { BUTTONS, copy } from "../ux/messages";
import type { InboundEvent } from "../webhook";
import { onRecordingCompleted, answerRecordingChoice } from "./appointmentRecording";
import { answerOpenPrompt, captureClaimId, replyToNotification, reviewBill } from "./approvalFlow";
import { answerDataConsent, askDataConsent } from "./consent";
import { type HandlerContext, say } from "./context";

const PATIENT_ID = /fn_pat_[A-Za-z0-9_]+/;
const TEXT_CHOICES: Record<string, string> = {
  yes: "YES",
  y: "YES",
  no: "NO",
  n: "NO",
  "not sure": "NOT_SURE",
  unsure: "NOT_SURE",
  approve: "APPROVE",
  reject: "REJECT",
  review: "REVIEW",
  done: "DONE",
  cancel: "CANCEL",
};

export function normalizeText(text: string | undefined): string | undefined {
  if (!text) return undefined;
  return TEXT_CHOICES[text.trim().toLowerCase().replace(/[.!]+$/, "")];
}

async function start(ctx: HandlerContext, convo: Conversation): Promise<void> {
  convo.stage = "awaiting_patient_id";
  await say(ctx, convo, copy.welcome);
}

async function capturePatientId(
  ctx: HandlerContext,
  convo: Conversation,
  text: string | undefined,
): Promise<void> {
  const patientId = text?.match(PATIENT_ID)?.[0];
  if (!patientId) {
    await say(ctx, convo, "Please send your FinchNode patient ID (it starts with fn_pat_).");
    return;
  }
  const created = await ctx.core.createCase({
    patient: { finchnode_patient_id: patientId },
    channel: "relay",
  });
  convo.finchnodePatientId = patientId;
  convo.caseId = created.case_id;
  await askDataConsent(ctx, convo);
}

export async function handleIncoming(ctx: HandlerContext, event: InboundEvent): Promise<void> {
  let convo = ctx.store.get(event.from);
  try {
    if (event.text?.trim().toLowerCase() === "restart") {
      convo = ctx.store.reset(event.from);
      await start(ctx, convo);
      return;
    }
    if (event.kind === "recording") {
      await onRecordingCompleted(ctx, convo, event.recordingUrl);
      return;
    }
    const choice = event.buttonId ?? normalizeText(event.text);
    if (choice?.startsWith("ntf:")) {
      await replyToNotification(ctx, convo, choice, event.id);
      return;
    }
    switch (convo.stage) {
      case "new":
        return await start(ctx, convo);
      case "awaiting_patient_id":
        return await capturePatientId(ctx, convo, event.text);
      case "awaiting_data_consent":
        return await answerDataConsent(ctx, convo, choice);
      case "awaiting_recording_choice":
        return await answerRecordingChoice(ctx, convo, choice);
      case "recording":
        return await say(ctx, convo, copy.recordingStarted);
      case "ready_for_bill":
        if (choice === BUTTONS.reviewBill.id || /review|bill/i.test(event.text ?? "")) {
          return await reviewBill(ctx, convo);
        }
        return await say(ctx, convo, copy.readyForBill());
      case "awaiting_claim_id":
        return await captureClaimId(ctx, convo, event.text);
      case "in_review":
        return await answerOpenPrompt(ctx, convo, choice, event.id);
      case "done":
        return await say(ctx, convo, copy.restart);
    }
  } finally {
    ctx.store.save();
  }
}
