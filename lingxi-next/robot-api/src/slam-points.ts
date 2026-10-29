import type { LidarEvent, LidarFrame } from "../../shared/contracts";
import type { Readable } from "node:stream";

export type PointInputMode = "live" | "recorded" | "synthetic";
export type SlamPointRecord = {
  session: string;
  inputMode: PointInputMode;
  nativeFrameId: number;
  algorithmStamp: number;
  emittedAtSeconds: number;
  nativePointCount: number;
  points: number[];
  worldLidarPose: number[]; // Row-major LiDAR pose, never a robot-base pose.
};

const modes: Record<PointInputMode, number> = { live: 1, recorded: 2, synthetic: 3 };
const maximumBytes = 192 + 8_000 * 24;
const magic = Buffer.from("LXSPOINT", "ascii");

function context(session: string, mode: PointInputMode) {
  if (typeof session !== "string" || !/^[a-f0-9]{32}$/.test(session) ||
      typeof mode !== "string" || !Object.hasOwn(modes, mode)) {
    throw new Error("explicit_slam_point_session_and_mode_required");
  }
}

function rigid(pose: number[]) {
  if (pose.some((value) => !Number.isFinite(value)) ||
      pose.slice(12).some((value, index) => Math.abs(value - (index === 3 ? 1 : 0)) > 1e-7)) return false;
  let orthogonality = 0;
  for (let row = 0; row < 3; row += 1) {
    for (let column = 0; column < 3; column += 1) {
      let dot = 0;
      for (let axis = 0; axis < 3; axis += 1) dot += pose[axis * 4 + row] * pose[axis * 4 + column];
      orthogonality += (dot - (row === column ? 1 : 0)) ** 2;
    }
  }
  const determinant = pose[0] * (pose[5] * pose[10] - pose[6] * pose[9]) -
    pose[1] * (pose[4] * pose[10] - pose[6] * pose[8]) +
    pose[2] * (pose[4] * pose[9] - pose[5] * pose[8]);
  return Math.sqrt(orthogonality) <= 1e-5 && Math.abs(determinant - 1) <= 1e-5;
}

export function decodePointRecord(
  payload: Buffer, session: string, inputMode: PointInputMode, nowSeconds: number,
): SlamPointRecord {
  context(session, inputMode);
  if (!Buffer.isBuffer(payload) || payload.length < 192 || payload.length > maximumBytes) {
    throw new Error("one_bounded_complete_slam_point_record_required");
  }
  const count = payload.readUInt32LE(12), nativePointCount = payload.readUInt32LE(16);
  const identity = payload.subarray(48, 64).toString("hex");
  const id = payload.readBigUInt64LE(24);
  if (!payload.subarray(0, 8).equals(magic) || payload.readUInt16LE(8) !== 1 ||
      payload[10] !== modes[inputMode] || payload[11] !== 0 || payload.readUInt32LE(20) !== 0 ||
      identity !== session || count < 1 || count > 8_000 || payload.length !== 192 + count * 24 ||
      nativePointCount < count || nativePointCount > 2_000_000 || id < 1n || id > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error("matching_slam_point_version_session_mode_and_counts_required");
  }
  const algorithmStamp = payload.readDoubleLE(32), emittedAtSeconds = payload.readDoubleLE(40);
  const age = nowSeconds - emittedAtSeconds;
  if (!Number.isFinite(nowSeconds) || nowSeconds < 0 || !Number.isFinite(emittedAtSeconds) ||
      emittedAtSeconds < 0 || !Number.isFinite(algorithmStamp * 1000) || algorithmStamp <= 0 ||
      age < -0.05 || age > 2) throw new Error("fresh_local_monotonic_slam_points_required");
  const worldLidarPose = Array.from({ length: 16 }, (_, index) => payload.readDoubleLE(64 + index * 8));
  const points = Array.from({ length: count * 3 }, (_, index) => payload.readDoubleLE(192 + index * 8));
  if (!rigid(worldLidarPose) || points.some((value) => !Number.isFinite(value))) {
    throw new Error("finite_slam_points_and_rigid_lidar_pose_required");
  }
  return { session, inputMode, nativeFrameId: Number(id), algorithmStamp, emittedAtSeconds,
    nativePointCount, worldLidarPose, points };
}

export class LatestSlamPointSource {
  private latest?: SlamPointRecord;
  private closed = false;
  private failed = false;

