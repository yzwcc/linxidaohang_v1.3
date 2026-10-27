import type { LidarFrame } from "../../shared/contracts";

export type ScanHistory = { key: string; frames: LidarFrame[] };

export function appendScan(history: ScanHistory, frame: LidarFrame, accumulate: boolean): ScanHistory {
  const key = `${frame.epoch}:${frame.frameId}:${frame.fixedFrame}`;
  if (!accumulate || !frame.fixedFrame) return { key, frames: [frame] };
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

export function lidarBounds(frames: LidarFrame[]) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const frame of frames) {
    for (let index = 0; index < frame.points.length; index += 3) {
      minX = Math.min(minX, frame.points[index]); maxX = Math.max(maxX, frame.points[index]);
      minY = Math.min(minY, frame.points[index + 1]); maxY = Math.max(maxY, frame.points[index + 1]);
    }
  }
  if (!Number.isFinite(minX)) return { minX: -5, maxX: 5, minY: -5, maxY: 5 };
  const padding = Math.max(0.4, Math.max(maxX - minX, maxY - minY) * 0.08);
  return { minX: minX - padding, maxX: maxX + padding, minY: minY - padding, maxY: maxY + padding };
}
