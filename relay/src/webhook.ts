/**
 * Webhooks.
 *  - Inbound from the Relay platform: signature verification, idempotency (durable
 *    ledger), normalization into InboundEvent.  # VERIFY against current docs
 *  - Outbound to Core: HMAC identical to core/app/auth.py `sign_payload`.
 */
import { createHmac, timingSafeEqual } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

// VERIFY against current docs: Relay platform webhook header names + signing scheme.
export const PLATFORM_SIGNATURE_HEADER = "x-relay-signature";
export const PLATFORM_TIMESTAMP_HEADER = "x-relay-timestamp";
export const SIGNATURE_TOLERANCE_S = 300;

export class SignatureError extends Error {}

export function hmacHex(secret: string, payload: string): string {
  return createHmac("sha256", secret).update(payload, "utf8").digest("hex");
}

/** `v1=<hex HMAC-SHA256(secret, "{timestamp}.{body}")>` -- must match Core. */
export function signCoreEvent(secret: string, timestamp: string, body: string): string {
  return `v1=${hmacHex(secret, `${timestamp}.${body}`)}`;
}

function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

/** Throws SignatureError unless the platform signature is valid and fresh. VERIFY scheme. */
export function verifyPlatformSignature(
  secret: string,
  rawBody: string,
  headers: Record<string, string | undefined>,
  nowS: number = Date.now() / 1000,
): void {
  const timestamp = headers[PLATFORM_TIMESTAMP_HEADER];
  const signature = headers[PLATFORM_SIGNATURE_HEADER];
  if (!secret) throw new SignatureError("RELAY_WEBHOOK_SECRET is not configured");
  if (!timestamp || !signature) throw new SignatureError("missing signature headers");
  if (!/^\d+$/.test(timestamp)) throw new SignatureError("invalid timestamp");
  if (Math.abs(nowS - Number(timestamp)) > SIGNATURE_TOLERANCE_S) {
    throw new SignatureError("timestamp outside tolerance (possible replay)");
  }
  const expected = hmacHex(secret, `${timestamp}.${rawBody}`);
  const provided = signature.replace(/^(v1|sha256)=/, "");
  if (!safeEqual(expected, provided)) throw new SignatureError("signature mismatch");
}

export type InboundKind = "message" | "button" | "recording";

export interface InboundEvent {
  id: string;
  kind: InboundKind;
  from: string;
  text?: string;
  buttonId?: string;
  recordingUrl?: string;
}

/** Normalize a Relay platform event. VERIFY against current docs: payload shape. */
export function parsePlatformEvent(payload: unknown): InboundEvent {
  const event = payload as {
    id?: string;
    type?: string;
    from?: string | { id?: string };
    text?: string;
    button?: { id?: string };
    recording?: { url?: string };
  };
  const from = typeof event.from === "string" ? event.from : event.from?.id;
  if (!event.id || !from) throw new Error("event is missing id or sender");
  if (event.type === "button") return { id: event.id, kind: "button", from, buttonId: event.button?.id };
  if (event.type === "recording.completed") {
    return { id: event.id, kind: "recording", from, recordingUrl: event.recording?.url };
  }
  return { id: event.id, kind: "message", from, text: event.text ?? "" };
}

/** Durable idempotency ledger: each platform event id is processed at most once. */
export class EventLedger {
  private readonly seenAt = new Map<string, number>();

  constructor(
    private readonly path?: string,
    private readonly ttlMs = 7 * 24 * 3600 * 1000,
  ) {
    if (path && existsSync(path)) {
      const stored = JSON.parse(readFileSync(path, "utf8")) as Record<string, number>;
      for (const [id, at] of Object.entries(stored)) this.seenAt.set(id, at);
    }
  }

  /** Returns false if the event was already recorded; records it otherwise. */
  claim(id: string, now = Date.now()): boolean {
    for (const [key, at] of this.seenAt) if (now - at > this.ttlMs) this.seenAt.delete(key);
    if (this.seenAt.has(id)) return false;
    this.seenAt.set(id, now);
    this.persist();
    return true;
  }

  /** Forget an id so the platform's retry is processed (used when handling failed). */
  release(id: string): void {
    this.seenAt.delete(id);
    this.persist();
  }

  private persist(): void {
    if (!this.path) return;
    mkdirSync(dirname(this.path), { recursive: true });
    writeFileSync(this.path, JSON.stringify(Object.fromEntries(this.seenAt)));
  }
}
