import { createHash } from "node:crypto";
import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { downloadVerified } from "./download.mjs";

if (!process.argv.includes("--commissioning")) throw new Error("Target acceptance is pending; only --commissioning packages are currently permitted.");
const pkg = JSON.parse(await readFile("package.json", "utf8"));
const version = `${pkg.version}-commissioning-20261028`;
const stage = path.resolve("artifacts", `lingxi-next-${version}-linux-arm64`);
if (existsSync(stage)) throw new Error("Release stage already exists; archive it before rebuilding with this version.");
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
await run(process.execPath, ["node_modules/vitest/vitest.mjs", "run"]);
await run("python", ["-m", "unittest", "discover", "-s", "robot-api/tests", "-p", "test_*.py", "-q"]);
await run(process.execPath, ["node_modules/vite/bin/vite.js", "build"]);
const { build } = await import("esbuild");
await build({ entryPoints: ["robot-api/src/index.ts"], bundle: true, platform: "node", target: "node22",
  format: "esm", packages: "external", outfile: "robot-api/dist/index.mjs" });
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
await writeFile(path.join(stage, "package.json"), JSON.stringify({ name: pkg.name, version: pkg.version, private: true,
  type: "module", dependencies: pkg.dependencies }, null, 2));
await cp("package-lock.json", path.join(stage, "package-lock.json"));
const npmCli = path.join(path.dirname(process.env.npm_execpath ?? ""), "npm-cli.js");
if (!existsSync(npmCli)) throw new Error("Run via npm run release:commissioning");
await run(process.execPath, [npmCli, "ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"], { cwd: stage });
await writeFile(path.join(stage, "VERSION"), `${version}\n`);
await writeFile(path.join(stage, "release-status.json"), JSON.stringify({
  version, target: "linux-arm64", classification: "commissioning", productionAccepted: false,
  sdk: { version: "1.5.0-SNAPSHOT", sha256: "810c5e7a7d40191097dc5c4c37e3133407e63307fcb204d35caacb7768912c25" },
  pending: ["physical mapping/navigation/estop", "ASR confidence field on target", "TTS item/event correlation on target",
    "map catalog schema and relocalization pixels/orientation on target",
    "preset motion/expression/LinkCraft manifest", "cloud credentials/voice rejection", "acoustic and P95 acceptance", "installer/rollback on ARM64 target"],
}, null, 2));
const archive = `${stage}.tar.gz`;
await run("python", ["tools/archive-release.py", stage, archive, nodeArchive, javaArchive]);
await writeFile(`${archive}.sha256`, `${createHash("sha256").update(await readFile(archive)).digest("hex")}  ${path.basename(archive)}\n`);
console.log(`Commissioning archive ready: ${archive}`);
