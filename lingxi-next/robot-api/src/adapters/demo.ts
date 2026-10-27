import { randomUUID } from "node:crypto";
import type { LidarFrame, MapPoint, OccupancyGrid, RobotMap } from "../../../shared/contracts";
import type { RobotAdapter, RobotSnapshot } from "../domain";

function demoGrid() {
  const width = 72;
  const height = 52;
  const cells = Array<number>(width * height).fill(0);
  for (let x = 0; x < width; x += 1) {
    cells[5 * width + x] = 100;
    cells[46 * width + x] = 100;
  }
  for (let y = 5; y <= 46; y += 1) {
    cells[y * width + 5] = 100;
    cells[y * width + 66] = 100;
  }
  for (let x = 16; x < 40; x += 1) cells[31 * width + x] = 100;
  for (let y = 18; y < 32; y += 1) cells[y * width + 40] = 100;
  for (let y = 12; y < 22; y += 1) {
    for (let x = 49; x < 61; x += 1) cells[y * width + x] = 100;
  }
  return { width, height, resolution: 0.1, originX: 0, originY: 0, cells, stamp: Date.now() };
}

const seededMaps: RobotMap[] = [
  { id: "demo-hall", name: "一层展厅", mapId: 1001, active: true, createdAt: "2026-10-24" },
  { id: "demo-lab", name: "体验区", mapId: 1002, active: false, createdAt: "2026-10-21" },
];

export class DemoAdapter implements RobotAdapter {
  readonly demoMode = true;
  private mapping = false;
  private activeMapId = "demo-hall";
  private navigation: RobotSnapshot["navigation"] = { status: "idle" };
  private maps = structuredClone(seededMaps);
  private mapTimer?: NodeJS.Timeout;
  private lidarSequence = 0;
  private lidarEpoch = randomUUID();

  async getLidarFrame(): Promise<LidarFrame> {
    // Simulated laser rays intersect continuous walls, independently of demoGrid.
    const time = Date.now() / 1000;
    const robot = { x: 3.1 + Math.sin(time * 0.15) * 0.12, y: 2.4, yaw: 0.3 + Math.sin(time * 0.2) * 0.08 };
    const walls = [[0.5, 0.5, 6.6, 0.5], [6.6, 0.5, 6.6, 4.6], [6.6, 4.6, 0.5, 4.6],
      [0.5, 4.6, 0.5, 0.5], [1.6, 3.1, 4, 3.1], [4, 1.8, 4, 3.1],
      [4.9, 1.2, 6.1, 1.2], [6.1, 1.2, 6.1, 2.2], [6.1, 2.2, 4.9, 2.2], [4.9, 2.2, 4.9, 1.2]];
    const points: number[] = [];
    for (let ray = 0; ray < 1080; ray += 1) {
      const angle = ray * Math.PI * 2 / 1080 + robot.yaw;
      const dx = Math.cos(angle), dy = Math.sin(angle);
      let distance = Infinity;
      for (const [x1, y1, x2, y2] of walls) {
        const sx = x2 - x1, sy = y2 - y1;
        const cross = dx * sy - dy * sx;
        if (Math.abs(cross) < 1e-9) continue;
        const qx = x1 - robot.x, qy = y1 - robot.y;
        const along = (qx * sy - qy * sx) / cross;
        const segment = (qx * dy - qy * dx) / cross;
        if (along > 0 && segment >= 0 && segment <= 1) distance = Math.min(distance, along);
      }
      if (!Number.isFinite(distance)) continue;
      distance += Math.sin(ray * 13.7 + time * 4) * 0.008;
      points.push(robot.x + dx * distance, robot.y + dy * distance, 0.25);
    }
    return { sequence: ++this.lidarSequence, epoch: this.lidarEpoch, source: "laser-scan",
      frameId: "map", sensorFrame: "demo_laser", fixedFrame: true, stampMs: Date.now(),
      receivedAtMs: Date.now(), points, robotPose: robot };
  }

