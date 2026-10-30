import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildAssets } from "./build-live.mjs";

export const buildSlam = (output) => buildAssets(output, "slam");

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.length !== 3) throw new Error("explicit_new_slam_build_output_required");
    const manifest = await buildSlam(process.argv[2]);
    console.log(JSON.stringify({ result: "slam_assets_built_not_target_executed",
      sourceFiles: Object.keys(manifest.sourceSha256).length, assetFiles: Object.keys(manifest.files).length }));
  } catch {
    console.error("Lingxi Next shared SLAM asset build failed.");
    process.exitCode = 1;
  }
}
