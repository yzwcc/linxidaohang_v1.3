import type { SavedSlamPointPreview } from "../../shared/contracts";
import { rigidMatrixSchema, sameLocalizationBinding, type LocalizationState, type RigidMatrix } from "../../shared/localization";
import type { PlanarLidarPose } from "./lidar-view";

export type SeedFields = Record<"x" | "y" | "z" | "roll" | "pitch" | "yaw", string>;
export const emptySeedFields = (): SeedFields => ({ x: "", y: "", z: "", roll: "", pitch: "", yaw: "" });

export function withPlanarSeed(fields: SeedFields, pose: PlanarLidarPose): SeedFields {
  if (![pose.x, pose.y, pose.yaw].every(Number.isFinite)) throw new Error("invalid_planar_seed");
  return { ...fields, x: String(pose.x), y: String(pose.y), yaw: String(pose.yaw * 180 / Math.PI) };
}

export function planarSeedDraft(fields: SeedFields): PlanarLidarPose | undefined {
  if ([fields.x, fields.y, fields.yaw].some((value) => !value.trim() || !Number.isFinite(Number(value)))) return;
  return { x: Number(fields.x), y: Number(fields.y), yaw: Number(fields.yaw) * Math.PI / 180 };
}

export function seedDraftReady(fields: SeedFields, binding: LocalizationState["binding"] | undefined,
  state: LocalizationState | undefined, preview: SavedSlamPointPreview | undefined): boolean {
  if (!binding || !state || !preview || !sameLocalizationBinding(binding, state.binding) ||
      preview.mapId !== binding.map_id || preview.receiptSha256 !== binding.receipt_sha256 ||
      preview.pointsSha256 !== binding.points_sha256 || preview.inputMode !== binding.input_mode ||
      !state.workerReady || state.workerClosed || state.requestPending || state.referenceFrame === null ||
      ["stopped", "failed"].includes(state.state)) return false;
  try { seedMatrix(fields); return true; } catch { return false; }
}

/** Intrinsic ZYX: mapFromLidar = translation * Rz(yaw) * Ry(pitch) * Rx(roll). */
export function seedMatrix(fields: SeedFields): RigidMatrix {
  const value = Object.fromEntries(Object.entries(fields).map(([key, text]) => {
    if (!text.trim() || !Number.isFinite(Number(text))) throw new Error("请完整填写有限数值位姿");
    return [key, Number(text)];
  })) as Record<keyof SeedFields, number>;
  const { x, y, z } = value;
  const [roll, pitch, yaw] = [value.roll, value.pitch, value.yaw].map((angle) => angle * Math.PI / 180);
  const [cr, sr, cp, sp, cy, sy] = [Math.cos(roll), Math.sin(roll), Math.cos(pitch), Math.sin(pitch), Math.cos(yaw), Math.sin(yaw)];
  return rigidMatrixSchema.parse([
    [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr, x],
    [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr, y],
    [-sp, cp * sr, cp * cr, z],
    [0, 0, 0, 1],
  ]);
}

export function planarLidarPose(matrix: RigidMatrix) {
  return { x: matrix[0][3], y: matrix[1][3], yaw: Math.atan2(matrix[1][0], matrix[0][0]) };
}
