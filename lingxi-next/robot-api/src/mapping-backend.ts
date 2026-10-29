import { createConnection } from "node:net";
import path from "node:path";
import { z } from "zod";
import type { RobotAdapter, RobotSnapshot } from "./domain";

const session = z.string().regex(/^[a-f0-9]{32}$/);
const stateSchema = z.object({
  backend: z.literal("glim"), inputMode: z.literal("recorded"),
  phase: z.enum(["idle", "requested", "running", "saving", "stopping", "failed"]),
  session: session.nullable(), mapping: z.boolean(), cleanupComplete: z.boolean(),
  completedMessages: z.number().int().nonnegative().safe().optional(),
  processedScans: z.number().int().nonnegative().safe().optional(),
  estimatedPoses: z.number().int().nonnegative().safe().optional(),
  pointStream: z.object({ socket: z.string().regex(/^[a-f0-9]{32}\.points\.sock$/) }).strict().optional(),
}).strict();
export type MappingBackendState = z.infer<typeof stateSchema>;
export interface MappingBackend {
  state(): Promise<MappingBackendState>;
  start(): Promise<MappingBackendState>;
  finish(session: string, save: boolean, name?: string): Promise<{
    session: string; saved: boolean; confirmed: true; mapId?: string;
  }>;
}

export class UnixMappingBackend implements MappingBackend {
  constructor(private readonly socket: string) {
    if (process.platform !== "linux" || !path.isAbsolute(socket) || Buffer.byteLength(socket) >= 100) {
      throw new Error("private_local_linux_mapping_socket_required");
    }
  }

  private request(command: object, milliseconds: number): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const connection = createConnection({ path: this.socket });
      const parts: Buffer[] = [];
      let length = 0, settled = false;
      const finish = (error?: Error, value?: unknown) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        connection.destroy();
        if (error) reject(error); else resolve(value);
      };
      const timer = setTimeout(() => finish(new Error("独立建图请求未在期限内确认")), milliseconds);
      connection.once("connect", () => connection.write(JSON.stringify(command) + "\n"));
      connection.on("error", () => finish(new Error("独立建图服务不可用")));
      connection.on("end", () => finish(new Error("独立建图服务响应不完整")));
      connection.on("data", (bytes: Buffer) => {
        length += bytes.length;
        if (length > 8192) { finish(new Error("独立建图服务响应无效")); return; }
        parts.push(bytes);
        const line = Buffer.concat(parts);
        const end = line.indexOf(10);
        if (end < 0) return;
        try {
          if (end !== line.length - 1) throw new Error();
          const result = z.discriminatedUnion("ok", [
            z.object({ ok: z.literal(true), value: z.unknown() }).strict(),
            z.object({ ok: z.literal(false), code: z.string().regex(/^[a-z_]{1,80}$/),
              status: z.union([z.literal(400), z.literal(409), z.literal(503)]) }).strict(),
          ]).parse(JSON.parse(line.subarray(0, end).toString("utf8")));
          if (!result.ok) {
            finish(Object.assign(new Error(result.status === 409
              ? "建图会话状态已变化，请刷新后确认" : "独立建图请求未完成确认"), { statusCode: result.status }));
          } else finish(undefined, result.value);
        } catch { finish(new Error("独立建图服务响应无效")); }
      });
    });
  }

  async state() {
    return stateSchema.parse(await this.request({ action: "state" }, 1200));
  }
  async start() {
    return stateSchema.parse(await this.request({ action: "start" }, 5000));
  }
  async finish(identity: string, save: boolean, name?: string) {
    session.parse(identity);
    const result = z.object({
      session, saved: z.boolean(), confirmed: z.literal(true),
      mapId: z.string().regex(/^slam-[a-f0-9]{32}$/).optional(),
    }).strict().parse(await this.request(
      save ? { action: "save", session: identity, name } : { action: "discard", session: identity },
      save ? 125_000 : 105_000,
    ));
    if (result.session !== identity || result.saved !== save ||
        (save ? result.mapId !== `slam-${identity}` : result.mapId !== undefined)) {
      throw new Error("独立建图结束状态未匹配当前会话");
    }
    return result;
  }
}

export function mappingGuard(adapter: RobotAdapter, backend: MappingBackend): RobotAdapter {
  const rejectMotion = async () => { throw new Error("录制建图验证模式不接受机器人运动或资源播放"); };
  return {
    demoMode: adapter.demoMode,
    async getSnapshot(): Promise<RobotSnapshot> {
      const original = await adapter.getSnapshot();
      const mapping = await backend.state().catch((): MappingBackendState => ({
        backend: "glim", inputMode: "recorded", phase: "failed", session: null,
        mapping: true, cleanupComplete: false,
      }));
      return {
        ...original, mapping: mapping.mapping,
        mappingStatus: mapping.phase === "stopping" ? "saving" : mapping.phase,
        mappingBackend: "glim", mappingInputMode: mapping.inputMode, mappingSession: mapping.session ?? undefined,
        motionPresets: [], linkcraftResources: [],
        checks: original.checks.map((check) => check.id === "slam" ? {
          ...check, ready: mapping.phase !== "failed" || mapping.cleanupComplete,
          detail: "独立 GLIM · 录制数据验证",
        } : check.id === "navigation" ? { ...check, ready: false, detail: "录制验证模式，不允许导航" } : check),
      };
    },
    listMaps: () => adapter.listMaps(),
    getLidarFrame: adapter.getLidarFrame ? () => adapter.getLidarFrame!() : undefined,
    startMapping: async () => { await backend.start(); },
    stopAndSaveMap: async () => { throw new Error("独立 SLAM 地图不能作为 AIMDK 地图激活"); },
    getStoredMapGrid: (map) => adapter.getStoredMapGrid(map),
    activateMap: rejectMotion,
    startNavigation: rejectMotion,
    controlNavigation: (action, identity) => action === "stop"
      ? adapter.controlNavigation(action, identity) : rejectMotion(),
    startPresetMotion: rejectMotion,
    waitPresetMotion: (identity) => adapter.waitPresetMotion(identity),
    playExpression: (name) => adapter.playExpression(name),
    executeLinkcraftResource: rejectMotion,
    confirmActionsIdle: () => adapter.confirmActionsIdle(),
  };
}
