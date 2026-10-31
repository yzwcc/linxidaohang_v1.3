import { z } from "zod";

const hash = z.string().regex(/^[0-9a-f]{64}$/);
const epoch = z.string().regex(/^[0-9a-f]{32}$/);
const id = z.string().regex(/^[A-Za-z0-9_-]{1,100}$/);
export const offlineIdentitySchema = z.object({
  map_id: z.string().regex(/^slam-[0-9a-f]{32}$/), receipt_sha256: hash,
  metric_evidence_sha256: hash, traversability_sha256: hash,
}).strict();
export const offlineNavigationStateSchema = z.object({
  protocol: z.literal(1), session: epoch, sequence: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
  identity: offlineIdentitySchema,
  phase: z.enum(["idle", "requested", "running", "stopping", "paused", "finished", "failed"]),
  detail: z.string().max(160), ticket: z.object({ task_id: id, epoch }).strict().nullable(),
  pointId: id.nullable(), requiresStop: z.boolean(), outputQuiescent: z.boolean(),
  pose: z.object({ x: z.number().finite(), y: z.number().finite(), yaw: z.number().finite() }).strict(),
  commandCount: z.number().int().nonnegative(),
  offlineOnly: z.literal(true), syntheticInputs: z.literal(true),
  physicalMotionSent: z.literal(false), navigationReady: z.literal(false),
}).strict().superRefine((state, context) => {
  if ((state.ticket === null) !== (state.pointId === null) ||
      (["running", "requested", "stopping", "paused", "finished"].includes(state.phase) && !state.ticket) ||
      (["idle", "paused", "finished"].includes(state.phase) && !state.outputQuiescent)) {
    context.addIssue({ code: "custom", message: "inconsistent_offline_navigation_state" });
  }
});
export const offlineSceneSchema = z.object({
  protocol: z.literal(1), session: epoch, identity: offlineIdentitySchema,
  case: z.enum(["open", "blocked", "detour", "appearing-wall"]),
  grid: z.object({ width: z.number().int().min(1).max(256), height: z.number().int().min(1).max(256),
    resolution: z.number().positive().max(1), originX: z.number().finite(), originY: z.number().finite(),
    cells: z.array(z.union([z.literal(-1), z.literal(0), z.literal(100)])).max(65536) }).strict(),
  points: z.array(z.number().finite()).max(18000),
  offlineOnly: z.literal(true), syntheticInputs: z.literal(true), physicalMotionSent: z.literal(false),
  navigationReady: z.literal(false),
}).strict().refine((value) => value.grid.cells.length === value.grid.width * value.grid.height && value.points.length % 3 === 0);

export type OfflineNavigationState = z.infer<typeof offlineNavigationStateSchema>;
export type OfflineScene = z.infer<typeof offlineSceneSchema>;
export type OfflineNavigationCommand = {
  action: "start" | "pause" | "resume" | "stop"; session: string;
  identity: OfflineNavigationState["identity"]; expectedEpoch: string | null;
  taskId: string; pointId?: string; goal?: { x: number; y: number; yaw: number };
};
