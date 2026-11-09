import { constants } from "node:fs";
import { open } from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import type { SavedSlamPointPreview } from "../../shared/contracts";

const digest = z.string().regex(/^[a-f0-9]{64}$/);
const schema = z.object({
  schemaVersion: z.literal(1), mapReceiptSha256: digest, archiveSha256: digest,
  sourceReturns: z.number().int().positive().max(16777216), voxelSize: z.literal(.05),
  points: z.array(z.number().finite()).min(3).max(240000).refine((v) => v.length % 3 === 0),
}).strict();

export async function readSavedScanPreview(directory: string, receiptSha256: string,
  observation?: { archiveSha256: string; returns: number }): Promise<SavedSlamPointPreview["scanPreview"]> {
  if (!observation) return undefined;
  let handle;
  try {
    handle = await open(path.join(directory, "scan-preview.json"), constants.O_RDONLY | constants.O_NOFOLLOW);
    const info = await handle.stat();
    if (!info.isFile() || info.size > 8 * 1024 * 1024) return undefined;
    const bytes = await handle.readFile();
    if (bytes.length > 8 * 1024 * 1024) return undefined;
    const value = schema.parse(JSON.parse(bytes.toString("utf8")));
    if (value.mapReceiptSha256 !== receiptSha256 || value.archiveSha256 !== observation.archiveSha256 ||
        value.sourceReturns !== observation.returns || value.points.length / 3 > value.sourceReturns) return undefined;
    return { source: "registered-laser-returns", archiveSha256: value.archiveSha256,
      sourceReturns: value.sourceReturns, voxelSize: value.voxelSize, points: value.points };
  } catch { return undefined; /* A missing display cache never invalidates a saved map. */ }
  finally { await handle?.close(); }
}
