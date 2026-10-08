/** Handlers end to end against a stub Core that mimics Core's notification flow. */
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { Schemas } from "../src/coreClient";
import { onCoreNotify } from "../src/handlers/humanHandoff";
import { handleIncoming } from "../src/handlers/incomingMessage";
import { ConversationStore } from "../src/store";
import { CASE, setup } from "./support";

describe("relay patient flow", () => {
  it("walks consent -> recording -> review -> question -> approval -> handoff -> report", async () => {
    const { core, relay, ctx, send, tap } = setup();
    await send({ text: "hi" });
    await send({ text: "my id is u_f03417db39ffc1ae" });
    await send({ kind: "button", buttonId: "CONSENT_YES" });
    await send({ kind: "button", buttonId: "RECORD_YES" });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/lorazepam/);
    await send({ kind: "button", buttonId: "REVIEW_BILL" });
    await send({ text: "CLM-2026-0915-001" });
    const question = relay.outbox.at(-1)!.message;
    expect(question.text).toMatch(/anesthesia/);
    expect(question.buttons?.map((b) => b.label)).toEqual(["Yes", "No", "Not sure"]);

    await tap(":NO");
    expect(core.events[0]).toMatchObject({ intervention_id: "hin_1", choice: "NO", case_id: CASE });
    expect(relay.outbox.at(-1)?.message.buttons?.map((b) => b.label)).toEqual(["Review", "Approve", "Reject"]);

    await tap(":REVIEW");
    expect(relay.outbox.at(-1)?.message.text).toBe("Dear insurer...");
    await tap(":APPROVE");
    expect(core.calls).toHaveLength(1); // the service started the call after approval
    expect(relay.outbox.at(-1)?.message.text).toMatch(/SSN/);

    await tap(":DONE");
    expect(core.state).toBe("RESOLVED");
    expect(relay.outbox.some((m) => m.message.text.startsWith("Case summary (RESOLVED)"))).toBe(true);
    expect(ctx.store.get("patient").stage).toBe("done");
  });

  it("delivers each Core notification exactly once (push + pull)", async () => {
    const { core, relay, ctx, send } = setup();
    await send({ text: "hi" });
    await send({ text: "fn_pat_demo_001" });
    await send({ kind: "button", buttonId: "CONSENT_YES" });
    await send({ kind: "button", buttonId: "RECORD_SKIP" });
    await send({ kind: "button", buttonId: "REVIEW_BILL" });
    await send({ text: "CLM-2026-0915-001" });
    const shown = relay.outbox.length;
    await onCoreNotify(ctx, { notification: core.notes[0]!, case: { case_id: CASE, channel: "relay", finchnode_patient_id: "x" } });
    expect(relay.outbox.length).toBe(shown);
  });

  it("accepts typed answers for the open card and ignores unknown cases on push", async () => {
    const { core, ctx, send } = setup();
    await send({ text: "hi" });
    await send({ text: "fn_pat_demo_001" });
    await send({ text: "yes" });
    await send({ kind: "button", buttonId: "RECORD_SKIP" });
    await send({ text: "review my bill" });
    await send({ text: "CLM-2026-0915-001" });
    await send({ text: "No" });
    expect(core.events[0]?.choice).toBe("NO");
    const result = await onCoreNotify(ctx, {
      notification: { ...core.notes[0]!, case_id: "case_other" },
      case: { case_id: "case_other", channel: "asi1", finchnode_patient_id: "x" },
    });
    expect(result.delivered).toBe(false);
  });

  it("saves the FinchNode id on first setup and skips it after restart", async () => {
    const { core, relay, ctx, send } = setup();
    let n = 0;
    const ids: string[] = [];
    core.createCase = async (body?: Schemas["CaseCreate"]) => {
      ids.push(body?.patient.finchnode_patient_id ?? "");
      return { case_id: `case_${(n += 1)}` } as Schemas["Case"];
    };
    await send({ text: "hi" });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/Quick setup \(once\)/);
    await send({ text: "u_f03417db39ffc1ae" });
    expect(relay.outbox.some((m) => /You're set/.test(m.message.text))).toBe(true);
    expect(ctx.store.get("patient").finchnodePatientId).toBe("u_f03417db39ffc1ae");
    await send({ text: "restart" });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/I consent|permission/i);
    expect(relay.outbox.some((m) => /Welcome back/.test(m.message.text))).toBe(true);
    expect(ids).toEqual(["u_f03417db39ffc1ae", "u_f03417db39ffc1ae"]);
    expect(ctx.store.get("patient").caseId).toBe("case_2");
  });

  it("keeps the saved FinchNode id when the conversation is reset", () => {
    const { ctx } = setup();
    ctx.store.get("patient").finchnodePatientId = "u_saved";
    const reset = ctx.store.reset("patient");
    expect(reset.finchnodePatientId).toBe("u_saved");
    expect(reset.stage).toBe("new");
    expect(ctx.store.clearAccount("patient").finchnodePatientId).toBeUndefined();
  });

  it("reloads the saved FinchNode id from disk", () => {
    const path = join(mkdtempSync(join(tmpdir(), "relay-store-")), "conversations.json");
    const store = new ConversationStore(path);
    store.get("patient").finchnodePatientId = "u_saved";
    store.save();
    expect(new ConversationStore(path).get("patient").finchnodePatientId).toBe("u_saved");
  });

  it("lets them switch FinchNode accounts, and accepts the id as the first message", async () => {
    const { core, relay, ctx, send } = setup();
    const ids: string[] = [];
    core.createCase = async (body?: Schemas["CaseCreate"]) => {
      ids.push(body?.patient.finchnode_patient_id ?? "");
      return { case_id: "case_1" } as Schemas["Case"];
    };
    await send({ text: "u_first" });
    expect(ids).toEqual(["u_first"]);
    await send({ text: "switch" });
    expect(ctx.store.get("patient").finchnodePatientId).toBeUndefined();
    expect(relay.outbox.at(-1)?.message.text).toMatch(/Quick setup \(once\)/);
    await send({ text: "u_second" });
    expect(ids).toEqual(["u_first", "u_second"]);
    expect(ctx.store.get("patient").finchnodePatientId).toBe("u_second");
  });

  it("gives each Relay handle its own case", async () => {
    const { core, ctx } = setup();
    let n = 0;
    core.createCase = async () => ({ case_id: `case_${(n += 1)}` }) as Schemas["Case"];
    await handleIncoming(ctx, { id: "e1", kind: "message", from: "alice", text: "hi" });
    await handleIncoming(ctx, { id: "e2", kind: "message", from: "bob", text: "hi" });
    await handleIncoming(ctx, { id: "e3", kind: "message", from: "alice", text: "u_alice" });
    await handleIncoming(ctx, { id: "e4", kind: "message", from: "bob", text: "u_bob" });
    expect(ctx.store.get("alice").caseId).toBe("case_1");
    expect(ctx.store.get("bob").caseId).toBe("case_2");
    expect(ctx.store.get("alice").finchnodePatientId).toBe("u_alice");
    expect(ctx.store.get("bob").finchnodePatientId).toBe("u_bob");
  });
});
