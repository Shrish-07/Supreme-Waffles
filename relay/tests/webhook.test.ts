import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  EventLedger,
  PLATFORM_SIGNATURE_HEADER,
  PLATFORM_TIMESTAMP_HEADER,
  SignatureError,
  hmacHex,
  parsePlatformEvent,
  signCoreEvent,
  verifyPlatformSignature,
} from "../src/webhook";

describe("signCoreEvent", () => {
  it("matches Core's Python sign_payload byte for byte", () => {
    // Vector from: app.auth.sign_payload("dev-core-webhook-secret", "1700000000", b'{"event_id":"evt_123456"}')
    expect(signCoreEvent("dev-core-webhook-secret", "1700000000", '{"event_id":"evt_123456"}')).toBe(
      "v1=4f5269273c2f93ecc20e88cc8384c4004dd3cff26e9969b17754e8d14412872d",
    );
  });
});

describe("verifyPlatformSignature", () => {
  const secret = "platform-secret";
  const body = '{"id":"e1"}';
  const headersFor = (ts: string, sig = hmacHex(secret, `${ts}.${body}`)) => ({
    [PLATFORM_TIMESTAMP_HEADER]: ts,
    [PLATFORM_SIGNATURE_HEADER]: `sha256=${sig}`,
  });

  it("accepts a fresh valid signature", () => {
    expect(() => verifyPlatformSignature(secret, body, headersFor("1000"), 1100)).not.toThrow();
  });

  it("rejects tampering, staleness and missing headers", () => {
    expect(() => verifyPlatformSignature(secret, `${body} `, headersFor("1000"), 1000)).toThrow(SignatureError);
    expect(() => verifyPlatformSignature(secret, body, headersFor("1000"), 5000)).toThrow(/tolerance/);
    expect(() => verifyPlatformSignature(secret, body, {}, 1000)).toThrow(/missing/);
    expect(() => verifyPlatformSignature("", body, headersFor("1000"), 1000)).toThrow(/not configured/);
  });
});

describe("parsePlatformEvent", () => {
  it("normalizes messages, buttons and recordings", () => {
    expect(parsePlatformEvent({ id: "1", type: "message", from: { id: "u" }, text: "hi" })).toEqual({
      id: "1",
      kind: "message",
      from: "u",
      text: "hi",
    });
    expect(parsePlatformEvent({ id: "2", type: "button", from: "u", button: { id: "YES" } }).buttonId).toBe("YES");
    expect(
      parsePlatformEvent({ id: "3", type: "recording.completed", from: "u", recording: { url: "https://x" } })
        .recordingUrl,
    ).toBe("https://x");
    expect(() => parsePlatformEvent({ type: "message" })).toThrow();
  });
});

describe("EventLedger", () => {
  it("claims each id once, persists across restarts, and can release", () => {
    const path = join(mkdtempSync(join(tmpdir(), "ledger-")), "events.json");
    const ledger = new EventLedger(path);
    expect(ledger.claim("evt_1")).toBe(true);
    expect(ledger.claim("evt_1")).toBe(false);
    expect(new EventLedger(path).claim("evt_1")).toBe(false);
    ledger.release("evt_1");
    expect(new EventLedger(path).claim("evt_1")).toBe(true); // the release was persisted
  });

  it("expires old ids", () => {
    const ledger = new EventLedger(undefined, 1000);
    expect(ledger.claim("evt_2", 0)).toBe(true);
    expect(ledger.claim("evt_2", 5000)).toBe(true);
  });
});
