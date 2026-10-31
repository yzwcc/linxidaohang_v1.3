import type { LidarFrame, MapPoint, RobotMap } from "../../../shared/contracts";
import { offlineNavigationStateSchema, offlineSceneSchema, type OfflineNavigationState, type OfflineScene } from "../../../shared/offline-navigation";
import type { RobotAdapter, RobotSnapshot } from "../domain";
import type { OfflineNavigationBackend } from "../offline-navigation-backend";

const denied = () => Object.assign(new Error("离线导航工作区不连接机器人、建图或动作接口"), { statusCode: 409 });
const sameIdentity = (a: OfflineScene["identity"], b: OfflineScene["identity"]) =>
  a.map_id === b.map_id && a.receipt_sha256 === b.receipt_sha256 &&
  a.metric_evidence_sha256 === b.metric_evidence_sha256 && a.traversability_sha256 === b.traversability_sha256;

export class OfflineNavigationAdapter implements RobotAdapter {
  readonly demoMode = true;
  private sceneValue?: OfflineScene;
  private scenePending?: Promise<OfflineScene>;
  private sceneAt = 0;
  private previous?: OfflineNavigationState;
  private uncertain = false;
  private lidarSequence = 0;

  constructor(private readonly backend: OfflineNavigationBackend) {
    if (backend.offlineOnly !== true) throw new Error("explicit_offline_backend_required");
  }

  private async state() {
    try {
      const value = offlineNavigationStateSchema.parse(await this.backend.state());
      if (this.previous && (value.session !== this.previous.session || value.sequence <= this.previous.sequence ||
          !sameIdentity(value.identity, this.previous.identity))) throw denied();
      if (this.sceneValue && (value.session !== this.sceneValue.session ||
          !sameIdentity(value.identity, this.sceneValue.identity))) throw denied();
      this.previous = value;
      return value;
    } catch (error) { this.uncertain = true; throw error; }
  }

  private async scene() {
    if (this.scenePending) return this.scenePending;
    if (!this.sceneValue || performance.now() - this.sceneAt >= 150) {
      this.scenePending = (async () => {
        try {
          const value = offlineSceneSchema.parse(await this.backend.scene());
          const bound = this.previous ?? this.sceneValue;
          if (bound && (value.session !== bound.session || !sameIdentity(value.identity, bound.identity))) throw denied();
          this.sceneValue = value;
          this.sceneAt = performance.now();
          return value;
        } catch (error) { this.uncertain = true; throw error; }
        finally { this.scenePending = undefined; }
      })();
      return this.scenePending;
    }
    return this.sceneValue;
  }

  async getSnapshot(): Promise<RobotSnapshot> {
    const [state, scene] = await Promise.all([this.state(), this.scene()]);
    if (state.session !== scene.session || !sameIdentity(state.identity, scene.identity)) {
      this.uncertain = true; throw denied();
    }
    return {
      connected: true, mapping: false, actionBusy: false,
      navigationExecution: { backend: "nav2-offline", syntheticInputs: true, physicalMotionSent: false, navigationReady: false },
      checks: [
        { id: "navigation", label: "离线 Nav2", ready: !this.uncertain && !state.requiresStop,
          detail: "合成平面场景，非机器人导航验收" },
        ...["aimdk", "slam", "lidar", "odom", "linksoul"].map((id) => ({ id, label: id, ready: false, detail: "未连接实机" })),
      ],
      navigation: { status: state.phase === "stopping" ? "requested" : state.phase,
        taskId: state.ticket?.task_id, pointId: state.pointId ?? undefined,
        requiresStop: this.uncertain || state.requiresStop, detail: state.detail },
      pose: state.pose, mapGrid: { ...scene.grid, stamp: Date.now() },
      motionPresets: [], expressions: [], linkcraftResources: [], updatedAt: new Date().toISOString(),
    };
  }

  async listMaps(): Promise<RobotMap[]> {
    const scene = await this.scene();
    return [{ id: scene.identity.map_id, name: `离线 Nav2 · ${scene.case}`, active: true }];
  }
  async getStoredMapGrid(map: RobotMap) {
    const scene = await this.scene();
    if (map.id !== scene.identity.map_id) throw denied();
    return { ...scene.grid, stamp: Date.now() };
  }
  async getLidarFrame(): Promise<LidarFrame> {
    const state = await this.state(), scene = await this.scene();
    return { sequence: ++this.lidarSequence, epoch: state.session, source: "point-cloud",
      frameId: "offline_map", sensorFrame: "synthetic_world", fixedFrame: true,
      stampMs: Date.now(), receivedAtMs: Date.now(), points: scene.points, robotPose: state.pose,
      detail: "离线合成障碍物点云，非实机雷达" };
  }
  async startNavigation(point: MapPoint, map: RobotMap, taskId: string) {
    const state = await this.state();
    if (this.uncertain || state.requiresStop || point.mapId !== state.identity.map_id || map.id !== point.mapId) throw denied();
    await this.command(state, "start", taskId, point);
  }
  async controlNavigation(action: "pause" | "resume" | "stop", taskId?: string) {
    const state = await this.state();
    if (!taskId || taskId !== state.ticket?.task_id || (this.uncertain && action !== "stop")) throw denied();
    await this.command(state, action, taskId);
    if (action === "stop") this.uncertain = false;
  }
  private async command(state: OfflineNavigationState, action: "start" | "pause" | "resume" | "stop", taskId: string, point?: MapPoint) {
    try {
      const result = offlineNavigationStateSchema.parse(await this.backend.command({ action, taskId,
        session: state.session, identity: state.identity, expectedEpoch: state.ticket?.epoch ?? null,
        ...(point ? { pointId: point.id, goal: { x: point.x, y: point.y, yaw: Math.atan2(Math.sin(point.yaw), Math.cos(point.yaw)) } } : {}),
      }));
      if (result.session !== state.session || result.sequence <= state.sequence ||
          !sameIdentity(result.identity, state.identity) || result.ticket?.task_id !== taskId ||
          (point && result.pointId !== point.id) ||
          (["start", "resume"].includes(action) && (result.phase !== "requested" || result.ticket.epoch === state.ticket?.epoch)) ||
          (["pause", "stop"].includes(action) && (!result.outputQuiescent || result.requiresStop ||
            result.phase !== (action === "pause" ? "paused" : "idle") || result.ticket.epoch !== state.ticket?.epoch))) throw denied();
      if (this.previous && result.sequence <= this.previous.sequence) throw denied();
      this.previous = result;
    } catch (error) { this.uncertain = true; throw error; }
  }
  async startMapping(): Promise<never> { throw denied(); }
  async stopAndSaveMap(_name: string): Promise<never> { throw denied(); }
  async activateMap(_map: RobotMap): Promise<never> { throw denied(); }
  async startPresetMotion(_name: string): Promise<never> { throw denied(); }
  async waitPresetMotion(_id: string): Promise<never> { throw denied(); }
  async playExpression(_name: string): Promise<never> { throw denied(); }
  async executeLinkcraftResource(_id: string): Promise<never> { throw denied(); }
  async confirmActionsIdle(): Promise<never> { throw denied(); }
}
