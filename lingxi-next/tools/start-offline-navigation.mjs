import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { tsImport } from "tsx/esm/api";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// One Node owner receives terminal signals; no intermediate child can orphan the API.
await tsImport(pathToFileURL(path.join(root, "robot-api/src/offline-main.ts")).href, import.meta.url);
