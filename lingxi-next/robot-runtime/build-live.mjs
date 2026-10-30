import { createHash } from "node:crypto";
import { lstat, mkdir, readFile, readdir, realpath, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const root = fileURLToPath(new URL("../", import.meta.url));
export const sourceDirectories = ["web", "robot-api/src", "robot-runtime/src", "robot-runtime/python",
  "robot-slam/lingxi_slam", "robot-slam/native-point-output", "tools/glim-replay", "tools/glim-mapping", "shared"];
export const metadataFiles = ["package.json", "package-lock.json", "vite.config.ts",
  "tsconfig.json", "tsconfig.api.json", "tsconfig.runtime.json", "robot-runtime/build-live.mjs",
  "docs/slam-humble-live-evidence.json", "docs/slam-supervised-arm64-evidence.json",
  "docs/slam-point-arm64-target-evidence.json", "docs/slam-point-consumer-preparation-evidence.json",
  "docs/slam-native-point-pipe-evidence.json", "docs/slam-stream-input-evidence.json"];

export const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");

export async function files(directory, prefix = "") {
  const result = [];
  for (const name of (await readdir(directory)).sort()) {
    if (name === "__pycache__") continue;
    const filename = path.join(directory, name);
    const relative = prefix ? `${prefix}/${name}` : name;
    const info = await lstat(filename);
    if (info.isSymbolicLink()) throw new Error("direct_live_build_sources_required");
    if (info.isDirectory()) result.push(...await files(filename, relative));
    else if (info.isFile()) result.push(relative);
    else throw new Error("ordinary_live_build_sources_required");
  }
  return result;
}

export async function sourceHashes(project = root, profile = "point") {
  if (!["point", "mapping", "slam"].includes(profile)) throw new Error("explicit_runtime_build_profile_required");
  const names = [...metadataFiles];
  if (profile === "mapping") names.push("robot-runtime/build-mapping.mjs");
  if (profile === "slam") names.push("robot-runtime/build-slam.mjs");
  for (const directory of sourceDirectories) {
    names.push(...(await files(path.join(project, directory), directory)));
  }
  for (const name of await readdir(path.join(project, "tools"))) {
    if (name.endsWith(".py")) names.push(`tools/${name}`);
  }
  const result = {};
  for (const name of names.sort()) {
    if (/(^|\/)(\.|private-)|\.(?:pem|key|npz|db3|sqlite3|log)$/i.test(name)) {
      throw new Error("source_only_live_build_inventory_required");
    }
    const filename = path.join(project, name);
    if (!(await lstat(filename)).isFile() || await realpath(filename) !== filename) {
      throw new Error("direct_live_build_source_required");
    }
    result[name] = digest(await readFile(filename));
  }
  return result;
}

export async function buildAssets(output, profile = "point") {
  output = path.resolve(output);
  const parent = path.dirname(output);
  if (await realpath(parent) !== parent || path.relative(root, output).split(path.sep)[0] !== ".local") {
    throw new Error("new_direct_local_live_build_directory_required");
  }
  const sources = await sourceHashes(root, profile);
  await mkdir(output, { mode: 0o700 });
  const esbuild = await import("esbuild");
  const vite = await import("vite");
  await esbuild.build({
    absWorkingDir: root,
    entryPoints: [profile === "slam" ? "robot-runtime/src/slam-main.ts" : profile === "mapping"
      ? "robot-runtime/src/mapping-main.ts" : "robot-runtime/src/main.ts"], bundle: true,
    platform: "node", target: "node22", format: "esm", packages: "external",
    outfile: path.join(output, "api/main.mjs"), logLevel: "warning",
  });
  await vite.build({
    configFile: path.join(root, "vite.config.ts"), root: path.join(root, "web"), envDir: false, envPrefix: [],
    mode: "production", logLevel: "warn",
    build: { outDir: path.join(output, "web"), emptyOutDir: false, sourcemap: false },
  });
  if (JSON.stringify(await sourceHashes(root, profile)) !== JSON.stringify(sources)) {
    throw new Error("live_build_sources_changed");
  }
  const inventory = {};
  for (const name of await files(output)) {
    const bytes = await readFile(path.join(output, name));
    inventory[name] = { sha256: digest(bytes), bytes: bytes.length };
  }
  if (!inventory["api/main.mjs"] || !inventory["web/index.html"]) throw new Error("complete_live_assets_required");
  const manifest = {
    schemaVersion: 1, purpose: profile === "slam" ? "shared_slam_assets_not_target_acceptance" : profile === "mapping"
      ? "isolated_managed_mapping_assets_not_target_acceptance" : "isolated_live_point_assets_not_target_acceptance",
    nodeBuildHostVersion: process.version, esbuildVersion: esbuild.version, viteVersion: vite.version,
    environmentFilesLoaded: false, customEnvironmentVariablesExposed: false,
    sourceSha256: sources, files: inventory,
  };
  await writeFile(path.join(output, "build-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`,
    { flag: "wx", mode: 0o600 });
  return manifest;
}

export const buildLive = (output) => buildAssets(output, "point");

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.length !== 3) throw new Error("explicit_new_live_build_output_required");
    const manifest = await buildLive(process.argv[2]);
    console.log(JSON.stringify({ result: "live_assets_built_not_target_executed",
      sourceFiles: Object.keys(manifest.sourceSha256).length, assetFiles: Object.keys(manifest.files).length }));
  } catch {
    console.error("Lingxi Next isolated live asset build failed.");
    process.exitCode = 1;
  }
}
