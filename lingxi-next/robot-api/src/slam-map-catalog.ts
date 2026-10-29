import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat, readFile, readdir, realpath } from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import type { SavedSlamMap, SavedSlamPointPreview } from "../../shared/contracts";

const positive = z.number().int().positive().safe();
const sha256 = z.string().regex(/^[a-f0-9]{64}$/);
const receiptSchema = z.object({
  schemaVersion: z.literal(1),
  session: z.string().regex(/^[a-f0-9]{32}$/),
  name: z.string().trim().min(1).max(64),
  inputMode: z.enum(["live", "recorded", "synthetic"]),
  counts: z.object({
    finalizedFrames: positive, savedFrames: positive, submaps: positive, points: positive,
  }),
  mapInventory: z.object({
    num_submaps: positive, num_all_frames: positive,
    num_matching_cost_factors: z.literal(0),
    fileCount: positive.max(4096), totalBytes: positive.max(64 * 1024 * 1024),
    artifactInventorySha256: sha256,
  }),
  savedPointsSha256: sha256,
  freshProcessReloadPassed: z.literal(true),
  navigationReady: z.literal(false),
  localizationReady: z.literal(false),
  geometricAccuracyValidated: z.literal(false),
});

const MAX_POINTS = 2_000_000;
const PREVIEW_POINTS = 20_000;
const MAX_MAPS = 32;
const required = [
  "graph.bin", "values.bin", "graph.txt", "odom_lidar.txt", "traj_lidar.txt",
  "odom_imu.txt", "traj_imu.txt",
];

async function direct(directory: string, file: boolean) {
  const info = await lstat(directory);
  if (info.isSymbolicLink() || (file ? !info.isFile() : !info.isDirectory())) {
    throw new Error("direct_slam_artifact_required");
  }
  return info;
}

async function digest(file: string) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest("hex");
}

/** Read-only: receipts are never imported into AIMDK maps or the navigation database. */
export class SavedSlamMapCatalog {
  private readonly root: string;
  private listing?: Promise<SavedSlamMap[]>;

  constructor(directory: string) {
    if (!path.isAbsolute(directory)) throw new Error("absolute_saved_slam_directory_required");
    this.root = path.resolve(directory);
  }

  private async directory() {
    await direct(this.root, false);
    if (await realpath(this.root) !== this.root) throw new Error("direct_saved_slam_directory_required");
  }