  async getSnapshot(): Promise<RobotSnapshot> {
    return {
      connected: true,
      checks: [
        { id: "aimdk", label: "AIMDK", ready: true, detail: "模拟 v1.1.0" },
        { id: "slam", label: "SLAM", ready: true, detail: this.mapping ? "采集中" : "在线" },
        { id: "lidar", label: "激光雷达", ready: true, detail: "模拟 10 Hz" },
        { id: "odom", label: "里程计", ready: true, detail: "模拟实时" },
        { id: "navigation", label: "原生导航", ready: true, detail: "模拟接口" },
        { id: "linksoul", label: "灵心", ready: false, detail: "演示模式不连接云端" },
      ],
      mapping: this.mapping,
      mappingStatus: this.mapping ? "running" : "idle",
      actionBusy: false,
      actionStatus: undefined,
      pose: { x: 3.1, y: 2.4, yaw: 0.3 },
      navigation: this.navigation,
      mapGrid: demoGrid(),
      motionPresets: ["挥手", "点头", "比心"],
      expressions: ["微笑", "惊喜", "平静"],
      linkcraftResources: [],
      updatedAt: new Date().toISOString(),
    };
  }

  async listMaps(): Promise<RobotMap[]> {
    return structuredClone(this.maps.map((map) => ({ ...map, active: map.id === this.activeMapId })));
  }

  async getStoredMapGrid(_map: RobotMap): Promise<OccupancyGrid> {
    return demoGrid();
  }

  async startMapping(): Promise<void> {
    this.lidarEpoch = randomUUID();
    this.mapping = true;
  }

  async stopAndSaveMap(name: string): Promise<RobotMap> {
    if (!this.mapping) throw new Error("当前没有正在采集的地图");
    this.mapping = false;
    const map = { id: randomUUID(), name, mapId: Math.floor(Date.now() / 1000), active: false, createdAt: new Date().toISOString() };
    this.maps.push(map);
    return map;
  }

  async activateMap(map: RobotMap): Promise<void> {
    this.lidarEpoch = randomUUID();
    this.activeMapId = map.id;
    this.maps = this.maps.map((item) => ({ ...item, active: item.id === map.id }));
  }

  async startNavigation(point: MapPoint, _map: RobotMap, taskId: string): Promise<void> {
    if (this.navigation.status === "running") throw new Error("已有导航任务正在执行");
    this.navigation = { status: "running", taskId, pointId: point.id, detail: `正在前往 ${point.name}` };
    if (this.mapTimer) clearTimeout(this.mapTimer);
    this.mapTimer = setTimeout(() => {
      if (this.navigation.taskId === taskId) {
        this.navigation = { status: "finished", taskId, pointId: point.id, detail: `已到达 ${point.name}` };
      }
    }, 1400);
  }

  async controlNavigation(action: "pause" | "resume" | "stop", taskId?: string): Promise<void> {
    if (taskId && this.navigation.taskId && taskId !== this.navigation.taskId) return;
    if (action === "stop") {
      if (this.mapTimer) clearTimeout(this.mapTimer);
      this.navigation = { status: "idle", detail: "任务已停止" };
    } else {
      this.navigation = {
        ...this.navigation,
        status: action === "pause" ? "paused" : "running",
        detail: action === "pause" ? "已暂停" : "继续执行",
      };
    }
  }

  async startPresetMotion(name: string, _monitorCompletion = true): Promise<string> {
    const snapshot = await this.getSnapshot();
    if (!snapshot.motionPresets.includes(name)) throw new Error("该预设动作未配置");
    return randomUUID();
  }

  async waitPresetMotion(_taskId: string): Promise<void> {
    return undefined;
  }

  async playExpression(name: string): Promise<void> {
    const snapshot = await this.getSnapshot();
    if (!snapshot.expressions.includes(name)) throw new Error("该表情未配置");
  }

  async executeLinkcraftResource(id: string): Promise<void> {
    const snapshot = await this.getSnapshot();
    if (!snapshot.linkcraftResources.some((resource) => resource.id === id)) {
      throw new Error("该灵创资源未配置");
    }
  }

  async confirmActionsIdle(): Promise<void> {
    return undefined;
  }
}
