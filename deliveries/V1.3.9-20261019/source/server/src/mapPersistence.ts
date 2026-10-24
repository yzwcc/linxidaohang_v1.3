import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { convertNav2TrinaryMap } from "./mapFormat.js";

export type PersistedLingxiMap = {
  mapId: string;
  directory: string;
  width: number;
  height: number;
  resolutionPixelsPerMeter: number;
};

export async function persistNav2Map(
  mapRoot: string,
  pgm: Buffer,
  yamlText: string,
  requestedMapId = String(Date.now())
): Promise<PersistedLingxiMap> {
  if (!/^\d{1,20}$/.test(requestedMapId) || /^0+$/.test(requestedMapId)) {
    throw new Error("新地图编号必须是 1 到 20 位的正整数");
  }
  const converted = convertNav2TrinaryMap(pgm, yamlText);
  validateGeneratedMap(converted.png, converted.gridInfo, converted.width, converted.height);

  const rootInfo = await fs.lstat(mapRoot);
  if (!rootInfo.isDirectory()) throw new Error("MapManager 根路径必须是已存在的目录");
  const root = await fs.realpath(mapRoot);
  const mapDirectory = path.join(root, requestedMapId);

  // mkdir is the exclusive reservation: an existing map ID is never replaced.
  await fs.mkdir(mapDirectory, { mode: 0o750 });
  const staging = path.join(mapDirectory, `.staging-${randomUUID()}`);
  try {
    await fs.mkdir(staging, { mode: 0o700 });
    await fs.writeFile(path.join(staging, "grid_map_info.txt"), converted.gridInfo, { flag: "wx", mode: 0o640 });
    await fs.writeFile(path.join(staging, "occupancy_map.png"), converted.png, { flag: "wx", mode: 0o640 });

    // Navigation only discovers maps containing both files; publish the image last.
    await fs.rename(path.join(staging, "grid_map_info.txt"), path.join(mapDirectory, "grid_map_info.txt"));
    await fs.rename(path.join(staging, "occupancy_map.png"), path.join(mapDirectory, "occupancy_map.png"));
    await fs.rmdir(staging);
    return {
      mapId: requestedMapId,
      directory: mapDirectory,
      width: converted.width,
      height: converted.height,
      resolutionPixelsPerMeter: converted.resolutionPixelsPerMeter
    };
  } catch (error) {
    await fs.rm(staging, { recursive: true, force: true });
    await fs.rm(path.join(mapDirectory, "grid_map_info.txt"), { force: true });
    await fs.rm(path.join(mapDirectory, "occupancy_map.png"), { force: true });
    await fs.rmdir(mapDirectory).catch(() => undefined);
    throw error;
  }
}

function validateGeneratedMap(png: Buffer, gridInfo: string, expectedWidth: number, expectedHeight: number): void {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (png.length < 33 || !png.subarray(0, 8).equals(signature)
      || png.toString("ascii", 12, 16) !== "IHDR") {
    throw new Error("转换结果不是有效的 PNG 地图");
  }
  const width = png.readUInt32BE(16);
  const height = png.readUInt32BE(20);
  if (width !== expectedWidth || height !== expectedHeight || width <= 0 || height <= 0) {
    throw new Error("PNG 尺寸与地图转换结果不一致");
  }
  const lines = gridInfo.trim().split(/\s+/).map(Number);
  if (lines.length !== 5 || !lines.every(Number.isFinite)
      || lines[0] <= 0 || lines[3] !== width || lines[4] !== height) {
    throw new Error("grid_map_info.txt 与 PNG 尺寸或分辨率不一致");
  }
}
