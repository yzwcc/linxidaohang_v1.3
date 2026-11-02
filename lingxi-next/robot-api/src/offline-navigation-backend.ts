import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomBytes } from "node:crypto";
import { z } from "zod";
import {
  offlineNavigationStateSchema, offlineSceneSchema,
  type OfflineNavigationCommand, type OfflineNavigationState, type OfflineScene,
} from "../../shared/offline-navigation";

export interface OfflineNavigationBackend {
  readonly offlineOnly: true;
  state(): Promise<OfflineNavigationState>;
  scene(): Promise<OfflineScene>;
  command(value: OfflineNavigationCommand): Promise<OfflineNavigationState>;
  close(): Promise<void>;
}
const unavailable = () => Object.assign(new Error("离线导航进程未确认，请停止任务并检查离线日志"), { statusCode: 503 });
const responseSchema = z.discriminatedUnion("ok", [
  z.object({ id: z.string().regex(/^[0-9a-f]{32}$/), ok: z.literal(true), value: z.unknown() }).strict(),
  z.object({ id: z.string().regex(/^[0-9a-f]{32}$/), ok: z.literal(false),
    code: z.string().regex(/^[a-z_]{1,100}$/), status: z.union([z.literal(400), z.literal(409), z.literal(503)]) }).strict(),
]);

/** A local, explicitly selected process; no TCP, shell interpolation or reconnect/replay. */
export class StdioOfflineNavigationBackend implements OfflineNavigationBackend {
  readonly offlineOnly = true;
  private readonly process: ChildProcessWithoutNullStreams;
  private buffer = Buffer.alloc(0);
  private closed = false;
  private receivedResponse = false;
  private pending?: { id: string; finish(error?: Error, value?: unknown): void };
  private tail: Promise<unknown> = Promise.resolve();
  private queued = 0;
  private processClosed = false;
  private closing?: Promise<void>;

  constructor(executable: string, args: string[], cwd: string, environment?: NodeJS.ProcessEnv) {
    if (!executable || args.some((arg) => arg.includes("\0"))) throw new Error("explicit_offline_process_required");
    this.process = spawn(executable, args, { cwd, env: environment, shell: false, windowsHide: true, stdio: "pipe" });
    // Do not forward potentially sensitive process output into API logs.
    this.process.stderr.resume();
    this.process.stdout.on("data", (chunk: Buffer) => this.receive(chunk));
    this.process.once("error", () => this.fail());
    this.process.once("exit", () => this.fail());
    this.process.once("close", () => { this.processClosed = true; });
    this.process.stdin.on("error", () => this.fail());
  }

  private fail() {
    this.closed = true;
    this.pending?.finish(unavailable());
    this.process.stdin.destroy();
    this.process.kill();
  }

  private receive(chunk: Buffer) {
    if (this.closed) return;
    if (this.buffer.length + chunk.length > 256 * 1024) { this.fail(); return; }
    this.buffer = Buffer.concat([this.buffer, chunk]);
    const end = this.buffer.indexOf(10);
    if (end < 0) return;
    try {
      if (end !== this.buffer.length - 1 || !this.pending) throw unavailable();
      const response = responseSchema.parse(JSON.parse(this.buffer.subarray(0, end).toString("utf8")));
      this.buffer = Buffer.alloc(0);
      if (response.id !== this.pending.id) throw unavailable();
      this.receivedResponse = true;
      if (response.ok) this.pending.finish(undefined, response.value);
      else this.pending.finish(Object.assign(new Error(response.status === 409
        ? "离线导航会话或任务代次已变化，请刷新后重试" : "离线导航请求未获确认"), { statusCode: response.status }));
    } catch { this.fail(); }
  }

  private request(body: object): Promise<unknown> {
    if (this.closed || this.queued >= 32) return Promise.reject(unavailable());
    this.queued += 1;
    const request = this.tail.then(() => new Promise((resolve, reject) => {
      if (this.closed) { reject(unavailable()); return; }
      const id = randomBytes(16).toString("hex");
      // WSL/ROS cold startup is not a navigation-control acknowledgement.
      const timer = setTimeout(() => this.fail(), this.receivedResponse ? 4500 : 15000);
      this.pending = { id, finish: (error, value) => {
        clearTimeout(timer);
        this.pending = undefined;
        if (error) reject(error); else resolve(value);
      } };
      this.process.stdin.write(JSON.stringify({ id, ...body }) + "\n");
    })).finally(() => { this.queued -= 1; });
    this.tail = request.catch(() => undefined);
    return request;
  }

  private async typed<T>(schema: z.ZodType<T>, body: object): Promise<T> {
    const value = await this.request(body);
    const parsed = schema.safeParse(value);
    if (!parsed.success) { this.fail(); throw unavailable(); }
    return parsed.data;
  }
  async state() { return this.typed(offlineNavigationStateSchema, { action: "state" }); }
  async scene() { return this.typed(offlineSceneSchema, { action: "scene" }); }
  async command(value: OfflineNavigationCommand) { return this.typed(offlineNavigationStateSchema, value); }
  close(): Promise<void> {
    return this.closing ??= this.finishClose();
  }

  private async finishClose() {
    this.closed = true;
    this.pending?.finish(unavailable());
    this.process.stdin.end();
    if (this.processClosed) return;
    await new Promise<void>((resolve, reject) => {
      const term = setTimeout(() => this.process.kill("SIGTERM"), 3500);
      const kill = setTimeout(() => this.process.kill("SIGKILL"), 4000);
      const deadline = setTimeout(() => {
        cleanup(); reject(new Error("offline_process_shutdown_unconfirmed"));
      }, 4500);
      const done = () => { cleanup(); resolve(); };
      const cleanup = () => {
        clearTimeout(term); clearTimeout(kill); clearTimeout(deadline);
        this.process.removeListener("close", done);
      };
      this.process.once("close", done);
    });
  }
}
