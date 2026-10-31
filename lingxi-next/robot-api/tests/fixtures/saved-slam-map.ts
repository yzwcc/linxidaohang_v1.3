/** Isolated synthetic serialization fixture, not a native-loadable GLIM map. */
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

export async function savedSlamFixture(root: string, options: {
  directory?: string; session?: string; points?: number; inputMode?: "live" | "recorded" | "synthetic";
  lidarTrajectory?: string;
  fullMapping?: boolean;
  sensorProvenance?: {
    schemaVersion: 1; profileSha256: string;
    timingHypothesis: "header_is_scan_start" | "header_is_scan_end";
    configurationSha256: Record<string, string>;
  };
} = {}) {
  const directory = path.join(root, options.directory ?? "synthetic-map");
  const mapRoot = path.join(directory, "map");
  await mkdir(path.join(mapRoot, "000000"), { recursive: true });
  const submaps = options.fullMapping ? 3 : 1;
  const frames = options.fullMapping ? 3 : 2;
  const trajectory = Array.from({ length: frames }, (_, i) => `${i} ${i} 0 0 0 0 0 1\n`).join("");
  const files: Record<string, string> = {
    "graph.bin": "isolated-synthetic-graph",
    "values.bin": "isolated-synthetic-values",
    "graph.txt": `num_submaps: ${submaps}\nnum_all_frames: ${frames}\nnum_matching_cost_factors: ${options.fullMapping ? 2 : 0}\n` +
      (options.fullMapping ? "matching_cost vgicp 0 1\nmatching_cost vgicp 0 2\n" : ""),
    "odom_lidar.txt": trajectory,
    "traj_lidar.txt": options.lidarTrajectory ?? trajectory,
    "odom_imu.txt": trajectory,
    "traj_imu.txt": trajectory,
    "000000/cloud.bin": "isolated-synthetic-cloud",
  };
  for (let i = 1; i < submaps; i += 1) {
    await mkdir(path.join(mapRoot, String(i).padStart(6, "0")));
    files[`${String(i).padStart(6, "0")}/cloud.bin`] = "isolated-synthetic-full-submap";
  }
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
    counts: { finalizedFrames: frames, savedFrames: frames, submaps, points: count },
    mapInventory: {
      num_submaps: submaps, num_all_frames: frames, num_matching_cost_factors: options.fullMapping ? 2 : 0,
      ...(options.fullMapping ? { nonAdjacentMatchingPairs: 1 } : {}),
      fileCount: entries.length, totalBytes: entries.reduce((sum, [, size]) => sum + size, 0),
      artifactInventorySha256: hash(JSON.stringify(entries)),
    },
    savedPointsSha256: hash(bytes), freshProcessReloadPassed: true,
    navigationReady: false, localizationReady: false, geometricAccuracyValidated: false,
    ...(options.sensorProvenance ? { schemaVersion: 2, sensorProvenance: options.sensorProvenance } : {}),
    ...(options.fullMapping ? { schemaVersion: 3, mappingEngine: "glim-full-async-v1" as const } : {}),
  };
  const ready = path.join(directory, "map-ready.json");
  const points = path.join(directory, "private-saved-points.f64");
  await writeFile(points, bytes);
  await writeFile(ready, JSON.stringify(receipt));
  return { directory, ready, points, receipt, id: `slam-${receipt.session}` };
}
