import { z } from "zod";
import type { LidarFrame } from "../../shared/contracts";

const pose = z.object({ x: z.number().finite(), y: z.number().finite(), yaw: z.number().finite() });
const frameSchema = z.object({
  sequence: z.number().int().nonnegative(),
  epoch: z.string().min(1).max(100),
  source: z.enum(["laser-scan", "point-cloud"]),
  frameId: z.string().min(1).max(200),
  sensorFrame: z.string().min(1).max(200),
  fixedFrame: z.boolean(),
  stampMs: z.number().finite().positive(),
  receivedAtMs: z.number().finite().positive(),
  points: z.array(z.number().finite()).max(24_000).refine((points) => points.length % 3 === 0),
  robotPose: pose.optional(),
  detail: z.string().max(300).optional(),
});

export function validatedLidarFrame(value: unknown, now = Date.now()): LidarFrame {
  const frame = frameSchema.parse(value);
  if (Math.abs(now - frame.receivedAtMs) > 2000) throw new Error("雷达数据已过期");
  return frame;
}
