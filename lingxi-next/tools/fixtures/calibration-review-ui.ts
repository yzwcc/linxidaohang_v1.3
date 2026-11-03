/** Local synthetic saved-map page for layout checks; never connects to a robot. */
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pairingCodeHash } from "../../robot-api/src/auth";
import { buildServer } from "../../robot-api/src/server";
import { savedSlamFixture } from "../../robot-api/tests/fixtures/saved-slam-map";

async function main() {
  const root = await mkdtemp(path.join(tmpdir(), "lingxi-calibration-ui-"));
  const maps = path.join(root, "maps");
  await mkdir(maps, { mode: 0o700 });
  await savedSlamFixture(maps, { inputMode: "synthetic", sensorProvenance: {
    schemaVersion: 1, profileSha256: "a".repeat(64), timingHypothesis: "header_is_scan_start",
    configurationSha256: { "synthetic-lidar.json": "b".repeat(64) },
  } });
  const app = await buildServer({ demoMode: true, savedSlamDirectory: maps,
    stateDirectory: path.join(root, "state"), staticDirectory: path.resolve("dist"),
    pairingCodeHash: pairingCodeHash("synthetic-calibration-ui") });
  const url = await app.listen({ host: "127.0.0.1", port: 0 });
  console.log(JSON.stringify({ classification: "isolated_synthetic_calibration_ui", url,
    pairingCode: "synthetic-calibration-ui", syntheticMapOnly: true,
    robotContacted: false, motionCommandsSent: false }));
  let closing = false;
  const close = async () => {
    if (closing) return; closing = true; clearTimeout(expiry);
    await app.close();
    const resolved = path.resolve(root);
    if (path.dirname(resolved) === path.resolve(tmpdir()) && path.basename(resolved).startsWith("lingxi-calibration-ui-")) {
      await rm(resolved, { recursive: true, force: true });
    }
  };
  const expiry = setTimeout(() => void close(), 15 * 60_000);
  process.once("SIGINT", () => void close());
  process.once("SIGTERM", () => void close());
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
