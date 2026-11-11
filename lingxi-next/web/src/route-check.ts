import { localizationStateSchema, sameLocalizationBinding, type LocalizationState, type RigidMatrix } from "../../shared/localization";

export type RouteSample = {
  at: string; binding: LocalizationState["binding"]; generation: number; frame: number;
  lidar: RigidMatrix; base?: NonNullable<LocalizationState["basePose"]>;
};
export type RouteCheck = {
  runtimeKey: string; status: "recording" | "complete" | "interrupted";
  mapName?: string; lastObservedFrame?: number;
  origin: RouteSample; outward?: RouteSample; returned?: RouteSample; interruption?: string;
};

/** Checkpoints are observations only. They never seed, calibrate, or admit motion. */
export function routeSample(state: LocalizationState | undefined, receivedAt: number, now: number,
  date = new Date()): RouteSample | undefined {
  if (!Number.isFinite(receivedAt) || !Number.isFinite(now) || now < receivedAt || now - receivedAt > 900) return;
  const parsed = localizationStateSchema.safeParse(state);
  if (!parsed.success) return;
  const value = parsed.data;
  if (value.binding.input_mode !== "live" || value.state !== "tracking" || !value.mapFromSensor ||
      value.lastFrame < 1 || value.cleanupComplete) return;
  return { at: date.toISOString(), binding: value.binding, generation: value.generation,
    frame: value.lastFrame, lidar: value.mapFromSensor,
    ...(value.basePose?.status === "available" ? { base: value.basePose } : {}) };
}

export function observeRoute(check: RouteCheck, sample: RouteSample | undefined, runtimeKey: string): RouteCheck {
  if (check.status !== "recording") return check;
  const previous = check.outward ?? check.origin;
  const reason = !sample ? "定位中断或数据过期" : runtimeKey !== check.runtimeKey ||
    !sameLocalizationBinding(sample.binding, check.origin.binding) || sample.generation !== check.origin.generation
    ? "地图或定位会话已变化" : sample.frame < (check.lastObservedFrame ?? previous.frame) ? "定位帧回退" : undefined;
  return reason ? { ...check, status: "interrupted", interruption: reason }
    : sample && sample.frame !== check.lastObservedFrame ? { ...check, lastObservedFrame: sample.frame } : check;
}

export function advanceRoute(check: RouteCheck, sample: RouteSample | undefined, runtimeKey: string): RouteCheck {
  const observed = observeRoute(check, sample, runtimeKey);
  if (observed.status !== "recording" || !sample) throw new Error("本轮记录已中断，请重新记录 A 点");
  if (sample.frame <= (check.outward ?? check.origin).frame) throw new Error("请等待新的定位帧后记录");
  return check.outward ? { ...observed, status: "complete", returned: sample } : { ...observed, outward: sample };
}

const distance = (a: RigidMatrix, b: RigidMatrix, dimensions = 3) =>
  Math.hypot(...a.slice(0, dimensions).map((row, i) => row[3] - b[i][3]));
const rotation = (a: RigidMatrix, b: RigidMatrix) => Math.acos(Math.max(-1, Math.min(1,
  (a.slice(0, 3).reduce((sum, row, i) => sum + row.slice(0, 3).reduce((v, x, j) => v + x * b[i][j], 0), 0) - 1) / 2))) * 180 / Math.PI;
const yawDifference = (a: RigidMatrix, b: RigidMatrix) => {
  const difference = Math.atan2(a[1][0], a[0][0]) - Math.atan2(b[1][0], b[0][0]);
  return Math.abs(Math.atan2(Math.sin(difference), Math.cos(difference))) * 180 / Math.PI;
};

export function routeMetrics(check: RouteCheck, measuredDistance?: number) {
  const { origin: a, outward: b, returned: c } = check;
  const measured = Number.isFinite(measuredDistance) && measuredDistance! >= .1 && measuredDistance! <= 50
    ? measuredDistance : undefined;
  const outwardRaw = b ? distance(a.lidar, b.lidar) : undefined;
  const baseSamples = [a, b, c].filter((v): v is RouteSample => Boolean(v));
  const basesAgree = baseSamples.every((v) => v.base?.mapFromBase &&
    v.base.metresPerMapUnit === a.base?.metresPerMapUnit &&
    v.base.coordinateFrame === a.base?.coordinateFrame && v.base.bodyFrame === a.base?.bodyFrame &&
    JSON.stringify(v.base.navigationFromRaw) === JSON.stringify(a.base?.navigationFromRaw));
  return {
    outwardRaw, closureRaw: c ? distance(a.lidar, c.lidar) : undefined,
    rotationDegrees: c ? rotation(a.lidar, c.lidar) : undefined,
    declaredMetresPerMapUnit: basesAgree ? a.base?.metresPerMapUnit : undefined,
    outwardBaseMetres: basesAgree && b ? distance(a.base!.mapFromBase!, b.base!.mapFromBase!, 2) : undefined,
    closureBaseMetres: basesAgree && c ? distance(a.base!.mapFromBase!, c.base!.mapFromBase!, 2) : undefined,
    headingBaseDegrees: basesAgree && c ? yawDifference(a.base!.mapFromBase!, c.base!.mapFromBase!) : undefined,
    measuredStraightDistanceMetres: measured,
    measuredMetresPerRawUnit: measured !== undefined && outwardRaw !== undefined && outwardRaw >= .1
      ? measured / outwardRaw : undefined,
    physicalScaleValidated: false, navigationReady: false,
  };
}
