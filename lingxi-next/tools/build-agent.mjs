import "./fetch-agent-sdk.mjs";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";

const version = "3.9.11";
const root = path.resolve(".local");
const home = path.join(root, `apache-maven-${version}`);
if (!existsSync(home)) {
  const base = `https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/${version}/apache-maven-${version}-bin.zip`;
  const [binary, checksum] = await Promise.all([fetch(base), fetch(`${base}.sha512`)]);
  if (!binary.ok || !checksum.ok) throw new Error("Cannot download Maven distribution/checksum");
  const bytes = Buffer.from(await binary.arrayBuffer());
  const expected = (await checksum.text()).trim().split(/\s/)[0];
  if (createHash("sha512").update(bytes).digest("hex") !== expected) throw new Error("Maven checksum mismatch");
  await mkdir(root, { recursive: true });
  const zip = path.join(root, "maven.zip");
  await writeFile(zip, bytes);
  const extracted = spawnSync("tar", ["-xf", zip, "-C", root], { stdio: "inherit" });
  if (extracted.status !== 0) throw new Error("Cannot extract Maven");
}
const executable = path.join(home, "bin", process.platform === "win32" ? "mvn.cmd" : "mvn");
const args = ["-B", "-f", "robot-agent/pom.xml", "verify", ...process.argv.slice(2)];
// cmd.exe is needed only for a trusted, fixed local .cmd file; no user data is interpolated.
const result = spawnSync(executable, args, { stdio: "inherit", shell: process.platform === "win32" });
if (result.status !== 0) process.exit(result.status ?? 1);
const jar = await readFile("robot-agent/lib/linksoul-agentsdk-1.5.0-SNAPSHOT.jar");
await writeFile("robot-agent/build/SDK-SHA256.txt", `${createHash("sha256").update(jar).digest("hex")}  linksoul-agentsdk-1.5.0-SNAPSHOT.jar\n`);
