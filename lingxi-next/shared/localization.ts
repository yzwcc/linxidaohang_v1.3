import { z } from "zod";

const counter = z.number().int().nonnegative().safe();
const frame = counter.positive();
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
  sourceFrame: z.literal("IMU"),
  localizationReady: z.literal(false),
  navigationReady: z.literal(false),
}).strict().refine((state) => {
  if (state.workerClosed && state.workerReady) return false;
  if (state.cleanupComplete && !state.workerClosed) return false;
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
  ...identity, referenceFrame: frame, mapFromSensor: rigidMatrixSchema,
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
