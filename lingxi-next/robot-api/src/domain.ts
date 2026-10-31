import { z } from "zod";
import type { LidarFrame, MapPoint, OccupancyGrid, RobotMap, RobotState, TourTask } from "../../shared/contracts";

export const pointSchema = z.object({
  id: z.string().min(1).max(100).optional(),
  mapId: z.string().min(1).max(100),
  name: z.string().trim().min(1).max(80),
  x: z.number().finite().min(-1000).max(1000),
  y: z.number().finite().min(-1000).max(1000),
  yaw: z.number().finite().min(-Math.PI * 2).max(Math.PI * 2),
  isStart: z.boolean().default(false),
});

export const tourStepSchema = z.object({
  id: z.string().min(1).max(100),
  pointId: z.string().min(1).max(100),
  waitSeconds: z.number().int().min(0).max(300),
  narration: z.string().max(4000),
  presetMotion: z.string().max(100).optional(),
  expression: z.string().max(80).optional(),
  linkcraftResourceId: z.string().max(160).optional(),
});

export const tourSchema = z.object({
  id: z.string().min(1).max(100),
  name: z.string().trim().min(1).max(120),
  mapId: z.string().min(1).max(100),
  repeatCount: z.number().int().min(1).max(20),
  idleReturnSeconds: z.number().int().min(0).max(300).default(30),
  steps: z.array(tourStepSchema).min(1).max(200),
  updatedAt: z.string().datetime().or(z.string().min(1).max(40)),
  requiresReview: z.boolean().optional(),
});

export const aiSettingsSchema = z.object({
  provider: z.enum(["ark", "openai-compatible"]),
  model: z.string().trim().min(1).max(200),
  baseUrl: z.string().max(500).optional().or(z.literal("")).refine((value) => {
    if (!value) return true;
    try {
      const url = new URL(value);
      return url.protocol === "https:" ||
        (url.protocol === "http:" && ["localhost", "127.0.0.1", "::1"].includes(url.hostname));
    } catch {
      return false;
    }
  }, "API 地址必须使用 HTTPS，HTTP 仅允许本机地址"),
});

export type AiSettings = z.infer<typeof aiSettingsSchema>;

export type DatabaseState = {
  schemaVersion: 1;
  robotName: string;
  maps: RobotMap[];
  points: MapPoint[];
  tours: TourTask[];
  activeMapId?: string;
  selectedTourId?: string;
  idleReturnSeconds: number;
  jobRecords: { task: string; point: string; at: string; reason: string }[];
  ai: AiSettings;
};

export function emptyDatabaseState(): DatabaseState {
  return {
    schemaVersion: 1,
    robotName: "Lingxi X2",
    maps: [],
    points: [],
    tours: [],
    idleReturnSeconds: 30,
    jobRecords: [],
    ai: { provider: "ark", model: "doubao-seed-2-0-mini" },
  };
}

export interface RobotAdapter {
  readonly demoMode: boolean;
  getSnapshot(): Promise<RobotSnapshot>;
  getLidarFrame?(): Promise<LidarFrame>;
  listMaps(): Promise<RobotMap[]>;
  startMapping(): Promise<void>;
  stopAndSaveMap(name: string): Promise<RobotMap>;
  getStoredMapGrid(map: RobotMap): Promise<OccupancyGrid>;
  activateMap(map: RobotMap, initialPose?: { x: number; y: number; yaw: number }): Promise<void>;
  startNavigation(point: MapPoint, map: RobotMap, taskId: string): Promise<void>;
  controlNavigation(action: "pause" | "resume" | "stop", taskId?: string): Promise<void>;
  startPresetMotion(name: string, monitorCompletion?: boolean): Promise<string>;
  waitPresetMotion(taskId: string): Promise<void>;
  playExpression(name: string): Promise<void>;
  executeLinkcraftResource(id: string): Promise<void>;
  confirmActionsIdle(): Promise<void>;
}

export type RobotSnapshot = Pick<
  RobotState,
  | "connected"
  | "checks"
  | "mapping"
  | "mappingStatus"
  | "mappingBackend"
  | "mappingInputMode"
  | "mappingCaptureLimitSeconds"
  | "mappingSession"
  | "mappingProgress"
  | "mappingFailure"
  | "mappingCleanupComplete"
  | "mappingControlReady"
  | "actionBusy"
  | "actionStatus"
  | "pose"
  | "navigation"
  | "mapGrid"
  | "motionPresets"
  | "expressions"
  | "linkcraftResources"
  | "updatedAt"
>;
