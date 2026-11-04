import { cp, lstat } from "node:fs/promises";
import path from "node:path";

const excludedDirectories = new Set(["node_modules", "__pycache__", "build", "dist",
  "tests", "test", "fixtures", "credentials", "secrets"]);
const sourceExtensions = new Set([".py", ".ts", ".tsx", ".mjs", ".cpp", ".hpp", ".h",
  ".cmake", ".json", ".json5", ".md", ".txt", ".patch", ".map", ".yaml", ".yml", ".sh"]);

export function sourceMemberAllowed(relative, directory) {
  const parts = relative.split(/[\\/]/);
  if (parts.some((part) => part.startsWith(".") || excludedDirectories.has(part.toLowerCase()))) return false;
  const name = parts.at(-1);
  if (directory) return true;
  if (/^(?:test[_-]|private[_-])/i.test(name) || /\.(?:test|spec)\.[^.]+$/.test(name)) return false;
  if (/^(?:credentials?|secrets?|tokens?)(?:[._-]|$)/i.test(name)) return false;
  return name === "CMakeLists.txt" || sourceExtensions.has(path.extname(name).toLowerCase());
}

export async function copySourceTree(source, destination) {
  const root = path.resolve(source);
  await cp(root, destination, { recursive: true, filter: async (file) => {
    const info = await lstat(file);
    if (info.isSymbolicLink()) throw new Error("direct_release_source_required");
    if (!info.isFile() && !info.isDirectory()) throw new Error("ordinary_release_source_required");
    return file === root || sourceMemberAllowed(path.relative(root, file), info.isDirectory());
  } });
}
