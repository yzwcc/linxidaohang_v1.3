import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

export const sdkSha256 = "810c5e7a7d40191097dc5c4c37e3133407e63307fcb204d35caacb7768912c25";
const file = path.resolve("robot-agent/lib/linksoul-agentsdk-1.5.0-SNAPSHOT.jar");
let bytes = await readFile(file).catch(() => null);
if (!bytes) {
  const response = await fetch("https://open.agibot.com/docs/agentsdk_docs/java/v1.5.0/dist/linksoul-agentsdk-1.5.0-SNAPSHOT.jar", {
    signal: AbortSignal.timeout(30_000),
  });
  if (!response.ok) throw new Error(`SDK download failed: ${response.status}`);
  bytes = Buffer.from(await response.arrayBuffer());
}
if (createHash("sha256").update(bytes).digest("hex") !== sdkSha256) {
  throw new Error("AgentSDK artifact changed. Review the new SDK before updating the pinned SHA-256.");
}
await mkdir(path.dirname(file), { recursive: true });
await writeFile(file, bytes);
console.log(`Verified AgentSDK 1.5.0-SNAPSHOT: ${sdkSha256}`);
