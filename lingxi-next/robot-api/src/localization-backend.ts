import { createConnection } from "node:net";
import path from "node:path";
import { z } from "zod";
import {
  localizationIdentitySchema, localizationSeedSchema, localizationStateSchema, sameLocalizationBinding,
  type LocalizationCommand, type LocalizationState,
} from "../../shared/localization";

export interface LocalizationBackend {
  state(): Promise<LocalizationState>;
  command(command: LocalizationCommand): Promise<LocalizationState>;
}

const unavailable = () => Object.assign(new Error("定位服务不可用，请检查录制定位进程"), { statusCode: 503 });
const envelope = z.discriminatedUnion("ok", [
  z.object({ ok: z.literal(true), value: z.unknown() }).strict(),
  z.object({ ok: z.literal(false), code: z.string().regex(/^[a-z_]{1,100}$/),
    status: z.union([z.literal(400), z.literal(409), z.literal(503)]) }).strict(),
]);

export function validatedLocalizationState(value: unknown): LocalizationState {
  const result = localizationStateSchema.safeParse(value);
  // Live and ARM64 localization have not passed commissioning.
  if (!result.success || result.data.binding.input_mode !== "recorded") throw unavailable();
  return result.data;
}

export function confirmedLocalizationCommand(command: LocalizationCommand, value: unknown): LocalizationState {
  const result = validatedLocalizationState(value);
  const expected = { seed: "seed_requested", pause: "paused", stop: "stopped" }[command.action];
  if (!sameLocalizationBinding(result.binding, command.binding) ||
      result.generation !== command.expectedGeneration + 1 || result.state !== expected ||
      !result.requestPending || result.mapFromSensor !== null) throw unavailable();
  return result;
}

/** One request per connection. A lost acknowledgement is never retried. */
export class UnixLocalizationBackend implements LocalizationBackend {
  constructor(private readonly socket: string) {
    if (process.platform !== "linux" || !path.isAbsolute(socket) || Buffer.byteLength(socket) >= 100) {
      throw new Error("private_local_linux_localization_socket_required");
    }
  }

  private request(command: object): Promise<unknown> {
    const payload = Buffer.from(JSON.stringify(command) + "\n");
    if (payload.length > 4096) return Promise.reject(unavailable());
    return new Promise((resolve, reject) => {
      const socket = createConnection({ path: this.socket });
      let buffer = Buffer.alloc(0), settled = false;
      const finish = (error?: Error, value?: unknown) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        socket.destroy();
        if (error) reject(error); else resolve(value);
      };
      const timer = setTimeout(() => finish(unavailable()), 1200);
      socket.once("connect", () => socket.write(payload));
      socket.once("error", () => finish(unavailable()));
      socket.once("end", () => finish(unavailable()));
      socket.on("data", (bytes: Buffer) => {
        if (buffer.length + bytes.length > 8192) { finish(unavailable()); return; }
        buffer = Buffer.concat([buffer, bytes]);
        const end = buffer.indexOf(10);
        if (end < 0) return;
        try {
          if (end !== buffer.length - 1) throw unavailable();
          const response = envelope.parse(JSON.parse(buffer.subarray(0, end).toString("utf8")));
          if (!response.ok) {
            finish(Object.assign(new Error(response.status === 409
              ? "定位会话、代次或参考帧已变化，请刷新后重新确认"
              : response.status === 400 ? "初始位姿或定位请求无效" : "定位工作线程尚未就绪"),
            { statusCode: response.status }));
          } else finish(undefined, response.value);
        } catch { finish(unavailable()); }
      });
    });
  }

  async state() {
    return validatedLocalizationState(await this.request({ action: "state" }));
  }

  async command(value: LocalizationCommand) {
    const parsed = value.action === "seed"
      ? { action: "seed" as const, ...localizationSeedSchema.parse(withoutAction(value)) }
      : { action: z.enum(["pause", "stop"]).parse(value.action), ...localizationIdentitySchema.parse(withoutAction(value)) };
    return confirmedLocalizationCommand(parsed, await this.request(parsed));
  }
}

function withoutAction({ action: _action, ...value }: LocalizationCommand) { return value; }
