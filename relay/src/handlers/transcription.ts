/**
 * Visit transcription (Member 2). Mock returns the synthetic demo transcript.
 * Live: VERIFY against current docs whether Relay transcribes recordings itself;
 * otherwise plug a speech-to-text provider in here. Output must match Core's
 * VisitComplete body (speaker-labelled segments).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { REPO_ROOT } from "../config";
import type { Schemas } from "../coreClient";

export interface Transcriber {
  transcribe(recordingRef: string): Promise<Schemas["VisitComplete"]>;
}

const DEMO_SCENARIO = resolve(
  REPO_ROOT,
  "shared/python/claimshield_contracts/fixtures/demo_scenario.json",
);

export class MockTranscriber implements Transcriber {
  async transcribe(): Promise<Schemas["VisitComplete"]> {
    const scenario = JSON.parse(readFileSync(DEMO_SCENARIO, "utf8")) as {
      transcript: Schemas["VisitComplete"];
    };
    return scenario.transcript;
  }
}

export class LiveTranscriber implements Transcriber {
  async transcribe(recordingRef: string): Promise<Schemas["VisitComplete"]> {
    throw new Error(
      `Live transcription is not implemented yet (recording ${recordingRef}). ` +
        "See relay/BRANCH_TASKS.md -> transcription.",
    );
  }
}
