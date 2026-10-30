import { z } from "zod";

const counter = z.number().int().nonnegative().safe();
const frame = counter.positive();
export const localizationSourceFrameSchema = z.literal("lidar_chest_front");
const row = z.tuple([z.number().finite(), z.number().finite(), z.number().finite(), z.number().finite()]);

export const rigidMatrixSchema = z.tuple([row, row, row, row]).refine((matrix) => {
  const close = (a: number, b: number) => Math.abs(a - b) <= 1e-6;
  if (!matrix[3].every((value, index) => close(value, index === 3 ? 1 : 0))) return false;
  for (let a = 0; a < 3; a++) {
    for (let b = 0; b < 3; b++) {
      const dot = matrix[a].slice(0, 3).reduce((sum, value, k) => sum + value * matrix[b][k], 0);
      if (!close(dot, a === b ? 1 : 0)) return false;
    }
  }
  const [a, b, c] = matrix;
  return close(a[0] * (b[1] * c[2] - b[2] * c[1]) -
    a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]), 1);
}, "必须提供有效的刚体位姿矩阵");

export const localizationBindingSchema = z.object({
  session: z.string().regex(/^[a-f0-9]{32}$/),
  input_mode: z.enum(["recorded", "live", "synthetic"]),
  map_id: z.string().regex(/^slam-[a-f0-9]{32}$/),
  receipt_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  points_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  profile_sha256: z.string().regex(/^[a-f0-9]{64}$/),
}).strict();

const sensorNs = z.string().regex(/^[1-9][0-9]{0,18}$/);
export const basePoseStateSchema = z.object({
  status: z.enum(["available", "unavailable"]),
  reason: z.enum(["diagnostic_quality_only", "localization_not_tracking", "awaiting_scan_time",
    "invalid_scan_time", "invalid_kinematic_tf", "input_closed", "timestamped_projection_unavailable"]),
  mapFromBase: rigidMatrixSchema.nullable(),
  lastFrame: counter,
  scanHeaderNs: sensorNs.nullable(),
  effectiveScanNs: sensorNs.nullable(),
  metresPerMapUnit: z.number().finite().positive(),
  coordinateUnits: z.literal("metres-at-declared-scale"),
  coordinateFrame: z.literal("lingxi_saved_map"),
  bodyFrame: z.literal("base_link"),
  physicalScaleValidated: z.literal(false),
  navigationReady: z.literal(false),
}).strict().refine((value) => value.status === "available"
  ? value.reason === "diagnostic_quality_only" && value.mapFromBase !== null && value.lastFrame > 0 &&
    value.scanHeaderNs !== null && value.effectiveScanNs !== null
  : value.mapFromBase === null && value.lastFrame === 0 && value.scanHeaderNs === null && value.effectiveScanNs === null,
"机身诊断位姿状态不一致");

export const localizationStateSchema = z.object({
  binding: localizationBindingSchema,
  generation: counter,
  state: z.enum(["unseeded", "seed_requested", "acquiring", "tracking", "lost", "paused", "stopped", "failed"]),
  reason: z.string().regex(/^[a-z_]{1,100}$/),
  requestPending: z.boolean(),
  workerReady: z.boolean(),
  workerClosed: z.boolean(),
  cleanupComplete: z.boolean(),
  consecutiveMatches: counter,
  lastFrame: counter,
  referenceFrame: frame.nullable(),
  mapFromSensor: rigidMatrixSchema.nullable(),
  coordinateUnits: z.literal("native-raw"),
  sourceFrame: localizationSourceFrameSchema,
  basePose: basePoseStateSchema.optional(),
  localizationReady: z.literal(false),
  navigationReady: z.literal(false),
}).strict().refine((state) => {
  if (state.workerClosed && state.workerReady) return false;
  if (state.cleanupComplete && !state.workerClosed) return false;
  if (state.basePose?.status === "available" &&
    (state.state !== "tracking" || state.basePose.lastFrame !== state.lastFrame)) return false;
  if (state.state === "tracking") {
    return state.workerReady && !state.workerClosed && !state.requestPending &&
      state.consecutiveMatches >= 3 && state.mapFromSensor !== null;
  }
  return state.mapFromSensor === null;
}, "定位状态未通过一致性校验");

const identity = {
  binding: localizationBindingSchema,
  expectedGeneration: counter.max(Number.MAX_SAFE_INTEGER - 1),
};
export const localizationIdentitySchema = z.object(identity).strict();
export const localizationSeedSchema = z.object({
  ...identity, sourceFrame: localizationSourceFrameSchema, referenceFrame: frame, mapFromSensor: rigidMatrixSchema,
}).strict();

