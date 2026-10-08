/** Entry point for every patient event from the Relay platform. */
import { CoreError } from "../coreClient";
import type { Conversation } from "../store";
import { BUTTONS, copy } from "../ux/messages";
import type { InboundEvent } from "../webhook";
import {
  answerRecordingChoice,
  awaitVoiceMemo,
  checkVisitEvidence,
  onRecordingCompleted,
} from "./appointmentRecording";
import { answerOpenPrompt, captureClaimId, replyToNotification, reviewBill } from "./approvalFlow";
import { answerDataConsent, askDataConsent } from "./consent";
import { type HandlerContext, say, serialize } from "./context";

const PATIENT_ID = /\b(?:fn_pat_[A-Za-z0-9_]+|patient-[A-Za-z0-9_-]+|u_[A-Za-z0-9]+)\b/i;
const LOOSE_PATIENT_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{3,80}$/;

export function parsePatientId(text: string | undefined): string | undefined {
  const trimmed = text?.trim() ?? "";
  return trimmed.match(PATIENT_ID)?.[0] ?? (LOOSE_PATIENT_ID.test(trimmed) ? trimmed : undefined);
}

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

async function openCase(
  ctx: HandlerContext,
  convo: Conversation,
  patientId: string,
): Promise<void> {
  const created = await ctx.core.createCase({
    patient: { finchnode_patient_id: patientId },
    channel: "relay",
  });
  convo.finchnodePatientId = patientId;
  convo.caseId = created.case_id;
  await askDataConsent(ctx, convo);
}

async function start(ctx: HandlerContext, convo: Conversation): Promise<void> {
  if (convo.finchnodePatientId) {
    await say(ctx, convo, copy.welcomeBack(convo.finchnodePatientId));
    await openCase(ctx, convo, convo.finchnodePatientId);
    return;
  }
  await say(ctx, convo, copy.welcome);
  convo.stage = "awaiting_patient_id";
}

async function capturePatientId(
  ctx: HandlerContext,
  convo: Conversation,
  text: string | undefined,
): Promise<void> {
  const patientId = parsePatientId(text);
  if (!patientId) {
    await say(ctx, convo, "Please send your FinchNode patient ID (the one FinchNode assigned).");
    return;
  }
  const firstTime = !convo.finchnodePatientId;
  if (firstTime) await say(ctx, convo, copy.accountSaved(patientId));
  await openCase(ctx, convo, patientId);
}

/** One patient event; serialized per patient. Core 4xx errors become a patient message
 * (retrying would fail the same way); 5xx/network errors propagate so the platform retries. */
export function handleIncoming(ctx: HandlerContext, event: InboundEvent): Promise<void> {
  return serialize(event.from, async () => {
    try {
      await route(ctx, event);
    } catch (error) {
      // 5xx usually means "retry later", but FinchNode 404s arrive as 502
      // finchnode_unavailable. Retrying those kills the Relay listener.
      const patientFacing =
        error instanceof CoreError &&
        (error.status < 500 || error.code === "finchnode_unavailable");
      if (!patientFacing) throw error;
      ctx.log(`core rejected ${event.kind} (${error.status} ${error.code})`);
      await say(ctx, ctx.store.get(event.from), copy.coreRejected(error.code));
    } finally {
      ctx.store.save();
    }
  });
}

async function route(ctx: HandlerContext, event: InboundEvent): Promise<void> {
  let convo = ctx.store.get(event.from);
  const command = event.text?.trim().toLowerCase();
  if (command === "switch") {
    convo = ctx.store.clearAccount(event.from);
    await say(ctx, convo, copy.welcome);
    convo.stage = "awaiting_patient_id";
    return;
  }
  if (command === "restart") {
    convo = ctx.store.reset(event.from);
    await start(ctx, convo);
    return;
  }
  if (event.kind === "recording") {
    await onRecordingCompleted(ctx, convo, event.recordingUrl);
    return;
  }
  if (event.audio) {
    if (convo.stage !== "recording") return await say(ctx, convo, copy.audioNotExpected);
    ctx.log(`voice memo received (${event.audio.mimeType}, ${event.audio.sizeBytes ?? "?"} bytes)`);
    return await onRecordingCompleted(ctx, convo, event.audio.url, event.audio.mimeType);
  }
  const choice = event.buttonId ?? normalizeText(event.text);
  if (choice?.startsWith("ntf:")) {
    await replyToNotification(ctx, convo, choice, event.id);
    return;
  }
  switch (convo.stage) {
    case "new": {
      const patientId = parsePatientId(event.text);
      if (patientId && !convo.finchnodePatientId) {
        return await capturePatientId(ctx, convo, event.text);
      }
      return await start(ctx, convo);
    }
    case "awaiting_patient_id":
      return await capturePatientId(ctx, convo, event.text);
    case "awaiting_data_consent":
      return await answerDataConsent(ctx, convo, choice);
    case "awaiting_recording_choice":
      return await answerRecordingChoice(ctx, convo, choice);
    case "recording":
      return await awaitVoiceMemo(ctx, convo, choice);
    case "processing_visit":
      if (await checkVisitEvidence(ctx, convo)) return;
      return await say(ctx, convo, copy.stillProcessingVisit);
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
      convo = ctx.store.reset(event.from);
      return await start(ctx, convo);
  }
}
