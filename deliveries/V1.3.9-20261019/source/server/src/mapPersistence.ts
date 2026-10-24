import { inflateSync } from "node:zlib";
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
  validateLingxiMapArtifacts(converted.png, converted.gridInfo, converted.width, converted.height);

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

export function validateLingxiMapArtifacts(png: Buffer, gridInfo: string, expectedWidth: number, expectedHeight: number): void {
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

  let offset = 8;
  let sawHeader = false;
  let sawImageData = false;
  let sawEnd = false;
  const compressed: Buffer[] = [];
  while (offset < png.length) {
    if (offset + 12 > png.length) throw new Error("PNG chunk 数据不完整");
    const length = png.readUInt32BE(offset);
    const end = offset + 12 + length;
    if (end > png.length) throw new Error("PNG chunk 长度超出文件范围");
    const type = png.toString("ascii", offset + 4, offset + 8);
    const data = png.subarray(offset + 8, offset + 8 + length);
    const expectedCrc = png.readUInt32BE(offset + 8 + length);
    if (crc32(png.subarray(offset + 4, offset + 8 + length)) !== expectedCrc) {
      throw new Error(`PNG ${type} chunk 校验失败`);
    }
    if (type === "IHDR") {
      if (sawHeader || offset !== 8 || length !== 13
          || data[8] !== 8 || data[9] !== 0 || data[10] !== 0 || data[11] !== 0 || data[12] !== 0) {
        throw new Error("PNG 必须是 8-bit 灰度、非交错地图");
      }
      sawHeader = true;
    } else if (type === "IDAT") {
      if (!sawHeader || sawEnd) throw new Error("PNG IDAT chunk 顺序无效");
      sawImageData = true;
      compressed.push(data);
    } else if (type === "IEND") {
      if (!sawImageData || length !== 0 || end !== png.length) throw new Error("PNG IEND chunk 无效");
      sawEnd = true;
      offset = end;
      break;
    }
    offset = end;
  }
  if (!sawHeader || !sawImageData || !sawEnd) throw new Error("PNG 缺少必要的图像 chunk");

  let raster: Buffer;
  try {
    raster = inflateSync(Buffer.concat(compressed), { maxOutputLength: height * (width + 1) });
  } catch {
    throw new Error("PNG 图像数据无法解压");
  }
  if (raster.length !== height * (width + 1)) throw new Error("PNG 解压尺寸与图像元数据不一致");
  for (let row = 0; row < height; row += 1) {
    if (raster[row * (width + 1)] !== 0) throw new Error("PNG 地图包含未支持的行过滤器");
  }

  const lines = gridInfo.trim().split(/\s+/).map(Number);
  if (lines.length !== 5 || !lines.every(Number.isFinite)
      || lines[0] <= 0 || lines[3] !== width || lines[4] !== height) {
    throw new Error("grid_map_info.txt 与 PNG 尺寸或分辨率不一致");
  }
}

function crc32(data: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
