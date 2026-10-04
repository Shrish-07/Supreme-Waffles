/**
 * Bill review: ingest + analyze, then let Core drive the conversation through its
 * notifications (one question at a time, approval card, handoff). Button replies
 * go back to Core as signed, idempotent events. This service only advances what
 * Core leaves to the interface: preparing the dispute and starting the call.
 */
import { randomUUID } from "node:crypto";

import type { Conversation } from "../store";
import { copy } from "../ux/messages";
import { type HandlerContext, requireCase, say } from "./context";
import { deliverPending } from "./humanHandoff";
import { sendCaseReport } from "./patientReport";

const CLAIM_NUMBER = /CLM-[A-Za-z0-9-]+/i;
const TERMINAL = new Set(["RESOLVED", "REJECTED", "FAILED"]);
const MAX_ADVANCE_STEPS = 6;

export async function reviewBill(ctx: HandlerContext, convo: Conversation): Promise<void> {
  convo.stage = "awaiting_claim_id";
  await say(ctx, convo, "Which bill? Send the claim number printed on it (e.g. CLM-2026-0915-001).");
}

export async function captureClaimId(
  ctx: HandlerContext,
  convo: Conversation,
  text: string | undefined,
): Promise<void> {
  const match = text?.match(CLAIM_NUMBER);
  if (!match) {
    await say(ctx, convo, "I couldn't find a claim number. It looks like CLM-2026-0915-001.");
    return;
  }
  const claim = await ctx.core.ingestClaim({
    case_id: requireCase(convo),
    source: "finchnode",
    finchnode_claim_id: match[0].toUpperCase(),
  });
  convo.claimId = claim.claim_id;
  convo.stage = "in_review";
  await say(ctx, convo, copy.reviewing);
  await ctx.core.analyzeClaim(claim.claim_id);
  await syncAndAdvance(ctx, convo);
}

/** A tap on a Core notification button (`ntf:<notificationId>:<CHOICE>`). */
export async function replyToNotification(
  ctx: HandlerContext,
  convo: Conversation,
  buttonId: string,
  eventId: string,
): Promise<void> {
  const [, notificationId, choice] = buttonId.split(":");
  const delivered = convo.delivered[notificationId ?? ""];
  if (!delivered || !choice) {
    await say(ctx, convo, copy.notUnderstood);
    return;
  }
  if (delivered.kind === "approval_request" && choice === "REVIEW") {
    const detail = await ctx.core.getCase(requireCase(convo));
    await say(ctx, convo, detail.dispute?.letter_text ?? "The dispute letter is not ready yet.");
    return;
  }
  await ctx.core.sendSignedEvent({
    event_id: eventId,
    event_type: "button_reply",
    case_id: requireCase(convo),
    notification_id: notificationId,
    intervention_id: delivered.interventionId ?? null,
    dispute_id: delivered.disputeId ?? null,
    choice,
    occurred_at: new Date().toISOString(),
  });
  await syncAndAdvance(ctx, convo);
}

/** Typed (non-button) reply while a Core prompt is open: map it onto the latest card. */
export async function answerOpenPrompt(
  ctx: HandlerContext,
  convo: Conversation,
  choice: string | undefined,
  eventId: string,
): Promise<void> {
  const open = Object.entries(convo.delivered)
    .filter(([, n]) => ["confirmation_question", "approval_request", "human_handoff"].includes(n.kind))
    .at(-1);
  if (!open || !choice) {
    await say(ctx, convo, copy.notUnderstood);
    return;
  }
  await replyToNotification(ctx, convo, `ntf:${open[0]}:${choice}`, eventId);
}

/** Show new Core notifications, then advance steps Core leaves to the interface. */
export async function syncAndAdvance(ctx: HandlerContext, convo: Conversation): Promise<void> {
  const caseId = requireCase(convo);
  for (let step = 0; step < MAX_ADVANCE_STEPS; step += 1) {
    await deliverPending(ctx, convo);
    const detail = await ctx.core.getCase(caseId);
    if (detail.state === "FINDINGS_CONFIRMED" && !detail.dispute) {
      await ctx.core.prepareDispute(caseId);
      continue;
    }
    if (
      detail.state === "APPROVED" &&
      detail.dispute?.patient_authorization_id &&
      (detail.calls ?? []).length === 0
    ) {
      await ctx.core.startCall(caseId);
      continue;
    }
    if (TERMINAL.has(detail.state)) {
      convo.stage = "done";
      await sendCaseReport(ctx, convo, detail);
    }
    return;
  }
}

export function newEventId(): string {
  return `evt_${randomUUID().replace(/-/g, "").slice(0, 20)}`;
}
