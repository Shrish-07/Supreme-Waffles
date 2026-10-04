/**
 * RELAY_MODE=live: Relay platform over HTTPS.
 *
 * # VERIFY against current docs: every endpoint path, payload field and auth header
 * below is a placeholder until checked against the Relay SDK/API docs. If the
 * official npm SDK is preferred, install it and replace the internals of this
 * class -- keep the RelayAdapter interface unchanged so handlers do not change.
 * If Relay delivers events over a websocket instead of webhooks, add a
 * `subscribe(onEvent)` method here and call handleIncoming() for each event.
 */
import type { RelayConfig } from "../config";
import type { OutboundMessage, RelayAdapter } from "./types";

export class LiveRelayAdapter implements RelayAdapter {
  readonly mode = "live" as const;

  constructor(
    private readonly config: RelayConfig,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  private async call<T>(path: string, body: unknown): Promise<T> {
    const response = await this.fetchImpl(`${this.config.relayApiBaseUrl}${path}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.config.relayAgentToken}`, // VERIFY auth scheme
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`Relay API ${path} failed: ${response.status}`);
    return (await response.json()) as T;
  }

  async sendMessage(to: string, message: OutboundMessage): Promise<{ id: string }> {
    // VERIFY: message + quick-reply button payload format.
    return this.call("/v1/messages", { to, text: message.text, buttons: message.buttons ?? [] });
  }

  async startRecording(to: string): Promise<{ recordingId: string }> {
    // VERIFY: whether/how Relay records appointments (consent prompt, duration, callback).
    return this.call("/v1/recordings", { to });
  }

  async fetchRecording(url: string): Promise<Uint8Array> {
    const response = await this.fetchImpl(url, {
      headers: { Authorization: `Bearer ${this.config.relayAgentToken}` }, // VERIFY
    });
    if (!response.ok) throw new Error(`recording download failed: ${response.status}`);
    return new Uint8Array(await response.arrayBuffer());
  }
}
