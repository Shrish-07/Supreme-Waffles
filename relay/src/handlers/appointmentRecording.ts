/**
 * Appointment recording: recording consent -> start recording -> on completion,
 * ingest audio, transcribe, and send the transcript to Core (which extracts evidence).
 * In mock mode the recording "completes" immediately.
 */
import { BUTTONS, RECORDING_CONSENT_TEXT, copy } from "../ux/messages";
import type { Conversation } from "../store";
import { ingestRecording } from "./audioIngest";
import { type HandlerContext, requireCase, say } from "./context";

export async function offerRecording(ctx: HandlerContext, convo: Conversation): Promise<void> {
  await say(ctx, convo, copy.recordingOffer());
  convo.stage = "awaiting_recording_choice";
}

export async function answerRecordingChoice(
  ctx: HandlerContext,
  convo: Conversation,
  choice: string | undefined,
): Promise<void> {
  if (choice === BUTTONS.recordSkip.id || choice === "NO") {
    await say(ctx, convo, copy.readyForBill());
    convo.stage = "ready_for_bill";
    return;
  }
  if (choice !== BUTTONS.recordYes.id && choice !== "YES") {
    await say(ctx, convo, copy.notUnderstood);
    return;
  }
  if (!convo.recordingConsentId) {
    const consent = await ctx.core.recordConsent(requireCase(convo), {
      consent_type: "recording",
      consent_text: RECORDING_CONSENT_TEXT,
      channel: "relay",
    });
    convo.recordingConsentId = consent.consent_id;
  }
  await ctx.relay.startRecording(convo.userId);
  convo.stage = "recording";
  await say(ctx, convo, copy.recordingStarted);
  if (ctx.relay.mode === "mock") await onRecordingCompleted(ctx, convo);
}

export async function onRecordingCompleted(
  ctx: HandlerContext,
  convo: Conversation,
  recordingUrl?: string,
): Promise<void> {
  if (!convo.recordingConsentId) throw new Error("recording completed without recording consent");
  if (!convo.visitId) {
    const recordingRef = await ingestRecording(ctx, recordingUrl);
    const visit = await ctx.core.createVisit({
      case_id: requireCase(convo),
      recording_consent_id: convo.recordingConsentId,
      recording_ref: recordingRef,
    });
    convo.visitId = visit.visit_id;
    convo.recordingRef = recordingRef;
    ctx.store.save(); // a retry must not create a second visit
  }
  const transcript = await ctx.transcriber.transcribe(convo.recordingRef ?? "");
  // Core makes this retry-safe: an already-stored transcript just re-runs extraction.
  const result = await ctx.core.completeVisit(convo.visitId, transcript);
  const noted = result.evidence.filter((e) => !e.negated).map((e) => e.description);
  await say(ctx, convo, copy.transcriptReady(noted));
  convo.stage = "ready_for_bill";
}
