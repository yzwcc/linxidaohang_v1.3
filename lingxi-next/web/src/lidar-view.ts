import type { LidarFrame } from "../../shared/contracts";

export type ScanHistory = { key: string; frames: LidarFrame[] };
export type PlanarLidarPose = { x: number; y: number; yaw: number };
export type CanvasPoint = { x: number; y: number };
export type LidarMeasurement = { start: CanvasPoint; end: CanvasPoint; distance: number };
export type LidarZSlice = { min: number; max: number };
export type LidarViewport = {
  width: number; height: number; scale: number; centerX: number; centerY: number; pan: CanvasPoint;
};

export function lidarViewport(bounds: ReturnType<typeof lidarBounds>, width: number, height: number,
  zoom: number, pan: CanvasPoint): LidarViewport {
  if (![...Object.values(bounds), width, height, zoom, pan.x, pan.y].every(Number.isFinite) ||
      width <= 48 || height <= 48 || zoom <= 0 || bounds.maxX <= bounds.minX || bounds.maxY <= bounds.minY) {
    throw new Error("invalid_lidar_viewport");
  }
  return { width, height, pan: { ...pan }, centerX: (bounds.minX + bounds.maxX) / 2,
    centerY: (bounds.minY + bounds.maxY) / 2,
    scale: zoom * Math.min((width - 48) / (bounds.maxX - bounds.minX), (height - 48) / (bounds.maxY - bounds.minY)) };
}

export function lidarToScreen(view: LidarViewport, point: CanvasPoint): CanvasPoint {
  return { x: view.width / 2 + view.pan.x + (point.x - view.centerX) * view.scale,
    y: view.height / 2 + view.pan.y - (point.y - view.centerY) * view.scale };
}

export function lidarFromScreen(view: LidarViewport, point: CanvasPoint): CanvasPoint {
  return { x: (point.x - view.width / 2 - view.pan.x) / view.scale + view.centerX,
    y: -(point.y - view.height / 2 - view.pan.y) / view.scale + view.centerY };
}

export function lidarSeedGesture(view: LidarViewport, start: CanvasPoint, end: CanvasPoint): PlanarLidarPose | undefined {
  const measurement = lidarMeasureGesture(view, start, end);
  return measurement ? { ...measurement.start, yaw: Math.atan2(start.y - end.y, end.x - start.x) } : undefined;
}

export function lidarMeasureGesture(view: LidarViewport, start: CanvasPoint, end: CanvasPoint): LidarMeasurement | undefined {
  if ([start, end].some((point) => !Number.isFinite(point.x) || !Number.isFinite(point.y) ||
      point.x < 0 || point.x > view.width || point.y < 0 || point.y > view.height) ||
      Math.hypot(end.x - start.x, end.y - start.y) < 12) return undefined;
  const first = lidarFromScreen(view, start), last = lidarFromScreen(view, end);
  const distance = Math.hypot(last.x - first.x, last.y - first.y);
  return Number.isFinite(distance) && distance > 0 ? { start: first, end: last, distance } : undefined;
}

export function appendScan(history: ScanHistory, frame: LidarFrame, accumulate: boolean): ScanHistory {
  const key = `${frame.epoch}:${frame.frameId}:${frame.fixedFrame}`;
  if (!accumulate || !frame.fixedFrame || frame.frameId === "lingxi_glim_map") return { key, frames: [frame] };
  if (history.key !== key) return { key, frames: [frame] };
  const previous = history.frames.at(-1);
  // A delayed/out-of-order packet cannot restore an older robot pose or trace.
  if (previous && (frame.sequence <= previous.sequence || frame.stampMs < previous.stampMs)) return history;
  const frames = history.frames.filter((item) => frame.stampMs - item.stampMs <= 15_000);
  frames.push(frame);
  let points = 0;
  let first = frames.length - 1;
  for (; first >= 0; first -= 1) {
    points += frames[first].points.length / 3;
    if (points > 50_000) { first += 1; break; }
  }
  return { key, frames: frames.slice(Math.max(0, first)) };
}

export function parseLidarZSlice(min: string, max: string): LidarZSlice | undefined {
  if (!min.trim() || !max.trim()) return undefined;
  const lower = Number(min), upper = Number(max);
  return Number.isFinite(lower) && Number.isFinite(upper) && lower < upper ? { min: lower, max: upper } : undefined;
}

export function sliceLidarFrames(frames: LidarFrame[], slice: LidarZSlice): LidarFrame[] {
  if (!Number.isFinite(slice.min) || !Number.isFinite(slice.max) || slice.min >= slice.max) {
    throw new Error("valid_raw_z_slice_required");
  }
  return frames.map((frame) => {
    const points: number[] = [];
    for (let index = 0; index < frame.points.length; index += 3) {
      if (frame.points[index + 2] >= slice.min && frame.points[index + 2] <= slice.max) {
        points.push(frame.points[index], frame.points[index + 1], frame.points[index + 2]);
      }
    }
    return { ...frame, points };
  });
}

export function lidarBounds(frames: LidarFrame[], mode: "all" | "main" = "all") {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  const xs: number[] = [], ys: number[] = [];
  for (const frame of frames) {
    for (let index = 0; index < frame.points.length; index += 3) {
      minX = Math.min(minX, frame.points[index]); maxX = Math.max(maxX, frame.points[index]);
      minY = Math.min(minY, frame.points[index + 1]); maxY = Math.max(maxY, frame.points[index + 1]);
      if (mode === "main") { xs.push(frame.points[index]); ys.push(frame.points[index + 1]); }
    }
  }
  if (!Number.isFinite(minX)) return { minX: -5, maxX: 5, minY: -5, maxY: 5 };
  if (mode === "main" && xs.length >= 100) {
    const quantile = (values: number[], p: number) => {
      const index = (values.length - 1) * p, lower = Math.floor(index), fraction = index - lower;
      return values[lower] * (1 - fraction) + values[Math.ceil(index)] * fraction;
    };
    xs.sort((a, b) => a - b); ys.sort((a, b) => a - b);
    minX = quantile(xs, .01); maxX = quantile(xs, .99);
    minY = quantile(ys, .01); maxY = quantile(ys, .99);
  }
  const padding = Math.max(0.4, Math.max(maxX - minX, maxY - minY) * 0.08);
  return { minX: minX - padding, maxX: maxX + padding, minY: minY - padding, maxY: maxY + padding };
}