  constructor(
    private readonly session: string,
    private readonly inputMode: PointInputMode,
    private readonly clock = () => Number(process.hrtime.bigint()) / 1e9,
    private readonly wallClock = Date.now,
  ) {
    context(session, inputMode);
  }

  offer(payload: Buffer): boolean {
    if (this.closed || this.failed) throw new Error("active_slam_point_source_required");
    try {
      const record = decodePointRecord(payload, this.session, this.inputMode, this.clock());
      if (this.latest && (record.nativeFrameId <= this.latest.nativeFrameId ||
          record.algorithmStamp <= this.latest.algorithmStamp ||
          record.emittedAtSeconds < this.latest.emittedAtSeconds)) return false;
      this.latest = record; // One visual snapshot only; no replay backlog.
      return true;
    } catch (error) {
      this.failed = true;
      this.latest = undefined;
      throw error;
    }
  }

  private frame(): LidarFrame {
    const record = this.latest, now = this.clock();
    if (this.closed || this.failed || !record ||
        !Number.isFinite(now) || now - record.emittedAtSeconds < -0.05 || now - record.emittedAtSeconds > 2) {
      throw new Error("fresh_slam_point_snapshot_required");
    }
    return {
      sequence: record.nativeFrameId, epoch: this.session, source: "point-cloud",
      frameId: "lingxi_glim_odom", sensorFrame: "lidar_chest_front", fixedFrame: true,
      stampMs: record.algorithmStamp * 1000,
      receivedAtMs: this.wallClock() - Math.max(0, now - record.emittedAtSeconds) * 1000,
      points: [...record.points],
      detail: "Corrected LiDAR odometry points; robot-base pose is not available.",
    };
  }

  async getLidarFrame(): Promise<LidarFrame> {
    if (this.inputMode !== "live") throw new Error("fresh_live_slam_point_snapshot_required");
    return this.frame();
  }

  getPointEvent(): LidarEvent {
    const frame = this.frame();
    return {
      type: "slam-preview", inputMode: this.inputMode, coordinateUnits: "native-raw", frame,
    };
  }

  close() {
    this.closed = true;
    this.latest = undefined;
  }

  invalidate() {
    this.failed = true;
    this.latest = undefined;
  }
}

/** An owned, dedicated local byte pipe; never health stdout or a network listener. */
export class SlamPointPipeSource extends LatestSlamPointSource {
  private buffer = Buffer.alloc(64);
  private offset = 0;
  private stopped = false;

  constructor(
    private readonly pipe: Readable,
    session: string,
    inputMode: PointInputMode,
    clock?: () => number,
    wallClock?: () => number,
  ) {
    super(session, inputMode, clock, wallClock);
    pipe.on("data", this.receive);
    pipe.once("end", this.disconnect);
    pipe.once("error", this.disconnect);
    pipe.once("close", this.disconnect);
    if (pipe.destroyed || pipe.readableEnded) this.disconnect();
  }

  private receive = (chunk: Buffer) => {
    if (this.stopped) return;
    try {
      if (!Buffer.isBuffer(chunk)) throw new Error("binary_slam_point_pipe_required");
      let cursor = 0;
      while (cursor < chunk.length) {
        const length = Math.min(this.buffer.length - this.offset, chunk.length - cursor);
        chunk.copy(this.buffer, this.offset, cursor, cursor + length);
        this.offset += length;
        cursor += length;
        if (this.offset !== this.buffer.length) continue;
        if (this.buffer.length === 64) {
          const count = this.buffer.readUInt32LE(12);
          if (!this.buffer.subarray(0, 8).equals(magic) || count < 1 || count > 8_000) {
            throw new Error("bounded_slam_point_pipe_header_required");
          }
          const record = Buffer.alloc(192 + count * 24);
          this.buffer.copy(record);
          this.buffer = record;
        } else {
          this.offer(this.buffer);
          this.buffer = Buffer.alloc(64);
          this.offset = 0;
        }
      }
    } catch {
      this.disconnect();
    }
  };

  private disconnect = () => {
    if (this.stopped) return;
    this.stopped = true;
    this.invalidate();
    this.buffer = Buffer.alloc(0);
    this.offset = 0;
    this.pipe.off("data", this.receive);
    this.pipe.off("end", this.disconnect);
    this.pipe.off("close", this.disconnect);
    // Retain the one-shot error listener until close if destroy emits an error.
    this.pipe.destroy();
  };

  override close() {
    this.disconnect();
    super.close();
  }
}
