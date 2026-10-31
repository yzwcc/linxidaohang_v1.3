import { lstat, realpath } from "node:fs/promises";
import { createConnection } from "node:net";
import path from "node:path";
import type { Readable } from "node:stream";
import type { LidarEvent } from "../../shared/contracts";
import type { MappingBackend } from "./mapping-backend";
import { SlamPointPipeSource, type PointInputMode } from "./slam-points";
import { readCorrectedMapPreview, type CorrectedMapPreview } from "./corrected-map-preview";

type Connection = { session: string; mode: PointInputMode; name: string; pipe?: Readable; source?: SlamPointPipeSource;
  corrected?: CorrectedMapPreview };
type Connector = (endpoint: string) => Promise<Readable>;

async function privateConnection(endpoint: string): Promise<Readable> {
  const root = path.dirname(endpoint);
  if (process.platform !== "linux" || !path.isAbsolute(root) || Buffer.byteLength(endpoint) >= 100 ||
      await realpath(root) !== root) throw new Error("direct_private_mapping_point_directory_required");
  const [directory, socket] = await Promise.all([lstat(root), lstat(endpoint)]);
  if (!directory.isDirectory() || directory.mode & 0o077 || directory.uid !== process.geteuid!() ||
      !socket.isSocket() || socket.mode & 0o077 || socket.uid !== directory.uid) {
    throw new Error("same_user_private_mapping_point_socket_required");
  }
  return new Promise((resolve, reject) => {
    const pipe = createConnection({ path: endpoint });
    const timer = setTimeout(() => pipe.destroy(new Error("mapping_point_connect_deadline")), 500);
    const failed = (error: Error) => { clearTimeout(timer); reject(error); };
    pipe.once("error", failed);
    pipe.once("connect", () => {
      clearTimeout(timer);
      pipe.off("error", failed);
      resolve(pipe);
    });
  });
}

/** Follows the local control owner even without browser clients. Never guesses a session. */
export class MappingPointSource {
  private current?: Connection;
  private visible = false;
  private closed = false;
  private timer?: ReturnType<typeof setTimeout>;
  private polling?: Promise<void>;
  private blockedSession?: string;

  constructor(
    private readonly backend: MappingBackend,
    private readonly directory: string,
    private readonly connect: Connector = privateConnection,
    private readonly clock?: () => number,
    private readonly readMap = readCorrectedMapPreview,
  ) {
    if (!path.isAbsolute(directory)) throw new Error("absolute_mapping_point_directory_required");
  }

  start() {
    if (this.closed || this.polling || this.timer) return;
    this.poll();
  }

  private poll() {
    this.polling = this.refresh().finally(() => {
      this.polling = undefined;
      if (!this.closed) {
        this.timer = setTimeout(() => { this.timer = undefined; this.poll(); }, 100);
        this.timer.unref();
      }
    });
  }

  private retire() {
    this.visible = false;
    this.current?.source?.close();
    this.current?.pipe?.destroy();
    this.current = undefined;
  }

  private async refresh() {
    try {
      const state = await this.backend.state();
      if (this.closed) return;
      const current = this.current;
      if (state.session === this.blockedSession) { this.retire(); return; }
      // Hide immediately while the existing pipe drains. Closing it during native
      // finalization would report a broken consumer and incorrectly revoke a save.
      if (current && state.session === current.session && (state.phase === "saving" || state.phase === "stopping")) {
        this.visible = false;
        return;
      }
      if (!state.session || !state.mapping || !["requested", "running"].includes(state.phase) ||
          !state.pointStream || !/^[a-f0-9]{32}$/.test(state.session) ||
          !/^[a-f0-9]{32}\.points\.sock$/.test(state.pointStream.socket)) {
        this.retire();
        return;
      }
      if (!current || current.session !== state.session || current.mode !== state.inputMode ||
          current.name !== state.pointStream.socket) {
        this.retire();
        this.current = { session: state.session, mode: state.inputMode, name: state.pointStream.socket };
      }
      const owned = this.current!;
      if (owned.source) {
        const corrected = await this.readMap(this.directory, owned.session);
        if (this.closed || this.current !== owned) return;
        if (corrected) {
          if (corrected.inputMode !== owned.mode || corrected.session !== owned.session ||
              owned.corrected && corrected.revision < owned.corrected.revision) {
            throw new Error("same_mapping_revision_and_provenance_required");
          }
          owned.corrected = corrected;
        }
        this.visible = true;
        return;
      } // EOF stays closed for this session.
      this.visible = false;
      const pipe = await this.connect(path.join(this.directory, owned.name));
      if (this.closed || this.current !== owned) { pipe.destroy(); return; }
      owned.pipe = pipe;
      owned.source = new SlamPointPipeSource(pipe, owned.session, owned.mode, this.clock);
      const confirmed = await this.backend.state();
      if (this.closed || this.current !== owned) { pipe.destroy(); return; }
      if (confirmed.session === owned.session && ["saving", "stopping"].includes(confirmed.phase)) return;
      if (confirmed.session !== owned.session || confirmed.inputMode !== owned.mode || !confirmed.mapping ||
          !["requested", "running"].includes(confirmed.phase) || confirmed.pointStream?.socket !== owned.name) {
        this.retire();
        return;
      }
      this.visible = true;
    } catch {
      // An absent endpoint can precede native initialization. Retry only before
      // accepting this session's one consumer, never replay a disconnected pipe.
      this.visible = false;
      if (this.current?.source) {
        this.blockedSession = this.current.session;
        this.retire();
      }
    }
  }

  getPointEvent(): LidarEvent {
    if (this.closed || !this.visible || !this.current?.source) throw new Error("current_mapping_points_unavailable");
    const scan = this.current.source.getPointEvent(); // Fresh sensor input is required even for unchanged map revisions.
    const corrected = this.current.corrected;
    if (!corrected || scan.type !== "slam-preview") return scan;
    return { type: "slam-map", inputMode: this.current.mode, coordinateUnits: "native-raw",
      revision: corrected.revision, submaps: corrected.submaps, totalPoints: corrected.totalPoints,
      updatedAtMs: corrected.updatedAtMs,
      frame: { ...scan.frame, sequence: corrected.revision, frameId: "lingxi_glim_map",
        stampMs: corrected.updatedAtMs, points: corrected.points,
        detail: "Revisioned globally corrected submaps; display sample only, not a robot-base pose." } };
  }

  async close() {
    this.closed = true;
    clearTimeout(this.timer);
    this.timer = undefined;
    this.retire();
    await this.polling;
  }
}
