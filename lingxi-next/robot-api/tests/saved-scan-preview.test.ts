import { afterEach, describe, expect, it } from "vitest";
import { mkdtemp, rm, writeFile, symlink } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { readSavedScanPreview } from "../src/saved-scan-preview";

const roots: string[] = [];
afterEach(async () => { for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true }); });
const receipt = "a".repeat(64), archiveSha256 = "b".repeat(64);
const observation = { archiveSha256, returns: 100 };
const fixture = { schemaVersion: 1, mapReceiptSha256: receipt, archiveSha256,
  sourceReturns: 100, voxelSize: .05, points: [1, 2, .5, 3, 4, 1.2] };

describe("display-only saved laser returns", () => {
  async function root() { const value = await mkdtemp(path.join(os.tmpdir(), "lx-scan-")); roots.push(value); return value; }
  it("accepts detail bound to the already verified map and observation archive", async () => {
    const directory = await root();
    await writeFile(path.join(directory, "scan-preview.json"), JSON.stringify(fixture));
    expect(await readSavedScanPreview(directory, receipt, observation)).toEqual({
      source: "registered-laser-returns", archiveSha256, sourceReturns: 100, voxelSize: .05, points: fixture.points,
    });
  });
  it("falls back for missing, malformed, non-finite, oversized or different-map detail", async () => {
    const directory = await root(), file = path.join(directory, "scan-preview.json");
    expect(await readSavedScanPreview(directory, receipt, observation)).toBeUndefined();
    for (const value of [{ ...fixture, mapReceiptSha256: "c".repeat(64) },
      { ...fixture, archiveSha256: "c".repeat(64) }, { ...fixture, sourceReturns: 99 },
      { ...fixture, points: [1, null, 0] }, { ...fixture, points: [1, 2] },
      { ...fixture, points: Array(240003).fill(0) }]) {
      await writeFile(file, JSON.stringify(value));
      expect(await readSavedScanPreview(directory, receipt, observation)).toBeUndefined();
    }
    await writeFile(file, JSON.stringify(fixture));
    expect(await readSavedScanPreview(directory, receipt)).toBeUndefined();
  });
  it.skipIf(process.platform === "win32")("rejects indirect cache files", async () => {
    const directory = await root();
    await writeFile(path.join(directory, "actual.json"), JSON.stringify(fixture));
    await symlink(path.join(directory, "actual.json"), path.join(directory, "scan-preview.json"));
    expect(await readSavedScanPreview(directory, receipt, observation)).toBeUndefined();
  });
});
