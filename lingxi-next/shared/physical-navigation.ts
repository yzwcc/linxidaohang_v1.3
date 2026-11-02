import { z } from "zod";

const sha256 = z.string().regex(/^[a-f0-9]{64}$/);
const epoch = z.string().regex(/^[a-f0-9]{32}$/);
const taskId = z.string().regex(/^[A-Za-z0-9_-]{1,120}$/);
const mapId = z.string().regex(/^slam-[a-f0-9]{32}$/);

export const physicalNavigationEvidenceSchema = z.object({
  mapId,
  receiptSha256: sha256,
  calibrationSha256: sha256,
  footprintSha256: sha256,
}).strict();

export const physicalNavigationChecksSchema = z.object({
  nav2Lifecycle: z.boolean(),
  localization: z.boolean(),
  tf: z.boolean(),
  costmap: z.boolean(),
  terrain: z.boolean(),
  footprint: z.boolean(),
  velocityOutput: z.boolean(),
}).strict();

const ticketSchema = z.object({ task_id: taskId, epoch }).strict();
const poseSchema = z.object({ x: z.number().finite(), y: z.number().finite(), yaw: z.number().finite() }).strict();

export const physicalNavigationMapActivationSchema = z.object({
  action: z.literal("activate_map"),
  session: epoch,
  mapId,
  // The bridge owns the evidence for a map that is not currently selected.
  // Callers include evidence when re-activating the current map; a map switch
  // may omit it and must receive a fresh evidence-bound state in response.
  evidence: physicalNavigationEvidenceSchema.optional(),
  initialPose: poseSchema.optional(),
}).strict();

export const physicalNavigationStateSchema = z.object({
  protocol: z.literal(1),
  source: z.literal("ros2-nav2"),
  session: epoch,
  sequence: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
  identity: physicalNavigationEvidenceSchema,
  phase: z.enum(["idle", "requested", "running", "stopping", "paused", "finished", "failed"]),
  detail: z.string().max(240),
  ticket: ticketSchema.nullable(),
  pointId: z.string().regex(/^[A-Za-z0-9_-]{1,120}$/).nullable(),
  requiresStop: z.boolean(),
  outputQuiescent: z.boolean(),
  pose: poseSchema,
  checks: physicalNavigationChecksSchema,
  navigationReady: z.boolean(),
  physicalMotionSent: z.boolean(),
}).strict().superRefine((state, context) => {
  const admitted = Object.values(state.checks).every(Boolean) && !state.requiresStop;
  if (state.navigationReady !== admitted) {
    context.addIssue({ code: "custom", message: "navigation_ready_must_match_all_admission_gates" });
  }
  if ((state.ticket === null) !== (state.pointId === null) ||
      (["requested", "running", "stopping", "paused", "finished"].includes(state.phase) && !state.ticket) ||
      (["idle", "paused", "finished"].includes(state.phase) && !state.outputQuiescent)) {
    context.addIssue({ code: "custom", message: "inconsistent_physical_navigation_state" });
  }
});

export type PhysicalNavigationState = z.infer<typeof physicalNavigationStateSchema>;
export type PhysicalNavigationEvidence = z.infer<typeof physicalNavigationEvidenceSchema>;
export type PhysicalNavigationMapActivation = z.infer<typeof physicalNavigationMapActivationSchema>;
export type PhysicalNavigationCommand = {
  action: "start" | "pause" | "resume" | "stop";
  session: string;
  expectedEpoch: string | null;
  taskId: string;
  mapId: string;
  pointId?: string;
  goal?: { x: number; y: number; yaw: number };
  evidence: PhysicalNavigationEvidence;
};

export function navigationAdmission(state: PhysicalNavigationState): { ok: true } | { ok: false; reason: string } {
  if (!state.navigationReady) return { ok: false, reason: "navigation_admission_gates_not_ready" };
  if (state.requiresStop) return { ok: false, reason: "navigation_requires_confirmed_stop" };
  if (!state.outputQuiescent && state.phase === "idle") return { ok: false, reason: "navigation_output_not_quiescent" };
  return { ok: true };
}
