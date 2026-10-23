import { inflateSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { convertNav2TrinaryMap } from "../src/mapFormat.js";

function pngPixels(png: Buffer): { width: number; height: number; pixels: number[] } {
  let offset = 8;
  let width = 0;
  let height = 0;
  const compressed: Buffer[] = [];
  while (offset + 12 <= png.length) {
    const length = png.readUInt32BE(offset);
    const type = png.toString("ascii", offset + 4, offset + 8);
    const payload = png.subarray(offset + 8, offset + 8 + length);
    if (type === "IHDR") {
      width = payload.readUInt32BE(0);
      height = payload.readUInt32BE(4);
    }
    if (type === "IDAT") compressed.push(payload);
    offset += length + 12;
    if (type === "IEND") break;
  }
  const raw = inflateSync(Buffer.concat(compressed));
  const pixels: number[] = [];
  for (let y = 0; y < height; y += 1) {
    const start = y * (width + 1);
    expect(raw[start]).toBe(0);
    for (let x = 0; x < width; x += 1) pixels.push(raw[start + x + 1]);
  }
  return { width, height, pixels };
}

describe("Nav2 trinary map conversion", () => {
  it("preserves Nav2 grayscale and derives Lingxi pixel-center metadata", () => {
    const result = convertNav2TrinaryMap(
      Buffer.from("P2\n# map\n2 2\n255\n0 254\n205 100\n"),
      "image: map.pgm\nmode: trinary\nresolution: 0.5\norigin: [-2, -3, 0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n"
    );
    expect(pngPixels(result.png)).toEqual({ width: 2, height: 2, pixels: [0, 254, 205, 100] });
    expect(result.gridInfo).toBe("2\n3.5 -4.5\n2 2\n");
    expect((0 - result.originU) / result.resolutionPixelsPerMeter).toBe(-1.75);
    expect(-(0 - result.originV) / result.resolutionPixelsPerMeter).toBe(-2.25);
    const convertedPixels = pngPixels(result.png).pixels;
    expect(convertedPixels.map((pixel) => pixel <= 205)).toEqual([true, false, true, true]);
  });

  it("supports binary 16-bit PGM samples", () => {
    const pgm = Buffer.concat([
      Buffer.from("P5\n2 1\n1023\n"),
      Buffer.from([0x00, 0x00, 0x03, 0xff])
    ]);
    const result = convertNav2TrinaryMap(pgm, "mode: trinary\nresolution: 1\norigin: [0, 0, 0]\nnegate: 0\n");
    expect(pngPixels(result.png).pixels).toEqual([0, 255]);
  });

  it.each([
    ["rejects rotated origins", "mode: trinary\nresolution: 0.1\norigin: [0, 0, 0.1]\nnegate: 0\n"],
    ["rejects non-trinary images", "mode: scale\nresolution: 0.1\norigin: [0, 0, 0]\nnegate: 0\n"],
    ["rejects inverted occupancy", "mode: trinary\nresolution: 0.1\norigin: [0, 0, 0]\nnegate: 1\n"]
  ])("%s", (_name, yaml) => {
    expect(() => convertNav2TrinaryMap(Buffer.from("P2\n1 1\n255\n0\n"), yaml as string)).toThrow();
  });

  it("rejects malformed and oversized PGM rasters", () => {
    const yaml = "mode: trinary\nresolution: 0.1\norigin: [0, 0, 0]\nnegate: 0\n";
    expect(() => convertNav2TrinaryMap(Buffer.from("P2\n2 2\n255\n0 0 0\n"), yaml)).toThrow();
    expect(() => convertNav2TrinaryMap(Buffer.from("P2\n5000 5000\n255\n"), yaml)).toThrow("超出支持范围");
    expect(() => convertNav2TrinaryMap(Buffer.from("P5\n1 1\n255\n"), yaml)).toThrow("长度与尺寸不匹配");
  });
});
