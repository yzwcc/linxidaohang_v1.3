import { rigidMatrixSchema, type RigidMatrix } from "../../shared/localization";

export type SeedFields = Record<"x" | "y" | "z" | "roll" | "pitch" | "yaw", string>;

/** Intrinsic ZYX: mapFromImu = translation * Rz(yaw) * Ry(pitch) * Rx(roll). */
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

export function planarImuPose(matrix: RigidMatrix) {
  return { x: matrix[0][3], y: matrix[1][3], yaw: Math.atan2(matrix[1][0], matrix[0][0]) };
}
