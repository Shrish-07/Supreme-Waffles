/**
 * Patient-facing copy and buttons. Core notifications are re-rendered here from
 * their `kind` + `data`, so wording can change without touching Core.
 * Button ids on Core notifications are `ntf:<notificationId>:<CHOICE>`; the
 * reply is forwarded to Core as a signed event (handlers/approvalFlow.ts).
 */
import type { PatientNotification } from "../coreClient";
import type { Button, OutboundMessage } from "../relayAdapter/types";

export const BUTTONS = {
  consentYes: { id: "CONSENT_YES", label: "I consent" },
  consentNo: { id: "CONSENT_NO", label: "No thanks" },
  recordYes: { id: "RECORD_YES", label: "Record my visit" },
  recordSkip: { id: "RECORD_SKIP", label: "Skip" },
  reviewBill: { id: "REVIEW_BILL", label: "Review my bill" },
  yes: { id: "YES", label: "Yes" },
  no: { id: "NO", label: "No" },
  notSure: { id: "NOT_SURE", label: "Not sure" },
  review: { id: "REVIEW", label: "Review" },
  approve: { id: "APPROVE", label: "Approve" },
  reject: { id: "REJECT", label: "Reject" },
  done: { id: "DONE", label: "Done" },
  cancel: { id: "CANCEL", label: "Cancel call" },
} as const satisfies Record<string, Button>;

export const QUESTION_BUTTONS: Button[] = [BUTTONS.yes, BUTTONS.no, BUTTONS.notSure];
export const APPROVAL_BUTTONS: Button[] = [BUTTONS.review, BUTTONS.approve, BUTTONS.reject];
export const HANDOFF_BUTTONS: Button[] = [BUTTONS.done, BUTTONS.cancel];

/** Must be >= 10 chars; stored verbatim as the consent text in Core. */
export const DATA_CONSENT_TEXT =
  "I allow ClaimShield to read my FinchNode health records (encounters, procedures, " +
  "medications and claims) to review this bill.";
export const RECORDING_CONSENT_TEXT =
  "I agree that ClaimShield may record and transcribe this appointment for my bill review.";

export const copy = {
  welcome:
    "Hi! I'm ClaimShield. I compare your medical bills with your health records and visit " +
    "recordings, flag possible billing errors, and help you dispute them.\n\n" +
    "Please send your FinchNode patient ID to get started.",
  dataConsent: (): OutboundMessage => ({
    text: `To review your bills I need your permission:\n\n"${DATA_CONSENT_TEXT}"`,
    buttons: [BUTTONS.consentYes, BUTTONS.consentNo],
  }),
  consentDeclined: "Understood. I won't access your records. Send \"restart\" any time.",
  recordingOffer: (): OutboundMessage => ({
    text:
      "Want me to record and transcribe your appointment so I can check the bill against " +
      `what actually happened?\n\n"${RECORDING_CONSENT_TEXT}"`,
    buttons: [BUTTONS.recordYes, BUTTONS.recordSkip],
  }),
  recordingStarted: "Recording started. I'll process it when your appointment ends.",
  sendVoiceMemo: (): OutboundMessage => ({
    text:
      "Great. Record your appointment as a voice memo (hold the mic button in this chat) " +
      "and send it here. I'll transcribe it and note what was done.",
    buttons: [BUTTONS.recordSkip],
  }),
  transcribing: "Got your recording. Transcribing it now...",
  transcriptionFailed: (reason: string): OutboundMessage => ({
    text: `I couldn't transcribe that recording (${reason}). Please send the voice memo again.`,
    buttons: [BUTTONS.recordSkip],
  }),
  audioNotExpected:
    "Thanks, but I only use recordings right after you tap Record my visit. Send \"restart\" to start over.",
  transcriptReady: (items: string[]): OutboundMessage => ({
    text:
      "Your visit recording is processed." +
      (items.length ? ` I noted: ${items.join("; ")}.` : " I didn't find any billable services in it.") +
      "\n\nWhen your bill arrives, tap Review my bill.",
    buttons: [BUTTONS.reviewBill],
  }),
  readyForBill: (): OutboundMessage => ({
    text: "Tap Review my bill when you're ready.",
    buttons: [BUTTONS.reviewBill],
  }),
  reviewing: "Reviewing your bill against your records and visit recording...",
  restart: "Send \"restart\" to review another bill.",
  notUnderstood: "Sorry, I didn't catch that. Please use the buttons above.",
  coreRejected: (code: string) =>
    `Sorry, I couldn't do that step (${code}). Send "restart" to start over if you're stuck.`,
};

function withNotificationIds(notificationId: string, buttons: Button[]): Button[] {
  return buttons.map((b) => ({ id: `ntf:${notificationId}:${b.id}`, label: b.label }));
}

/** Render a Core notification for the patient. */
export function renderNotification(n: PatientNotification): OutboundMessage {
  const buttons = (n.buttons ?? []).map((b) => ({ id: b.id, label: b.label }));
  switch (n.kind) {
    case "confirmation_question":
      return { text: n.text, buttons: withNotificationIds(n.notification_id, QUESTION_BUTTONS) };
    case "approval_request":
      return { text: n.text, buttons: withNotificationIds(n.notification_id, APPROVAL_BUTTONS) };
    case "human_handoff":
      return {
        text: n.text,
        buttons: withNotificationIds(n.notification_id, buttons.length ? buttons : HANDOFF_BUTTONS),
      };
    default:
      return { text: n.text };
  }
}
