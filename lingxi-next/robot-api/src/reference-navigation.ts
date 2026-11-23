import { lstatSync, realpathSync } from "node:fs";
import { request } from "node:http";
import path from "node:path";
import { z } from "zod";
import type { MapPoint, RobotMap } from "../../shared/contracts";
import type { RobotAdapter, RobotSnapshot } from "./domain";
import { validatedLidarFrame } from "./lidar";

const pose = z.object({ x: z.number().finite(), y: z.number().finite(), yaw: z.number().finite() }).strict();
const mapId = z.string().regex(/^fastlio-[0-9a-f]{32}$/);
const sessionId = z.string().regex(/^[0-9a-f]{32}$/);
const motionState = z.object({
  session: sessionId, ready: z.boolean(), physicalMotionSent: z.boolean(),
  status: z.enum(["idle", "requested", "running", "paused", "finished", "failed"]),
  requiresStop: z.boolean(), taskId: z.string().min(1).max(100).nullable(),
  pointId: z.string().min(1).max(100).nullable(), detail: z.string().max(250),
}).strict();
const mapSchema = z.object({
  id: mapId, name: z.string().min(1).max(64), active: z.boolean(),
  resolution: z.number().min(0.02).max(0.2),
  origin: z.tuple([z.number().finite(), z.number().finite(), z.number().finite()]),
}).strict();
const calibrationState = z.enum(["matched", "invalid_profile", "missing_live_tf", "invalid_live_tf", "mismatch"]);
const calibrationDetail: Record<z.infer<typeof calibrationState>, string> = {
  matched: "FAST-LIO 外参与本机雷达—IMU TF 一致",
  invalid_profile: "本机 FAST-LIO 标定配置缺失或无效",
  missing_live_tf: "本机尚未发布雷达—IMU TF",
  invalid_live_tf: "本机雷达—IMU TF 无效",
  mismatch: "FAST-LIO 外参与本机雷达—IMU TF 不一致，请核对本机标定",
};
const calibrationErrors = new Map(Object.entries(calibrationDetail)
  .filter(([state]) => state !== "matched").map(([state, detail]) => [`reference_calibration_${state}`, detail]));
const statusSchema = z.object({
  phase: z.enum(["idle", "requested", "running", "saving", "failed"]),
  session: sessionId.nullable(),
  sensorsReady: z.boolean(), mappingReady: z.boolean(), aimdkReady: z.boolean(),
  planningReady: z.boolean(), calibration: calibrationState.optional(),
  pose: pose.optional(), poseAtMs: z.number().finite().positive().optional(),
  navigation: motionState.optional(),
}).strict();
const gridSchema = z.object({
  width: z.number().int().positive(), height: z.number().int().positive(),
  resolution: z.number().min(0.02).max(0.2),
  originX: z.number().finite(), originY: z.number().finite(), originYaw: z.number().finite(),
  stamp: z.number().finite().positive(), cells: z.array(z.union([z.literal(-1), z.literal(0), z.literal(100)])).max(4_000_000),
}).strict().refine((grid) => grid.cells.length === grid.width * grid.height);

function denied(message: string, statusCode = 409) {
  return Object.assign(new Error(message), { statusCode });
}

export class ReferenceNavigationAdapter implements RobotAdapter {
  readonly demoMode = false;
  private mode: "point" | "nav2" = "point";
  private pendingCommands = 0;
  private last?: z.infer<typeof motionState>;
  private failedDispatch?: string;

  constructor(private readonly endpoint: string) {
    if (process.platform !== "linux" || !path.isAbsolute(endpoint) || path.normalize(endpoint) !== endpoint ||
        Buffer.byteLength(endpoint) >= 100) throw new Error("invalid_reference_navigation_socket");
  }

