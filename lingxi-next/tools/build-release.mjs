import { createHash } from "node:crypto";
import { cp, mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { downloadVerified } from "./download.mjs";
import { copySourceTree } from "./release-sources.mjs";

const independent = process.argv.includes("--independent-commissioning");
const commissioning = process.argv.includes("--commissioning");
if (independent && commissioning) throw new Error("Choose one release profile");
if (!independent && !commissioning) throw new Error("Target acceptance is pending; only commissioning packages are currently permitted.");
const pkg = JSON.parse(await readFile("package.json", "utf8"));
const version = `${pkg.version}-${independent ? "independent" : "core"}-commissioning-${new Date().toISOString().slice(0, 10).replaceAll("-", "")}`;
const stage = path.resolve("artifacts", `lingxi-next-${version}-linux-arm64`);
const archive = `${stage}.tar.gz`;
if (existsSync(stage) || existsSync(archive)) throw new Error("Release already exists; choose a new version before rebuilding.");
const run = (command, args, options = {}) => new Promise((resolve, reject) => {
  const child = spawn(command, args, { stdio: "inherit", ...options });
  child.on("error", reject);
  child.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`${command} failed: ${code}`)));
});
async function fetchArchive(url, name, sha256) {
  const filename = path.resolve(".local", name);
  return downloadVerified(url, filename, "sha256", sha256);
}
await run(process.execPath, ["node_modules/typescript/bin/tsc", "--noEmit"]);
await run(process.execPath, ["node_modules/typescript/bin/tsc", "-p", "tsconfig.api.json", "--noEmit"]);
await run(process.execPath, ["node_modules/typescript/bin/tsc", "-p", "tsconfig.runtime.json", "--noEmit"]);
await run(process.execPath, ["node_modules/vitest/vitest.mjs", "run"]);
await run("python", ["-m", "unittest", "discover", "-s", "robot-api/tests", "-p", "test_*.py", "-q"]);
await run(process.execPath, ["node_modules/vite/bin/vite.js", "build"]);
const { build } = await import("esbuild");
await build({ entryPoints: ["robot-api/src/index.ts"], bundle: true, platform: "node", target: "node22",
  format: "esm", packages: "external", outfile: "robot-api/dist/index.mjs" });
