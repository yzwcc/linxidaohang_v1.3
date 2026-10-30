/** Isolated synthetic serialization fixture, not a native-loadable GLIM map. */
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

export async function savedSlamFixture(root: string, options: {
  directory?: string; session?: string; points?: number; inputMode?: "live" | "recorded" | "synthetic";
  sensorProvenance?: {
    schemaVersion: 1; profileSha256: string;
    timingHypothesis: "header_is_scan_start" | "header_is_scan_end";
    configurationSha256: Record<string, string>;
  };
} = {}) {
  const directory = path.join(root, options.directory ?? "synthetic-map");
  const mapRoot = path.join(directory, "map");
  await mkdir(path.join(mapRoot, "000000"), { recursive: true });
  const files: Record<string, string> = {
    "graph.bin": "isolated-synthetic-graph",
    "values.bin": "isolated-synthetic-values",
    "graph.txt": "num_submaps: 1\nnum_all_frames: 2\nnum_matching_cost_factors: 0\n",
    "odom_lidar.txt": "0 0 0 0 0 0 0 1\n1 1 0 0 0 0 0 1\n",
    "traj_lidar.txt": "0 0 0 0 0 0 0 1\n1 1 0 0 0 0 0 1\n",
    "odom_imu.txt": "0 0 0 0 0 0 0 1\n1 1 0 0 0 0 0 1\n",
    "traj_imu.txt": "0 0 0 0 0 0 0 1\n1 1 0 0 0 0 0 1\n",
    "000000/cloud.bin": "isolated-synthetic-cloud",
  };
  const hash = (bytes: string | Buffer) => createHash("sha256").update(bytes).digest("hex");
  const entries: [string, number, string][] = [];
  for (const file of Object.keys(files).sort()) {
    const bytes = Buffer.from(files[file]);
    await writeFile(path.join(mapRoot, file), bytes);
    entries.push([file, bytes.length, hash(bytes)]);
  }
  const count = options.points ?? 1440;
  const bytes = Buffer.alloc(count * 24);
  for (let index = 0; index < count; index += 1) {
    const t = index / count * Math.PI * 2;
    const xyz = [6 * Math.cos(t), 4 * Math.sin(t), Math.sin(t * 3) * .3];
    xyz.forEach((value, axis) => bytes.writeDoubleLE(value, index * 24 + axis * 8));
  }
  const receipt = {
    schemaVersion: 1, session: options.session ?? "bbaaccdd00112233445566778899aabb",
    name: "Synthetic saved map", inputMode: options.inputMode ?? "synthetic",
    counts: { finalizedFrames: 2, savedFrames: 2, submaps: 1, points: count },
    mapInventory: {
      num_submaps: 1, num_all_frames: 2, num_matching_cost_factors: 0,
      fileCount: entries.length, totalBytes: entries.reduce((sum, [, size]) => sum + size, 0),
      artifactInventorySha256: hash(JSON.stringify(entries)),
    },
    savedPointsSha256: hash(bytes), freshProcessReloadPassed: true,
    navigationReady: false, localizationReady: false, geometricAccuracyValidated: false,
    ...(options.sensorProvenance ? { schemaVersion: 2, sensorProvenance: options.sensorProvenance } : {}),
  };
  const ready = path.join(directory, "map-ready.json");
  const points = path.join(directory, "private-saved-points.f64");
  await writeFile(points, bytes);
  await writeFile(ready, JSON.stringify(receipt));
  return { directory, ready, points, receipt, id: `slam-${receipt.session}` };
}