  private async load(name: string) {
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(name)) throw new Error("local_slam_directory_name_required");
    const directory = path.join(this.root, name);
    await direct(directory, false);
    const ready = path.join(directory, "map-ready.json");
    const info = await direct(ready, true);
    if (info.size > 16 * 1024) throw new Error("bounded_slam_receipt_required");
    const receipt = receiptSchema.parse(JSON.parse(await readFile(ready, "utf8")));
    const { counts, mapInventory: inventory } = receipt;
    if (counts.points > MAX_POINTS || counts.savedFrames !== counts.finalizedFrames ||
        counts.savedFrames !== inventory.num_all_frames || counts.submaps !== inventory.num_submaps) {
      throw new Error("consistent_saved_slam_counts_required");
    }
    const pointsFile = path.join(directory, "private-saved-points.f64");
    if ((await direct(pointsFile, true)).size !== counts.points * 24) {
      throw new Error("complete_saved_slam_points_required");
    }
    const mapRoot = path.join(directory, "map");
    await direct(mapRoot, false);
    const entries: [string, number, string][] = [];
    let total = 0;
    const walk = async (folder: string, depth: number) => {
      if (depth > 4) throw new Error("bounded_saved_slam_depth_required");
      for (const child of (await readdir(folder)).sort()) {
        if (!/^[A-Za-z0-9._-]+$/.test(child)) throw new Error("portable_slam_artifact_name_required");
        const file = path.join(folder, child), stat = await lstat(file);
        if (stat.isSymbolicLink()) throw new Error("indirect_slam_artifact_rejected");
        if (stat.isDirectory()) { await walk(file, depth + 1); continue; }
        if (!stat.isFile() || (stat.size === 0 && child !== "imu_rate.txt")) {
          throw new Error("complete_slam_artifact_required");
        }
        total += stat.size;
        if (entries.length >= 4096 || total > 64 * 1024 * 1024) throw new Error("bounded_saved_slam_map_required");
        entries.push([path.relative(mapRoot, file).split(path.sep).join("/"), stat.size, await digest(file)]);
      }
    };
    await walk(mapRoot, 0);
    entries.sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0);
    const files = new Set(entries.map(([file]) => file));
    if (required.some((file) => !files.has(file)) ||
        entries.length !== inventory.fileCount || total !== inventory.totalBytes ||
        createHash("sha256").update(JSON.stringify(entries)).digest("hex") !== inventory.artifactInventorySha256 ||
        await digest(pointsFile) !== receipt.savedPointsSha256) {
      throw new Error("unchanged_saved_slam_artifacts_required");
    }
    const map: SavedSlamMap = {
      id: `slam-${receipt.session}`, name: receipt.name, source: "glim",
      inputMode: receipt.inputMode, counts, freshProcessReloadPassed: true,
      localizationReady: false, navigationReady: false, geometricAccuracyValidated: false,
      coordinateUnits: "native-raw",
    };
    return { map, pointsFile, pointsSha256: receipt.savedPointsSha256 };
  }

  private async scan() {
    await this.directory();
    const entries = (await readdir(this.root, { withFileTypes: true }))
      .filter((entry) => entry.isDirectory() && /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(entry.name))
      .sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
    if (entries.length > MAX_MAPS) throw new Error("bounded_saved_slam_catalog_required");
    const maps = [];
    for (const entry of entries) {
      try { maps.push({ directory: entry.name, ...await this.load(entry.name) }); }
      catch { /* Incomplete, failed and changed maps do not enter the catalog. */ }
    }
    const counts = new Map<string, number>();
    for (const { map } of maps) counts.set(map.id, (counts.get(map.id) ?? 0) + 1);
    return maps.filter(({ map }) => counts.get(map.id) === 1);
  }

  list(): Promise<SavedSlamMap[]> {
    if (this.listing) return this.listing;
    const pending = this.scan().then((entries) => entries.map(({ map }) => map));
    this.listing = pending;
    void pending.finally(() => { if (this.listing === pending) this.listing = undefined; }).catch(() => undefined);
    return pending;
  }

  async preview(id: string): Promise<SavedSlamPointPreview> {
    if (!/^slam-[a-f0-9]{32}$/.test(id)) throw Object.assign(new Error("SLAM 地图不存在"), { statusCode: 404 });
    const found = (await this.scan()).find(({ map }) => map.id === id);
    if (!found) throw Object.assign(new Error("SLAM 地图不存在或校验未通过"), { statusCode: 404 });
    const bytes = await readFile(found.pointsFile);
    if (bytes.length !== found.map.counts.points * 24 ||
        createHash("sha256").update(bytes).digest("hex") !== found.pointsSha256) {
      throw new Error("unchanged_saved_slam_preview_required");
    }
    const stride = Math.ceil(found.map.counts.points / PREVIEW_POINTS);
    const points: number[] = [];
    for (let index = 0; index < found.map.counts.points; index += 1) {
      const xyz = [0, 8, 16].map((offset) => bytes.readDoubleLE(index * 24 + offset));
      if (!xyz.every(Number.isFinite)) throw new Error("finite_saved_slam_points_required");
      if (index % stride === 0) points.push(...xyz);
    }
    return {
      mapId: id, inputMode: found.map.inputMode, coordinateUnits: "native-raw",
      totalPoints: found.map.counts.points, sampled: stride !== 1, points,
    };
  }
}