  private call(route: string, payload?: object, timeoutMs = 1500, maximumBytes = 256 * 1024): Promise<unknown> {
    return new Promise((resolve, reject) => {
      try {
        const parent = path.dirname(this.endpoint);
        if (realpathSync(parent) !== parent) throw new Error();
        const directory = lstatSync(parent);
        const endpoint = lstatSync(this.endpoint, { throwIfNoEntry: false });
        if (!directory.isDirectory() || directory.uid !== process.getuid?.() || (directory.mode & 0o077) ||
            !endpoint?.isSocket() || endpoint.uid !== directory.uid || (endpoint.mode & 0o077)) throw new Error();
      } catch {
        reject(denied("FAST-LIO 本机服务未就绪", 503));
        return;
      }
      const body = payload === undefined ? undefined : JSON.stringify(payload);
      const client = request({ socketPath: this.endpoint, path: `/lingxi/${route}`, signal: AbortSignal.timeout(timeoutMs),
        method: body === undefined ? "GET" : "POST", agent: false,
        headers: body === undefined ? {} : { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) },
      }, (response) => {
        const parts: Buffer[] = [];
        let size = 0;
        response.on("data", (part: Buffer) => {
          size += part.length;
          if (size > maximumBytes) client.destroy(new Error("reference_response_limit"));
          else parts.push(part);
        });
        response.on("error", () => reject(denied("FAST-LIO 响应中断", 503)));
        response.on("end", () => {
          if (response.statusCode !== 200) {
            const status = response.statusCode;
            let detail: string | undefined;
            if (status === 409 && size <= maximumBytes) {
              try { detail = calibrationErrors.get(JSON.parse(Buffer.concat(parts).toString("utf8"))?.detail); }
              catch { /* Other internal failures are not public diagnostics. */ }
            }
            reject(denied(detail ?? "FAST-LIO 操作未确认，请查看运行状态", status === 404 ? 404 : status === 409 ? 409 : 503));
            return;
          }
          try { resolve(JSON.parse(Buffer.concat(parts).toString("utf8"))); }
          catch { reject(denied("FAST-LIO 响应无效", 503)); }
        });
      });
      client.on("error", () => reject(denied("FAST-LIO 请求未确认，请刷新状态", 503)));
      client.end(body);
    });
  }

  async getSnapshot(): Promise<RobotSnapshot> {
    const state = statusSchema.parse(await this.call("status"));
    const motion = state.navigation;
    if (motion) {
      if (this.last && this.last.session !== motion.session && (this.last.requiresStop ||
          ["requested", "running", "paused"].includes(this.last.status))) this.failedDispatch ??= this.last.taskId ?? "restart";
      this.last = motion;
    }
    const now = Date.now();
    const livePose = state.poseAtMs !== undefined && now - state.poseAtMs >= -100 && now - state.poseAtMs <= 1500;
    const calibrated = state.calibration === "matched";
    const calibrationMessage = state.calibration ? calibrationDetail[state.calibration] : "参考服务未报告本机标定，请同步升级";
    const planningReady = calibrated && state.planningReady;
    return {
      connected: true, mapping: ["requested", "running", "saving"].includes(state.phase),
      mappingStatus: state.phase, mappingSession: state.session ?? undefined,
      mappingBackend: "fastlio", mappingInputMode: "live", mappingControlReady: calibrated && state.mappingReady && state.aimdkReady,
      mappingCleanupComplete: !["requested", "running", "saving"].includes(state.phase),
      actionBusy: false,
      referenceNavigation: { motionEnabled: !!motion, planningReady, mode: this.mode ?? "point" },
      navigation: this.failedDispatch ? { status: "failed", taskId: motion?.taskId ?? undefined, requiresStop: true, detail: "导航结果未确认，请停止任务" }
        : motion ? { status: motion.status, taskId: motion.taskId ?? undefined, pointId: motion.pointId ?? undefined,
          requiresStop: motion.requiresStop, detail: motion.detail }
        : { status: "idle", detail: !calibrated ? calibrationMessage : planningReady ? "路径规划就绪 · 运动关闭" : "等待重定位 · 运动关闭" },
      ...(calibrated && livePose && state.pose ? { pose: state.pose, poseEvidence: {
        source: "independent-localization", coordinateUnits: "metres", observedAtMs: state.poseAtMs!,
      } } : {}),
      checks: [
        { id: "aimdk", label: "AIMDK", ready: state.aimdkReady },
        { id: "slam", label: "FAST-LIO", ready: state.phase !== "failed" || state.mappingReady },
        { id: "lidar", label: "雷达与 IMU", ready: state.sensorsReady },
        { id: "calibration", label: "本机标定", ready: calibrated, detail: calibrationMessage },
        { id: "odom", label: "GICP 定位", ready: calibrated && !!(livePose && state.pose) },
        { id: "navigation", label: "导航", ready: calibrated && !!motion?.ready && !this.failedDispatch && livePose && !!state.pose,
          detail: !calibrated ? calibrationMessage : !motion ? "运动关闭" : motion.ready ? "运动准入已确认" : "等待地图、定位、障碍与控制状态" },
        { id: "linksoul", label: "灵心", ready: false },
      ],
      motionPresets: [], expressions: [], linkcraftResources: [], updatedAt: new Date(now).toISOString(),
    };
  }

  async getLidarFrame() { return validatedLidarFrame(await this.call("lidar", undefined, 1500, 1024 * 1024)); }
  async listMaps() { return z.array(mapSchema).max(2000).parse(await this.call("maps", undefined, 1500, 1024 * 1024)); }
  async startMapping() { sessionId.parse(await this.call("mapping/start", {}, 65_000)); }
  async stopAndSaveMap(name: string, session?: string) {
    const identity = sessionId.parse(session);
    const map = mapSchema.parse(await this.call("mapping/save", { name, session: identity }, 1_100_000));
    if (map.id !== `fastlio-${identity}` || map.name !== name) throw denied("地图保存回执不匹配", 503);
    return map;
  }
  async discardMapping(session: string) {
    if (await this.call("mapping/discard", { session: sessionId.parse(session) }, 490_000) !== true) {
      throw denied("建图停止未确认", 503);
    }
  }
  async getStoredMapGrid(map: RobotMap) {
    return gridSchema.parse(await this.call(`maps/${mapId.parse(map.id)}/grid`, undefined, 15_000, 16 * 1024 * 1024));
  }
  async activateMap(map: RobotMap, initialPose?: z.infer<typeof pose>) {
    if (await this.call(`maps/${mapId.parse(map.id)}/activate`, { pose: initialPose }, 220_000) !== true) {
      throw denied("重定位未确认", 503);
    }
  }
  async previewPath(map: RobotMap, goal: z.infer<typeof pose>) {
    return z.array(pose).min(1).max(4096).parse(await this.call(`maps/${mapId.parse(map.id)}/plan`,
      { pose: pose.parse(goal) }, 30_000));
  }

  async selectMode(mode: "point" | "nav2") {
    const state = await this.getSnapshot();
    if (this.pendingCommands || state.mapping || state.navigation.requiresStop ||
        ["requested", "running", "paused"].includes(state.navigation.status)) throw denied("请先完成或停止当前任务");
    this.mode = mode;
  }
  async startNavigation(point: MapPoint, map: RobotMap, taskId: string) {
    if (point.mapId !== map.id || this.pendingCommands) throw denied("导航地图或任务冲突");
    this.pendingCommands++;
    try {
      const state = await this.getSnapshot();
      const current = this.last;
      const calibration = state.checks.find((check) => check.id === "calibration");
      if (calibration && !calibration.ready) throw denied(calibration.detail ?? "本机标定未确认");
      if (!state.checks.some((check) => check.id === "navigation" && check.ready) || !current?.ready) {
        throw denied("FAST-LIO 运动尚未准入");
      }
      await this.motionCommand("start", taskId, current.session, {
        mapId: mapId.parse(map.id), pointId: point.id, mode: this.mode,
        pose: pose.parse({ x: point.x, y: point.y, yaw: point.yaw }),
      }, ["requested", "running", "finished"]);
    } finally { this.pendingCommands--; }
  }
  private async motionCommand(route: string, taskId: string, session: string, extra: object, expected: string[]) {
    try {
      const result = motionState.parse(await this.call(`navigation/${route}`, { taskId, session, ...extra }, 21_000));
      if (result.session !== session || result.taskId !== taskId || result.requiresStop || !expected.includes(result.status)) {
        throw denied("导航回执与任务不匹配");
      }
      this.last = result;
      this.failedDispatch = undefined;
    } catch (error) {
      this.failedDispatch = taskId;
      throw error;
    }
  }
  async controlNavigation(action: "pause" | "resume" | "stop", taskId?: string): Promise<void> {
    if (!taskId || this.pendingCommands) throw denied("等待当前导航请求完成");
    this.pendingCommands++;
    try {
      const { navigation: current, calibration } = statusSchema.parse(await this.call("status"));
      if (!current || current.taskId !== taskId || (this.failedDispatch && action !== "stop")) {
        throw denied("控制器没有匹配的任务回执");
      }
      if (action === "resume" && calibration !== "matched") {
        throw denied(calibration ? calibrationDetail[calibration] : "参考服务未报告本机标定，请同步升级");
      }
      await this.motionCommand("control", taskId, current.session, { action }, action === "stop"
        ? ["idle", "finished", "failed"] : action === "pause" ? ["paused"] : ["requested", "running", "finished"]);
    } finally { this.pendingCommands--; }
  }
  async startPresetMotion(): Promise<never> { throw denied("动作服务尚未接入 FAST-LIO 联调配置"); }
  async waitPresetMotion(): Promise<never> { throw denied("没有已确认的动作任务"); }
  async playExpression(): Promise<never> { throw denied("表情服务尚未接入 FAST-LIO 联调配置"); }
  async executeLinkcraftResource(): Promise<never> { throw denied("灵创服务尚未接入 FAST-LIO 联调配置"); }
  async confirmActionsIdle(): Promise<never> { throw denied("动作服务尚未接入 FAST-LIO 联调配置"); }
}
