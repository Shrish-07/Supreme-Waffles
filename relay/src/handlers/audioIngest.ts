/**
 * Store recorded appointment audio and return a reference for Core.
 * Recordings are PHI: they stay in relay/data (git-ignored) and only the
 * reference string is sent to Core.
 */
import { randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import type { HandlerContext } from "./context";

export async function ingestRecording(ctx: HandlerContext, recordingUrl?: string): Promise<string> {
  if (ctx.relay.simulatesRecording || !recordingUrl) {
    return `mock://recordings/${randomUUID()}.wav`;
  }
  const audio = await ctx.relay.fetchRecording(recordingUrl);
  const directory = join(ctx.config.dataDir, "recordings");
  mkdirSync(directory, { recursive: true });
  const path = join(directory, `${randomUUID()}.wav`);
  writeFileSync(path, audio);
  ctx.log(`stored recording (${audio.byteLength} bytes)`);
  return `file://${path}`;
}