export type RigidMatrix = z.infer<typeof rigidMatrixSchema>;
export type LocalizationState = z.infer<typeof localizationStateSchema>;
export type LocalizationIdentity = z.infer<typeof localizationIdentitySchema>;
export type LocalizationSeed = z.infer<typeof localizationSeedSchema>;
export type LocalizationCommand =
  | (LocalizationSeed & { action: "seed" })
  | (LocalizationIdentity & { action: "pause" | "stop" });

export function sameLocalizationBinding(a: LocalizationIdentity["binding"], b: LocalizationIdentity["binding"]) {
  return (Object.keys(a) as (keyof typeof a)[]).every((key) => a[key] === b[key]);
}

export const localizationMapSelectionSchema = z.object({
  mapId: z.string().regex(/^slam-[a-f0-9]{32}$/),
  receiptSha256: z.string().regex(/^[a-f0-9]{64}$/),
}).strict();
const serviceIdentity = {
  serviceId: z.string().regex(/^[a-f0-9]{32}$/),
  expectedGeneration: counter.max(Number.MAX_SAFE_INTEGER - 1),
};
export const localizationStartSchema = z.object({
  ...serviceIdentity, ...localizationMapSelectionSchema.shape,
}).strict();
export const localizationShutdownSchema = z.object({
  ...serviceIdentity, runId: z.string().regex(/^[a-f0-9]{32}$/),
}).strict();
export const localizationRuntimeSchema = z.object({
  backend: z.literal("glim"), inputMode: z.enum(["recorded", "live"]),
  serviceId: serviceIdentity.serviceId, generation: counter,
  runId: z.string().regex(/^[a-f0-9]{32}$/).nullable(),
  phase: z.enum(["idle", "starting", "running", "stopping", "stopped", "failed"]),
  selectedMap: localizationMapSelectionSchema.nullable(), cleanupComplete: z.boolean(), closed: z.boolean(),
  localizationReady: z.literal(false), navigationReady: z.literal(false),
  failureCode: z.enum(["map_selection_unavailable", "localization_process_failed", "process_start_failed",
    "process_startup_deadline_exceeded", "process_progress_deadline_exceeded", "process_shutdown_deadline_exceeded",
    "child_reported_failure", "child_exited_without_confirmed_stop", "child_health_pipe_closed",
    "invalid_child_health_protocol", "child_descendants_outlived_owner", "process_cleanup_failed"]).optional(),
}).strict().refine((value) => {
  if (value.phase === "idle") return value.runId === null && value.selectedMap === null &&
    value.generation === 0 && value.cleanupComplete && !value.failureCode;
  if (!value.runId || !value.selectedMap || value.generation === 0) return false;
  if (value.phase === "failed") return value.failureCode !== undefined;
  if (value.failureCode) return false;
  return value.phase === "stopped" ? value.cleanupComplete : !value.cleanupComplete;
}, "定位进程生命周期状态不一致");

export type LocalizationRuntime = z.infer<typeof localizationRuntimeSchema>;
export type LocalizationLifecycleCommand =
  | (z.infer<typeof localizationStartSchema> & { action: "start" })
  | (z.infer<typeof localizationShutdownSchema> & { action: "shutdown" });

export function sameLocalizationRuntime(a: LocalizationRuntime, b: LocalizationRuntime) {
  return a.serviceId === b.serviceId && a.generation === b.generation && a.runId === b.runId &&
    a.inputMode === b.inputMode && a.phase === b.phase && a.closed === b.closed &&
    a.selectedMap?.mapId === b.selectedMap?.mapId &&
    a.selectedMap?.receiptSha256 === b.selectedMap?.receiptSha256;
}

export function localizationStateMatchesRuntime(state: LocalizationState, runtime: LocalizationRuntime) {
  return !runtime.closed && runtime.phase === "running" && state.binding.input_mode === runtime.inputMode &&
    state.binding.map_id === runtime.selectedMap?.mapId &&
    state.binding.receipt_sha256 === runtime.selectedMap?.receiptSha256;
}

export function localizationLifecycleConfirmed(command: LocalizationLifecycleCommand, result: LocalizationRuntime) {
  if (result.closed || result.serviceId !== command.serviceId || result.generation !== command.expectedGeneration + 1) return false;
  return command.action === "start"
    ? result.phase === "starting" && result.runId !== null && !result.cleanupComplete &&
      result.selectedMap?.mapId === command.mapId && result.selectedMap?.receiptSha256 === command.receiptSha256
    : result.runId === command.runId && ["stopping", "stopped", "failed"].includes(result.phase);
}
