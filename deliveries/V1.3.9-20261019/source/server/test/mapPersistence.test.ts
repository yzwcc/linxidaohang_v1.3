import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { persistNav2Map, validateLingxiMapArtifacts } from "../src/mapPersistence.js";

const roots: string[] = [];
const yaml = "mode: trinary\nresolution: 0.05\norigin: [-2, -1, 0]\nnegate: 0\n";

async function temporaryRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "lingxi-map-"));
  roots.push(root);
  return root;
}

afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })));
});

describe("atomic Lingxi map persistence", () => {
  it("writes a new numeric map directory in the navigation discovery format", async () => {
    const root = await temporaryRoot();
    const result = await persistNav2Map(root, Buffer.from("P2\n2 1\n255\n0 254\n"), yaml, "123456");
    expect(result.mapId).toBe("123456");
    expect(await fs.readdir(result.directory)).toEqual(["grid_map_info.txt", "occupancy_map.png"]);
    const info = await fs.readFile(path.join(result.directory, "grid_map_info.txt"), "utf8");
    expect(info).toBe("20\n39.5 -19.5\n2 1\n");
    const png = await fs.readFile(path.join(result.directory, "occupancy_map.png"));
    expect(png.toString("ascii", 12, 16)).toBe("IHDR");
    expect(png.readUInt32BE(16)).toBe(2);
    expect(png.readUInt32BE(20)).toBe(1);
  });

  it("never overwrites an existing map ID", async () => {
    const root = await temporaryRoot();
    const existing = path.join(root, "123456");
    await fs.mkdir(existing);
    await fs.writeFile(path.join(existing, "keep.txt"), "preserve me");
    await expect(persistNav2Map(root, Buffer.from("P2\n1 1\n255\n0\n"), yaml, "123456"))
      .rejects.toThrow();
    expect(await fs.readFile(path.join(existing, "keep.txt"), "utf8")).toBe("preserve me");
  });

  it("rejects invalid maps before reserving an ID", async () => {
    const root = await temporaryRoot();
    await expect(persistNav2Map(root, Buffer.from("not a map"), yaml, "123456")).rejects.toThrow();
    expect(await fs.readdir(root)).toEqual([]);
  });

  it("rejects PNGs with corrupted chunks, truncated payloads, or mismatched metadata", async () => {
    const root = await temporaryRoot();
    const result = await persistNav2Map(root, Buffer.from("P2\n2 1\n255\n0 254\n"), yaml, "123456");
    const valid = await fs.readFile(path.join(result.directory, "occupancy_map.png"));
    const corrupted = Buffer.from(valid);
    corrupted[corrupted.length - 6] ^= 0xff;
    expect(() => validateLingxiMapArtifacts(corrupted, "20\n39.5 -19.5\n2 1\n", 2, 1)).toThrow("校验失败");
    expect(() => validateLingxiMapArtifacts(valid.subarray(0, valid.length - 2), "20\n39.5 -19.5\n2 1\n", 2, 1)).toThrow();
    expect(() => validateLingxiMapArtifacts(valid, "20\n39.5 -19.5\n3 1\n", 2, 1)).toThrow("尺寸或分辨率不一致");
  });

  it.each(["0", "-1", "../123", "x123", "123456789012345678901"])("rejects unsafe map ID %s", async (mapId) => {
    const root = await temporaryRoot();
    await expect(persistNav2Map(root, Buffer.from("P2\n1 1\n255\n0\n"), yaml, mapId)).rejects.toThrow();
    expect(await fs.readdir(root)).toEqual([]);
  });
});
