import { constants } from "node:fs";
import { open, realpath } from "node:fs/promises";
import path from "node:path";
import { z } from "zod";

const integer = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const schema = z.object({
  schemaVersion: z.literal(1), session: z.string().regex(/^[a-f0-9]{32}$/),
  inputMode: z.enum(["live", "recorded", "synthetic"]),
  revision: integer, submaps: integer, totalPoints: integer.max(2_000_000),
  matchingFactors: z.number().int().nonnegative(), nonAdjacentMatchingFactors: z.number().int().nonnegative(),
  coordinateFrame: z.literal("lingxi_glim_map"), coordinateUnits: z.literal("native-raw"),
  updatedAtMs: z.number().finite().positive(),
  points: z.array(z.number().finite()).min(3).max(24_000).refine((values) => values.length % 3 === 0),
}).strict();
export type CorrectedMapPreview = z.infer<typeof schema>;

export async function readCorrectedMapPreview(directory: string, session: string): Promise<CorrectedMapPreview | undefined> {
  const parent = path.join(directory, session);
  let file;
  try {
    if (await realpath(parent) !== parent) throw new Error("direct_corrected_preview_directory_required");
    file = await open(path.join(parent, "corrected-preview.json"), constants.O_RDONLY | constants.O_NOFOLLOW);
    const info = await file.stat();
    if (!info.isFile() || info.mode & 0o077 || info.uid !== process.geteuid!() || info.size > 2 * 1024 * 1024) {
      throw new Error("private_bounded_corrected_preview_required");
    }
    const bytes = await file.readFile();
    if (bytes.length > 2 * 1024 * 1024) throw new Error("bounded_corrected_preview_required");
    const value = schema.parse(JSON.parse(bytes.toString("utf8")));
    if (value.session !== session || value.points.length / 3 > value.totalPoints ||
        value.nonAdjacentMatchingFactors > value.matchingFactors || value.updatedAtMs > Date.now() + 2000) {
      throw new Error("coherent_corrected_preview_required");
    }
    return value;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  } finally { await file?.close(); }
}
