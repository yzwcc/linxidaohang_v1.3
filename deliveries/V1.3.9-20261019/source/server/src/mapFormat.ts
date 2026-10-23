import { deflateSync } from "node:zlib";
import { parse as parseYaml } from "yaml";

const maxPixels = 20_000_000;
const pngSignature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

export type LingxiMapExport = {
  png: Buffer;
  gridInfo: string;
  width: number;
  height: number;
  resolutionPixelsPerMeter: number;
  originU: number;
  originV: number;
};

export function convertNav2TrinaryMap(pgm: Buffer, yamlText: string): LingxiMapExport {
  const metadata = parseYaml(yamlText) as Record<string, unknown> | null;
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    throw new Error("Nav2 地图 YAML 必须是对象");
  }
  if (metadata.mode !== "trinary") {
    throw new Error("仅支持 Nav2 trinary 地图；scale/raw 的未知栅格语义尚未验证");
  }
  const resolution = finitePositive(metadata.resolution, "resolution");
  const origin = metadata.origin;
  if (!Array.isArray(origin) || origin.length !== 3
      || !origin.every((value) => typeof value === "number" && Number.isFinite(value))) {
    throw new Error("Nav2 origin 必须是有限的 [x, y, yaw]");
  }
  const [originX, originY, yaw] = origin as number[];
  if (Math.abs(yaw) > 1e-8) {
    throw new Error("灵犀 grid_map_info.txt 不支持带旋转角的 Nav2 地图原点");
  }
  if (metadata.negate !== 0) {
    throw new Error("仅支持 Nav2 negate: 0 地图");
  }

  const raster = parsePgm(pgm);
  const pixelsPerMeter = 1 / resolution;
  const originU = -0.5 - originX * pixelsPerMeter;
  const originV = raster.height - 0.5 + originY * pixelsPerMeter;
  if (!Number.isFinite(pixelsPerMeter) || !Number.isFinite(originU) || !Number.isFinite(originV)) {
    throw new Error("地图分辨率或原点超出可表示范围");
  }

  return {
    png: encodeGrayscalePng(raster.width, raster.height, raster.pixels),
    gridInfo: `${formatNumber(pixelsPerMeter)}\n${formatNumber(originU)} ${formatNumber(originV)}\n${raster.width} ${raster.height}\n`,
    width: raster.width,
    height: raster.height,
    resolutionPixelsPerMeter: pixelsPerMeter,
    originU,
    originV
  };
}

function finitePositive(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    throw new Error(`Nav2 ${name} 必须是正的有限数值`);
  }
  return value;
}

function parsePgm(data: Buffer): { width: number; height: number; pixels: Buffer } {
  let offset = 0;
  const readToken = (): string => {
    while (offset < data.length) {
      if (data[offset] === 35) {
        while (offset < data.length && data[offset] !== 10) offset += 1;
      } else if (data[offset] <= 32) {
        offset += 1;
      } else {
        break;
      }
    }
    const start = offset;
    while (offset < data.length && data[offset] > 32 && data[offset] !== 35) offset += 1;
    if (start === offset) throw new Error("PGM 头部不完整");
    return data.toString("ascii", start, offset);
  };

  const magic = readToken();
  if (magic !== "P2" && magic !== "P5") throw new Error("仅支持 PGM P2/P5 灰度地图");
  const width = parseHeaderInteger(readToken(), "width");
  const height = parseHeaderInteger(readToken(), "height");
  const maxValue = parseHeaderInteger(readToken(), "maxval");
  if (width <= 0 || height <= 0 || width * height > maxPixels || maxValue <= 0 || maxValue > 65535) {
    throw new Error("PGM 尺寸或 maxval 超出支持范围");
  }

  const count = width * height;
  const pixels = Buffer.allocUnsafe(count);
  if (magic === "P2") {
    for (let index = 0; index < count; index += 1) {
      const value = parseHeaderInteger(readToken(), "pixel");
      if (value > maxValue) throw new Error("PGM 像素值超过 maxval");
      pixels[index] = Math.round(value * 255 / maxValue);
    }
    if (offset < data.length && data.subarray(offset).some((byte) => byte > 32 && byte !== 35)) {
      throw new Error("PGM 像素数量与尺寸不匹配");
    }
    return { width, height, pixels };
  }

  if (offset >= data.length || data[offset] > 32) throw new Error("PGM P5 头部缺少像素分隔符");
  const separator = data[offset++];
  if (separator === 13 && data[offset] === 10) offset += 1;
  const bytesPerSample = maxValue < 256 ? 1 : 2;
  if (data.length - offset !== count * bytesPerSample) throw new Error("PGM 二进制像素长度与尺寸不匹配");
  for (let index = 0; index < count; index += 1) {
    const value = bytesPerSample === 1 ? data[offset + index] : data.readUInt16BE(offset + index * 2);
    if (value > maxValue) throw new Error("PGM 像素值超过 maxval");
    pixels[index] = Math.round(value * 255 / maxValue);
  }
  return { width, height, pixels };
}

function parseHeaderInteger(value: string, name: string): number {
  if (!/^\d+$/.test(value)) throw new Error(`PGM ${name} 必须是非负整数`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error(`PGM ${name} 超出安全整数范围`);
  return parsed;
}

function encodeGrayscalePng(width: number, height: number, pixels: Buffer): Buffer {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 0;
  const scanlines = Buffer.alloc(height * (width + 1));
  for (let row = 0; row < height; row += 1) {
    const start = row * (width + 1);
    scanlines[start] = 0;
    pixels.copy(scanlines, start + 1, row * width, (row + 1) * width);
  }
  return Buffer.concat([
    pngSignature,
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", deflateSync(scanlines)),
    pngChunk("IEND", Buffer.alloc(0))
  ]);
}

function pngChunk(type: string, payload: Buffer): Buffer {
  const name = Buffer.from(type, "ascii");
  const length = Buffer.alloc(4);
  length.writeUInt32BE(payload.length);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([name, payload])));
  return Buffer.concat([length, name, payload, crc]);
}

function crc32(data: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function formatNumber(value: number): string {
  return Number(value.toPrecision(12)).toString();
}
