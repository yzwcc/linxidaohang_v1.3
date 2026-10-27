export type HealthCheck = {
  id: string;
  label: string;
  ready: boolean;
  detail?: string;
};

export type RobotPose = {
  x: number;
  y: number;
  yaw: number;
};

export type OccupancyGrid = {
  width: number;
  height: number;
  resolution: number;
  originX: number;
  originY: number;
  originYaw?: number;
  cells: number[];
  stamp: number;
};

export type LidarFrame = {
  sequence: number;
  epoch: string;
  source: "laser-scan" | "point-cloud";
  frameId: string;
  sensorFrame: string;
  fixedFrame: boolean;
  stampMs: number;
  receivedAtMs: number;
  points: number[]; // Flat XYZ coordinates in frameId, in metres.
  robotPose?: RobotPose;
  detail?: string;
};

export type LidarEvent =
  | { type: "lidar"; frame: LidarFrame }
  | { type: "lidar-unavailable"; message: string };

export type RobotMap = {
  id: string;
  name: string;
  mapId?: number;
  active: boolean;
  imageUrl?: string;
  resolution?: number;
  origin?: [number, number, number];
  createdAt?: string;
};

export type MapPoint = {
  id: string;
  mapId: string;
  name: string;
  x: number;
  y: number;
  yaw: number;
  isStart: boolean;
};

export type TourStep = {
  id: string;
  pointId: string;
  waitSeconds: number;
  narration: string;
  presetMotion?: string;
  expression?: string;
  linkcraftResourceId?: string;
};

export type TourTask = {
  id: string;
  name: string;
  mapId: string;
  repeatCount: number;
  steps: TourStep[];
  idleReturnSeconds: number;
  updatedAt: string;
  requiresReview?: boolean;
};

export type NavigationState = {
  status: "idle" | "requested" | "running" | "paused" | "finished" | "failed";
  requiresStop?: boolean;
  taskId?: string;
  pointId?: string;
  detail?: string;
};

export type RobotState = {
  connected: boolean;
  demoMode: boolean;
  robotName: string;
  checks: HealthCheck[];
  mapping: boolean;
  mappingStatus?: "idle" | "requested" | "running" | "saving" | "failed";
  activeMapId?: string;
  pose?: RobotPose;
  navigation: NavigationState;
  mapGrid?: OccupancyGrid;
  maps: RobotMap[];
  points: MapPoint[];
  tours: TourTask[];
  selectedTourId?: string;
  idleReturnSeconds?: number;
  motionPresets: string[];
  expressions: string[];
  linkcraftResources: { id: string; name: string }[];
  updatedAt: string;
};

export type VoiceEvent =
  | { eventId: string; sessionId: string; kind: "wake"; occurredAt: number }
  | { eventId: string; sessionId: string; kind: "session-ended"; occurredAt: number }
  | {
      eventId: string;
      sessionId: string;
      kind: "recognition";
      transcript: string;
      confidence: number;
      isFinal: boolean;
      occurredAt: number;
    };

export type VoiceReply = {
  accepted: boolean;
  category: "command" | "question" | "clarification" | "rejected";
  text: string;
  commandId?: string;
  latencyMs: number;
  speechDelivered?: boolean;
  firstAudioMs?: number;
  deliveryError?: string;
};

export type PublicSettings = {
  provider: "ark" | "openai-compatible";
  model: string;
  baseUrl?: string;
  credentialConfigured: boolean;
  retrieverConfigured: boolean;
  firstAudioTargetMs: 2000;
};
