import type { LocalizationState, LocalizationRuntime, RigidMatrix } from "../../../shared/localization";

export const identityMatrix: RigidMatrix = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];

/** Synthetic service metadata only; no native process or sensor is running. */
export function localizationRuntimeFixture(): LocalizationRuntime {
  return { backend: "glim", inputMode: "recorded", serviceId: "1".repeat(32), generation: 0,
    runId: null, phase: "idle", selectedMap: null, cleanupComplete: true, closed: false,
    localizationReady: false, navigationReady: false };
}

/** Protocol fixture only; it does not establish a native map/profile association. */
export function localizationFixture(): LocalizationState {
  return {
    binding: {
      session: "a".repeat(32), input_mode: "recorded", map_id: `slam-${"b".repeat(32)}`,
      receipt_sha256: "c".repeat(64), points_sha256: "d".repeat(64), profile_sha256: "e".repeat(64),
    },
    generation: 0, state: "unseeded", reason: "explicit_seed_required",
    requestPending: false, workerReady: true, workerClosed: false, cleanupComplete: false,
    consecutiveMatches: 0, lastFrame: 12, referenceFrame: 12, mapFromSensor: null,
    coordinateUnits: "native-raw", sourceFrame: "lidar_chest_front", localizationReady: false, navigationReady: false,
  };
}
