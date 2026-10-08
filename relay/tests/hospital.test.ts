import { afterEach, describe, expect, it } from "vitest";

import { isAtHospitalText, stopLiveWatch } from "../src/handlers/hospitalCheckin";
import { findNearbyHospital, metersBetween } from "../src/places/hospitals";
import { setup } from "./support";

afterEach(() => stopLiveWatch("patient"));

describe("hospital lookup", () => {
  it("measures distance in meters", () => {
    expect(metersBetween({ latitude: 37.76, longitude: -122.45 }, { latitude: 37.76, longitude: -122.45 })).toBe(0);
    const block = metersBetween(
      { latitude: 37.76, longitude: -122.45 },
      { latitude: 37.761, longitude: -122.45 },
    );
    expect(block).toBeGreaterThan(80);
    expect(block).toBeLessThan(150);
  });

  it("picks the closest named hospital from Overpass", async () => {
    const fetchImpl: typeof fetch = async () =>
      new Response(
        JSON.stringify({
          elements: [
            { lat: 37.76, lon: -122.45, tags: { name: "ZSFG" } },
            { lat: 37.77, lon: -122.45, tags: { name: "Farther Clinic" } },
          ],
        }),
        { status: 200 },
      );
    const found = await findNearbyHospital(37.7601, -122.45, { fetchImpl, radiusM: 400 });
    expect(found?.name).toBe("ZSFG");
    expect(found?.meters).toBeLessThan(50);
  });
});

describe("hospital check-in", () => {
  it("pings to record when a pin is at a hospital", async () => {
    const { relay, ctx, send } = setup({
      findHospital: async () => ({ name: "Zuckerberg San Francisco General", meters: 30 }),
    });
    await send({ text: "hi", chatId: "chat_1" });
    await send({ text: "u_f03417db39ffc1ae" });
    expect(relay.locationRequests).toContain("chat_1");
    expect(relay.outbox.some((m) => /Share your location/.test(m.message.text))).toBe(true);
    await send({ text: "yes" });
    await send({ kind: "button", buttonId: "RECORD_SKIP" });
    await send({
      place: { latitude: 37.7558, longitude: -122.4047, address: "1001 Potrero Ave" },
    });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/Zuckerberg San Francisco General/);
    expect(relay.outbox.at(-1)?.message.buttons?.map((b) => b.id)).toEqual(["RECORD_YES", "RECORD_SKIP"]);
    expect(ctx.store.get("patient").stage).toBe("awaiting_recording_choice");
  });

  it("says so when the pin is not a hospital", async () => {
    const { relay, send } = setup({ findHospital: async () => undefined });
    await send({ text: "hi" });
    await send({ text: "u_f03417db39ffc1ae" });
    await send({ text: "yes" });
    await send({ kind: "button", buttonId: "RECORD_SKIP" });
    await send({ place: { latitude: 37.77, longitude: -122.42 } });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/doesn't look like a hospital/);
  });

  it("treats I'm at the hospital as a check-in", async () => {
    expect(isAtHospitalText("I'm at the hospital")).toBe(true);
    expect(isAtHospitalText("hospital bill")).toBe(false);
    const { relay, send } = setup();
    await send({ text: "I'm at the hospital" });
    expect(relay.outbox.at(-1)?.message.text).toMatch(/Send your FinchNode patient ID first/);
    const later = setup();
    await later.send({ text: "hi" });
    await later.send({ text: "u_demo" });
    await later.send({ text: "yes" });
    await later.send({ kind: "button", buttonId: "RECORD_SKIP" });
    await later.send({ text: "I'm at the hospital" });
    expect(later.relay.outbox.at(-1)?.message.text).toMatch(/Looks like you're at a hospital/);
  });
});