if (independent) {
  for (const [entry, outfile] of [["robot-runtime/src/mapping-main.ts", "robot-runtime/dist/mapping-main.mjs"],
    ["robot-runtime/src/localization-main.ts", "robot-runtime/dist/localization-main.mjs"],
    ["robot-runtime/src/slam-main.ts", "robot-runtime/dist/slam-main.mjs"]]) {
    await build({ entryPoints: [entry], bundle: true, platform: "node", target: "node22",
      format: "esm", packages: "external", outfile });
  }
}
if (!existsSync("robot-agent/build/lingxi-agent-0.1.0.jar")) throw new Error("Run npm run build:agent first");
await mkdir(".local", { recursive: true });
console.log("Fetching checksum-pinned Linux ARM64 runtimes");
const [nodeArchive, javaArchive] = await Promise.all([
  fetchArchive("https://nodejs.org/dist/v22.23.4/node-v22.23.4-linux-arm64.tar.gz", "node-arm64.tar.gz",
    "f7a1e486dbc8b286847815b477d4fb5e9fb963ed4489c3e1588853d685f589f9"),
  fetchArchive("https://cdn.azul.com/zulu/bin/zulu21.54.107-ca-jre21.0.13-linux_aarch64.tar.gz", "zulu21-java-arm64.tar.gz",
    "3f4649159c0780bfb4c66b73f17f784830e1cbc0b79698a2b235bd7fe18dea25"),
]);
for (const folder of ["web", "api", "bridge", "agent/lib", "tools"]) await mkdir(path.join(stage, folder), { recursive: true });
await Promise.all([
  cp("dist", path.join(stage, "web"), { recursive: true }),
  cp("robot-api/dist/index.mjs", path.join(stage, "api/index.mjs")),
  cp("robot-api/src/aimdk_ros_bridge.py", path.join(stage, "bridge/aimdk_ros_bridge.py")),
  cp("robot-agent/build/lingxi-agent-0.1.0.jar", path.join(stage, "agent/lingxi-agent.jar")),
  cp("robot-agent/build/lib", path.join(stage, "agent/lib"), { recursive: true }),
  cp("robot-agent/lib/linksoul-agentsdk-1.5.0-SNAPSHOT.jar", path.join(stage, "agent/lib/linksoul-agentsdk-1.5.0-SNAPSHOT.jar")),
  cp("robot-agent/src/main/resources/logback.xml", path.join(stage, "agent/logback.xml")),
  cp("tools/migrate-legacy.mjs", path.join(stage, "tools/migrate-legacy.mjs")),
  cp("deploy", path.join(stage, "deploy"), { recursive: true }),
  cp("docs", path.join(stage, "docs"), { recursive: true }),
  cp("README.md", path.join(stage, "README.md")),
]);
if (independent) {
  const { buildAssets, metadataFiles, slamMetadata } = await import("../robot-runtime/build-live.mjs");
  const assetWork = await mkdtemp(path.resolve(".local", `release-independent-${pkg.version}-`));
  const mappingAssets = path.join(assetWork, "mapping");
  const slamAssets = path.join(assetWork, "slam");
  await buildAssets(mappingAssets, "mapping");
  await buildAssets(slamAssets, "slam");
  await mkdir(path.join(stage, "independent/assets"), { recursive: true });
  await mkdir(path.join(stage, "independent/runtime"), { recursive: true });
  await mkdir(path.join(stage, "independent/source"), { recursive: true });
  for (const folder of ["robot-runtime", "robot-slam", "robot-navigation/native-ground",
    "robot-navigation/native-navfn", "robot-navigation/native-octomap", "shared"]) {
    await mkdir(path.join(stage, "independent/source", folder), { recursive: true });
  }
  await Promise.all([
    cp(mappingAssets, path.join(stage, "independent/assets/mapping"), { recursive: true }),
    cp(slamAssets, path.join(stage, "independent/assets/slam"), { recursive: true }),
    cp("robot-runtime/dist/mapping-main.mjs", path.join(stage, "independent/runtime/mapping-main.mjs")),
    cp("robot-runtime/dist/localization-main.mjs", path.join(stage, "independent/runtime/localization-main.mjs")),
    cp("robot-runtime/dist/slam-main.mjs", path.join(stage, "independent/runtime/slam-main.mjs")),
    ...["robot-runtime", "robot-slam", "robot-navigation", "tools", "shared"].map((folder) =>
      copySourceTree(folder, path.join(stage, "independent/source", folder))),
  ]);
  // Native loaders verify adapter sources and published evidence, not just Python imports.
  for (const file of new Set([...metadataFiles, ...slamMetadata])) {
    const destination = path.join(stage, "independent/source", file);
    await mkdir(path.dirname(destination), { recursive: true });
    await cp(file, destination);
  }
  const independentScope = JSON.parse(await readFile("deploy/independent-scope.json", "utf8"));
  const mappingManifest = JSON.parse(await readFile(path.join(mappingAssets, "build-manifest.json"), "utf8"));
  const slamManifest = JSON.parse(await readFile(path.join(slamAssets, "build-manifest.json"), "utf8"));
  await writeFile(path.join(stage, "independent/manifest.json"), JSON.stringify({
    schemaVersion: 1,
    profile: independentScope.profile,
    source: "clean-room independent GLIM/Nav2 composition",
    target: "linux-arm64-commissioning",
    productionAccepted: false,
    mappingAssets: { manifestSha256: createHash("sha256").update(JSON.stringify(mappingManifest)).digest("hex"), files: Object.keys(mappingManifest.files) },
    slamAssets: { manifestSha256: createHash("sha256").update(JSON.stringify(slamManifest)).digest("hex"), files: Object.keys(slamManifest.files) },
    runtimeEntrypoints: ["independent/runtime/mapping-main.mjs", "independent/runtime/localization-main.mjs", "independent/runtime/slam-main.mjs"],
    externalRequirements: independentScope.requiresExternal,
    acceptanceGates: ["arm64-native-execution", "sensor-topic-and-TF-calibration", "map-save-reload", "physical-navigation", "field-voice-and-Android"],
  }, null, 2) + "\n");
}
await writeFile(path.join(stage, "package.json"), JSON.stringify({ name: pkg.name, version: pkg.version, private: true,
  type: "module", dependencies: pkg.dependencies }, null, 2));
await cp("package-lock.json", path.join(stage, "package-lock.json"));
const npmCli = path.join(path.dirname(process.env.npm_execpath ?? ""), "npm-cli.js");
if (!existsSync(npmCli)) throw new Error("Run via npm run release:commissioning");
await run(process.execPath, [npmCli, "ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"], { cwd: stage });
await writeFile(path.join(stage, "VERSION"), `${version}\n`);
await writeFile(path.join(stage, "release-status.json"), JSON.stringify({
  version, target: "linux-arm64", classification: "commissioning", productionAccepted: false,
  scope: JSON.parse(await readFile("deploy/release-scope.json", "utf8")),
  ...(independent ? { independentStackIncluded: true,
    independent: JSON.parse(await readFile("deploy/independent-scope.json", "utf8")) } : {}),
  sdk: { version: "1.5.0-SNAPSHOT", sha256: "810c5e7a7d40191097dc5c4c37e3133407e63307fcb204d35caacb7768912c25" },
  pending: [independent ? "ARM64 native execution, sensor/TF calibration and physical navigation are not accepted" : "independent GLIM/localization/OctoMap/Nav2 runtime composition is NOT included in this core package",
    "physical mapping/navigation/estop", "ASR confidence field on target", "TTS item/event correlation on target",
    "map catalog schema and relocalization pixels/orientation on target",
    "physical preset-motion/expression/LinkCraft action acceptance", "cloud credentials/voice rejection",
    "acoustic and P95 acceptance", "installer/rollback on ARM64 target"],
}, null, 2));
if (independent) await run("python", ["tools/release_scope.py", "--stage", stage]);
await run("python", ["tools/archive-release.py", stage, archive, nodeArchive, javaArchive]);
await writeFile(`${archive}.sha256`, `${createHash("sha256").update(await readFile(archive)).digest("hex")}  ${path.basename(archive)}\n`);
await run("python", ["tools/verify-release.py", archive, "--report", `${archive}.verification.json`]);
console.log(independent
  ? `Independent-stack commissioning archive ready (not production accepted): ${archive}`
  : `Core-services commissioning archive ready (not the independent full product): ${archive}`);
