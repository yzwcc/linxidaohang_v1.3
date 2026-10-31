import { z } from "zod";
import { rigidMatrixSchema } from "./localization";

const sha256 = z.string().regex(/^[a-f0-9]{64}$/);
const frame = z.number().int().positive().max(100_000);
const endpoint = z.object({ frameIndex: frame, mapFromSensor: rigidMatrixSchema }).strict();

export const savedSlamReferencesSchema = z.object({
  source: z.literal("glim-final-lidar-trajectory"),
  sourceFrame: z.literal("lidar_chest_front"),
  coordinateUnits: z.literal("native-raw"),
  trajectorySha256: sha256,
  profileSha256: sha256,
  frames: frame,
  first: endpoint,
  last: endpoint,
}).strict().refine((value) => value.first.frameIndex === 1 && value.last.frameIndex === value.frames,
  "建图参考位姿帧号不一致");

export type SavedSlamReferences = z.infer<typeof savedSlamReferencesSchema>;
