/** Builds the handler context and the HTTP server (index.ts just listens). */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { join } from "node:path";
import { timingSafeEqual } from "node:crypto";

import type { RelayConfig } from "./config";
import { CoreClient } from "./coreClient";
import type { HandlerContext } from "./handlers/context";
import { handleIncoming } from "./handlers/incomingMessage";
import { onCoreNotify, type CoreNotifyPayload } from "./handlers/humanHandoff";
import { LiveTranscriber, MockTranscriber } from "./handlers/transcription";
import { LiveRelayAdapter } from "./relayAdapter/live";
import { MockRelayAdapter } from "./relayAdapter/mock";
import { ConversationStore } from "./store";
import { EventLedger, SignatureError, parsePlatformEvent, verifyPlatformSignature } from "./webhook";

export function createContext(config: RelayConfig, overrides: Partial<HandlerContext> = {}): HandlerContext {
  return {
    config,
    core: new CoreClient(config.coreBaseUrl, config.serviceToken, config.coreWebhookSecret),
    relay: config.mode === "live" ? new LiveRelayAdapter(config) : new MockRelayAdapter(true),
    store: new ConversationStore(join(config.dataDir, "conversations.json")),
    transcriber: config.mode === "live" ? new LiveTranscriber() : new MockTranscriber(),
    log: (message) => console.log(`[relay] ${message}`),
    ...overrides,
  };
}

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf8");
}

function send(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function bearerMatches(header: string | undefined, token: string): boolean {
  const provided = Buffer.from(header?.replace(/^Bearer\s+/i, "") ?? "");
  const expected = Buffer.from(token);
  return provided.length === expected.length && timingSafeEqual(provided, expected);
}

export function createApp(ctx: HandlerContext, ledger = new EventLedger(join(ctx.config.dataDir, "events.json"))): Server {
  return createServer(async (req, res) => {
    try {
      if (req.method === "GET" && req.url === "/health") {
        return send(res, 200, { status: "ok", mode: ctx.config.mode });
      }
      if (req.method === "POST" && req.url === "/webhook/relay") {
        const raw = await readBody(req);
        if (ctx.config.mode === "live") {
          verifyPlatformSignature(ctx.config.relayWebhookSecret, raw, req.headers as Record<string, string>);
        }
        const event = parsePlatformEvent(JSON.parse(raw));
        if (!ledger.claim(event.id)) return send(res, 200, { status: "duplicate" });
        try {
          await handleIncoming(ctx, event);
        } catch (error) {
          ledger.release(event.id); // let the platform retry
          throw error;
        }
        return send(res, 200, { status: "processed" });
      }
      if (req.method === "POST" && req.url === "/internal/notify") {
        if (!bearerMatches(req.headers.authorization, ctx.config.serviceToken)) {
          return send(res, 401, { error: "unauthorized" });
        }
        const payload = JSON.parse(await readBody(req)) as CoreNotifyPayload;
        return send(res, 200, await onCoreNotify(ctx, payload));
      }
      return send(res, 404, { error: "not found" });
    } catch (error) {
      if (error instanceof SignatureError) return send(res, 401, { error: error.message });
      ctx.log(`error: ${(error as Error).message}`);
      return send(res, 500, { error: "internal error" });
    }
  });
}
