/**
 * Core -> patient notifications. Core pushes human-handoff requests here during
 * insurer calls (NOTIFY_MODE=relay_service), along with every other prompt
 * (questions, approval requests, status). In mock mode the service pulls the same
 * notifications from Core's outbox instead. Each one is rendered exactly once.
 */
import type { PatientNotification } from "../coreClient";
import type { Conversation } from "../store";
import { renderNotification } from "../ux/messages";
import { type HandlerContext, requireCase } from "./context";

export async function deliverNotification(
  ctx: HandlerContext,
  convo: Conversation,
  notification: PatientNotification,
): Promise<string | undefined> {
  if (convo.delivered[notification.notification_id]) return undefined;
  convo.delivered[notification.notification_id] = {
    kind: notification.kind,
    interventionId: notification.intervention_id,
    disputeId: notification.dispute_id,
  };
  const sent = await ctx.relay.sendMessage(convo.userId, renderNotification(notification));
  return sent.id;
}

/** Pull Core's outbox for this case and show anything not shown yet. */
export async function deliverPending(ctx: HandlerContext, convo: Conversation): Promise<void> {
  for (const notification of await ctx.core.notifications(requireCase(convo))) {
    await deliverNotification(ctx, convo, notification);
  }
}

export interface CoreNotifyPayload {
  notification: PatientNotification;
  case: { case_id: string; channel: string; finchnode_patient_id: string };
}

/** POST /internal/notify (Core -> Relay service). */
export async function onCoreNotify(
  ctx: HandlerContext,
  payload: CoreNotifyPayload,
): Promise<{ delivered: boolean; delivery_ref?: string }> {
  const convo = ctx.store.byCase(payload.notification.case_id);
  if (!convo) {
    ctx.log(`no Relay conversation for case ${payload.notification.case_id}; skipping`);
    return { delivered: false };
  }
  const ref = await deliverNotification(ctx, convo, payload.notification);
  ctx.store.save();
  return { delivered: true, delivery_ref: ref };
}
