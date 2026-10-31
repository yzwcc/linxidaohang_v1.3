import { rigidMatrixSchema, type RigidMatrix } from "../../shared/localization";
import type { SavedSlamReferences } from "../../shared/slam-reference";

export const MAX_TRAJECTORY_BYTES = 8 * 1024 * 1024;

/** GLIM's final map trajectory is TUM: time, XYZ, quaternion XYZW (six decimals). */
export function mappingEndpointPoses(text: string, frames: number): Pick<SavedSlamReferences, "first" | "last"> {
  if (!Number.isSafeInteger(frames) || frames < 1 || frames > 100_000 ||
      Buffer.byteLength(text) > MAX_TRAJECTORY_BYTES) throw new Error("bounded_mapping_trajectory_required");
  const rows = text.trim().split(/\r?\n/);
  if (rows.length !== frames) throw new Error("complete_mapping_trajectory_required");
  let previous = -1n, first: RigidMatrix | undefined, last: RigidMatrix | undefined;
  for (const row of rows) {
    const fields = row.trim().split(/\s+/);
    if (fields.length !== 8 || !/^[0-9]{1,10}(?:\.[0-9]{1,9})?$/.test(fields[0])) {
      throw new Error("tum_lidar_trajectory_required");
    }
    const [seconds, fraction = ""] = fields[0].split(".");
    const stamp = BigInt(seconds) * 1_000_000_000n + BigInt(fraction.padEnd(9, "0"));
    const values = fields.slice(1).map((value) => {
      if (!/^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/.test(value)) return NaN;
      return Number(value);
    });
    if (stamp <= previous || !values.every(Number.isFinite)) throw new Error("ordered_finite_mapping_trajectory_required");
    previous = stamp;
    const [tx, ty, tz, qx, qy, qz, qw] = values;
    const norm = Math.hypot(qx, qy, qz, qw);
    if (Math.abs(norm - 1) > 2e-6) throw new Error("saved_mapping_unit_quaternion_required");
    // Normalize only the pinned writer's decimal roundoff, never an arbitrary quaternion.
    const [x, y, z, w] = [qx, qy, qz, qw].map((value) => value / norm);
    last = rigidMatrixSchema.parse([
      [1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w), tx],
      [2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w), ty],
      [2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y), tz],
      [0, 0, 0, 1],
    ]);
    first ??= last;
  }
  return { first: { frameIndex: 1, mapFromSensor: first! }, last: { frameIndex: frames, mapFromSensor: last! } };
}
