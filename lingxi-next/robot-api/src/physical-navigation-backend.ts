import { createConnection, type Socket } from "node:net";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { z } from "zod";
import {
  physicalNavigationMapActivationSchema,
  physicalNavigationStateSchema,
  type PhysicalNavigationMapActivation,
  type PhysicalNavigationCommand,
  type PhysicalNavigationState,
} from "../../shared/physical-navigation";

export interface PhysicalNavigationBackend {
  readonly physical: true;
  state(): Promise<PhysicalNavigationState>;
  command(value: PhysicalNavigationCommand): Promise<PhysicalNavigationState>;
  activateMap?(value: PhysicalNavigationMapActivation): Promise<PhysicalNavigationState>;
  close(): Promise<void>;
}

const unavailable = () => Object.assign(
  new Error("physical_navigation_bridge_unavailable"), { statusCode: 503 },
);
const responseSchema = z.discriminatedUnion("ok", [
  z.object({ id: z.string().regex(/^[0-9a-f]{32}$/), ok: z.literal(true), value: z.unknown() }).strict(),
  z.object({ id: z.string().regex(/^[0-9a-f]{32}$/), ok: z.literal(false),
    code: z.string().regex(/^[a-z_]{1,100}$/), status: z.union([z.literal(400), z.literal(409), z.literal(503)]) }).strict(),
]);

/**
 * Private one-request-at-a-time Unix bridge for the owned ROS 2/Nav2 process.
 * It deliberately has no reconnect or replay behavior: an uncertain command
 * must be stopped and inspected by the operator before a new command is sent.
 */
export class UnixRosNavigationBackend implements PhysicalNavigationBackend {
  readonly physical = true;
  private closed = false;
  private socket?: Socket;
  private buffer = Buffer.alloc(0);
  private pending?: { id: string; finish(error?: Error, value?: unknown): void };
  private tail: Promise<unknown> = Promise.resolve();
  private queued = 0;
  private closing?: Promise<void>;

  constructor(private readonly socketPath: string) {
    if (process.platform !== "linux" || !path.isAbsolute(socketPath) ||
        path.normalize(socketPath) !== socketPath || Buffer.byteLength(socketPath) >= 100) {
      throw new Error("private_absolute_linux_navigation_socket_required");
    }
  }

  private fail(error: Error = unavailable()) {
    if (this.closed) return;
    this.closed = true;
    this.pending?.finish(error);
    this.pending = undefined;
    this.socket?.destroy();
    this.socket = undefined;
  }

  private request(body: object, timeoutMs: number): Promise<unknown> {
    if (this.closed || this.queued >= 16) return Promise.reject(unavailable());
    this.queued += 1;
    const request = this.tail.then(() => new Promise((resolve, reject) => {
      if (this.closed) { reject(unavailable()); return; }
      const id = randomBytes(16).toString("hex");
      let settled = false;
      const finish = (error?: Error, value?: unknown) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (error) reject(error); else resolve(value);
      };
      const timer = setTimeout(() => {
        this.fail(Object.assign(new Error("physical_navigation_bridge_timeout"), { statusCode: 503 }));
      }, timeoutMs);
      const socket = createConnection({ path: this.socketPath });
      this.socket = socket;
      this.pending = { id, finish };
      socket.once("connect", () => {
        try { socket.write(JSON.stringify({ id, ...body }) + "\n"); }
        catch { this.fail(); }
      });
      socket.on("data", (chunk: Buffer) => this.receive(chunk));
      socket.once("error", () => this.fail());
      socket.once("close", () => {
        if (!settled && this.pending?.id === id) this.fail();
      });
    })).finally(() => { this.queued -= 1; });
    this.tail = request.catch(() => undefined);
    return request;
  }

  private receive(chunk: Buffer) {
    if (this.closed) return;
    if (this.buffer.length + chunk.length > 64 * 1024) { this.fail(); return; }
    this.buffer = Buffer.concat([this.buffer, chunk]);
    const end = this.buffer.indexOf(10);
    if (end < 0) return;
    try {
      if (end !== this.buffer.length - 1 || !this.pending) throw unavailable();
      const response = responseSchema.parse(JSON.parse(this.buffer.subarray(0, end).toString("utf8")));
      this.buffer = Buffer.alloc(0);
      const pending = this.pending;
      if (response.id !== pending.id) throw unavailable();
      this.pending = undefined;
      if (response.ok) pending.finish(undefined, response.value);
      else pending.finish(Object.assign(new Error(response.code), { statusCode: response.status }));
      this.socket?.end();
    } catch (error) {
      this.fail(error instanceof Error ? error : unavailable());
    }
  }

  private async typed(body: object, timeoutMs: number): Promise<PhysicalNavigationState> {
    const value = await this.request(body, timeoutMs);
    const result = physicalNavigationStateSchema.safeParse(value);
    if (!result.success) {
      this.fail(new Error("physical_navigation_state_invalid"));
      throw Object.assign(new Error("physical_navigation_state_invalid"), { statusCode: 503 });
    }
    return result.data;
  }

  state() { return this.typed({ action: "state" }, 1500); }

  command(value: PhysicalNavigationCommand) {
    return this.typed(value, value.action === "stop" ? 7000 : 5000);
  }

  activateMap(value: PhysicalNavigationMapActivation) {
    // Unclaimed producer exit precedes a separate map-bound stop deadline.
    return this.typed(physicalNavigationMapActivationSchema.parse(value), 7000);
  }

  close(): Promise<void> {
    return this.closing ??= Promise.resolve().then(() => {
      this.closed = true;
      this.pending?.finish(unavailable());
      this.pending = undefined;
      this.socket?.destroy();
      this.socket = undefined;
    });
  }
}
