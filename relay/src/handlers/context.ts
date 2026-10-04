/** Everything a handler needs. Built once in index.ts (or by demo.ts / tests). */
import type { RelayConfig } from "../config";
import type { CoreClient } from "../coreClient";
import type { RelayAdapter, OutboundMessage } from "../relayAdapter/types";
import type { Conversation, ConversationStore } from "../store";
import type { Transcriber } from "./transcription";

/** Public surface of CoreClient (lets tests pass a stub). */
export type CoreApi = Pick<CoreClient, keyof CoreClient>;

export interface HandlerContext {
  config: RelayConfig;
  core: CoreApi;
  relay: RelayAdapter;
  store: ConversationStore;
  transcriber: Transcriber;
  log: (message: string) => void;
}

export async function say(
  ctx: HandlerContext,
  convo: Conversation,
  message: OutboundMessage | string,
): Promise<void> {
  await ctx.relay.sendMessage(convo.userId, typeof message === "string" ? { text: message } : message);
}

export function requireCase(convo: Conversation): string {
  if (!convo.caseId) throw new Error(`conversation ${convo.userId} has no case yet`);
  return convo.caseId;
}
