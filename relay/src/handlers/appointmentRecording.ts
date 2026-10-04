/**
 * Appointment recording: recording consent -> the patient sends a voice memo of the
 * appointment -> ingest audio, transcribe, and send the transcript to Core (which
 * extracts evidence). In mock mode the recording "completes" immediately.
 */
import { BUTTONS, RECORDING_CONSENT_TEXT, copy } from "../ux/messages";
import type { Conversation } from "../store";
import { ingestRecording } from "./audioIngest";
import { type HandlerContext, requireCase, say } from "./context";
import { TranscriptionError } from "./transcription";

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
  convo.stage = "recording";
  if (!ctx.relay.simulatesRecording) {
    await say(ctx, convo, copy.sendVoiceMemo());
    return;
  }
  await ctx.relay.startRecording(convo.userId);
  await say(ctx, convo, copy.recordingStarted);
  await onRecordingCompleted(ctx, convo);
}

/** While waiting for the voice memo: Skip moves on, anything else repeats the ask. */
export async function awaitVoiceMemo(
  ctx: HandlerContext,
  convo: Conversation,
  choice: string | undefined,
): Promise<void> {
  if (choice === BUTTONS.recordSkip.id || choice === "NO") {
    await say(ctx, convo, copy.readyForBill());
    convo.stage = "ready_for_bill";
    return;
  }
  await say(ctx, convo, ctx.relay.simulatesRecording ? copy.recordingStarted : copy.sendVoiceMemo());
}

export async function onRecordingCompleted(
  ctx: HandlerContext,
  convo: Conversation,
  recordingUrl?: string,
  mimeType?: string,
): Promise<void> {
  if (!convo.recordingConsentId) throw new Error("recording completed without recording consent");
  if (!convo.visitId) {
    if (recordingUrl) await say(ctx, convo, copy.transcribing);
    convo.recordingRef = await ingestRecording(ctx, recordingUrl, mimeType);
  }
  // Transcribe before creating the visit: Core allows one visit per case, so an
  // unusable memo must not claim it.
  let transcript;
  try {
    transcript = await ctx.transcriber.transcribe(convo.recordingRef ?? "");
  } catch (error) {
    if (!(error instanceof TranscriptionError)) throw error;
    ctx.log(`transcription failed: ${error.message}`);
    convo.recordingRef = undefined;
    await say(ctx, convo, copy.transcriptionFailed(error.message));
    return;
  }
  if (!convo.visitId) {
    const visit = await ctx.core.createVisit({
      case_id: requireCase(convo),
      recording_consent_id: convo.recordingConsentId,
      recording_ref: convo.recordingRef ?? "",
    });
    convo.visitId = visit.visit_id;
    ctx.store.save(); // a retry must not create a second visit
  }
  // Core makes this retry-safe: an already-stored transcript just re-runs extraction.
  const result = await ctx.core.completeVisit(convo.visitId, transcript);
  const noted = result.evidence.map((e) => e.description);
  await say(ctx, convo, copy.transcriptReady(noted));
  convo.stage = "ready_for_bill";
}
