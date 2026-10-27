import type { LidarFrame, MapPoint, RobotMap } from "../../../shared/contracts";
import type { OccupancyGrid } from "../../../shared/contracts";
import type { RobotAdapter, RobotSnapshot } from "../domain";

export class AimdkAdapter implements RobotAdapter {
  readonly demoMode = false;

  constructor(
    private readonly bridgeUrl = "http://127.0.0.1:18082/v1",
    private readonly bridgeToken = process.env.LINGXI_AIMDK_BRIDGE_TOKEN ?? "",
  ) {}

  async getSnapshot(): Promise<RobotSnapshot> {
    return this.request("/state");
  }

  async getLidarFrame(): Promise<LidarFrame> {
    return this.request("/lidar", {}, 1200);
  }

  async listMaps(): Promise<RobotMap[]> {
    const result = await this.request<{ maps: RobotMap[] }>("/maps");
    return result.maps;
  }

  async startMapping(): Promise<void> {
    await this.request("/mapping/start", { method: "POST" });
  }

  async stopAndSaveMap(name: string): Promise<RobotMap> {
    const result = await this.request<{ map: RobotMap; confirmed: boolean }>(
      "/mapping/stop-save",
      { method: "POST", body: { name } },
      125_000,
    );
    if (!result.confirmed || !result.map?.mapId) throw new Error("AIMDK 未确认地图保存完成");
    return result.map;
  }

  async getStoredMapGrid(map: RobotMap): Promise<OccupancyGrid> {
    return this.request(`/maps/${encodeURIComponent(map.id)}/grid`, {}, 15_000);
  }

  async activateMap(map: RobotMap, initialPose?: { x: number; y: number; yaw: number }): Promise<void> {
    const result = await this.request<{ accepted: boolean; confirmed: boolean; mapId: number }>(
      `/maps/${encodeURIComponent(map.id)}/activate`,
      { method: "POST", body: { initialPose } },
      40_000,
    );
    if (result.accepted !== true || result.confirmed !== true ||
        !Number.isSafeInteger(result.mapId) || result.mapId <= 0) {
      throw new Error("AIMDK 未确认切换到目标地图");
    }
    map.mapId = result.mapId;
    map.active = true;
  }

  async startNavigation(point: MapPoint, map: RobotMap, taskId: string): Promise<void> {
    if (!map.mapId) throw new Error("地图未通过 AIMDK 服务确认");
    await this.request("/navigation/start", {
      method: "POST",
      body: { taskId, mapId: map.mapId, target: { x: point.x, y: point.y, yaw: point.yaw } },
    });
  }

  async controlNavigation(action: "pause" | "resume" | "stop", taskId?: string): Promise<void> {
    const result = await this.request<{ confirmed: boolean }>(
      "/navigation/control", { method: "POST", body: { action, taskId } }, 5000,
    );
    if (!result.confirmed) throw new Error("PNC 未确认导航控制请求");
  }

  async runPresetMotion(name: string): Promise<void> {
    await this.request("/motions/preset", { method: "POST", body: { name } });
  }

  private async request<T = void>(
    endpoint: string,
    init: { method?: string; body?: unknown } = {},
    timeoutMs = 3000,
  ): Promise<T> {
    const response = await fetch(`${this.bridgeUrl}${endpoint}`, {
      method: init.method ?? "GET",
      headers: {
        ...(init.body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(this.bridgeToken ? { Authorization: `Bearer ${this.bridgeToken}` } : {}),
      },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      signal: AbortSignal.timeout(timeoutMs),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body.message === "string" ? body.message : `AIMDK adapter error (${response.status})`);
    return body as T;
  }
}
