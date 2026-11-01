import { createHash } from "node:crypto";
import { lstat, readFile, readdir } from "node:fs/promises";
import path from "node:path";

export const BUILD_IDENTITY_FILE = "lingxi-build.json";
const WEB_INPUTS = ["web", "shared", "package.json", "package-lock.json", "tsconfig.json",
  "vite.config.ts", "capacitor.config.ts", "tools/build-identity.mjs"];

export function productVersion(metadata) {
  if (metadata.name !== "lingxi-next" || typeof metadata.version !== "string" ||
      !/^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$/.test(metadata.version) ||
      !Number.isSafeInteger(metadata.androidVersionCode) || metadata.androidVersionCode < 1 ||
      metadata.androidVersionCode > 2100000000) throw new Error("Invalid product version metadata");
  return { name: metadata.name, version: metadata.version, androidVersionCode: metadata.androidVersionCode };
}

export async function fileInventory(root, inputs, exclude = () => false) {
  const files = [];
  async function visit(relative) {
    if (exclude(relative)) return;
    const absolute = path.join(root, relative);
    const stat = await lstat(absolute);
    if (stat.isSymbolicLink()) throw new Error(`Build inputs cannot be symbolic links: ${relative}`);
    if (stat.isDirectory()) {
      for (const name of (await readdir(absolute)).sort()) await visit(`${relative}/${name}`);
    } else if (stat.isFile()) {
      const data = await readFile(absolute);
      files.push({ path: relative, bytes: data.length, sha256: createHash("sha256").update(data).digest("hex") });
    } else throw new Error(`Unsupported build input: ${relative}`);
  }
  for (const input of inputs) await visit(input);
  files.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  if (new Set(files.map((file) => file.path)).size !== files.length) throw new Error("Duplicate build inputs");
  return { algorithm: "sha256", sha256: createHash("sha256").update(JSON.stringify(files)).digest("hex"), files };
}

export async function webBuildIdentity(root) {
  const product = productVersion(JSON.parse(await readFile(path.join(root, "package.json"), "utf8")));
  return { schema: 1, product, source: await fileInventory(root, WEB_INPUTS) };
}

export async function assertCurrentWebBuild(root) {
  const actual = JSON.parse(await readFile(path.join(root, "dist", BUILD_IDENTITY_FILE), "utf8"));
  const expected = await webBuildIdentity(root);
  if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new Error("Web build does not match current source; rebuild and sync Android");
  return expected;
}

export async function androidBuildIdentity(root) {
  return fileInventory(root, ["android/app/src/main", "android/app/build.gradle", "android/build.gradle",
    "android/variables.gradle", "android/settings.gradle", "android/gradle.properties",
    "android/capacitor.settings.gradle", "android/app/capacitor.build.gradle", "android/gradle/wrapper",
    "android/app/proguard-rules.pro", "tools/build-android.mjs", "tools/build-identity.mjs",
    "tools/download.mjs", "tools/maven-proxy.mjs", "tools/verify-android.py", "package.json", "package-lock.json"],
  (relative) => relative === "android/app/src/main/assets/public");
}

export async function assertSyncedWebAssets(root) {
  const built = await fileInventory(root, ["dist"]);
  const synced = await fileInventory(root, ["android/app/src/main/assets/public"]);
  const normalize = (inventory, prefix) => inventory.files.map((file) => ({ ...file, path: file.path.slice(prefix.length) }));
  const expected = normalize(built, "dist/");
  const actual = normalize(synced, "android/app/src/main/assets/public/")
    .filter((file) => !["cordova.js", "cordova_plugins.js"].includes(file.path));
  if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new Error("Android web assets are stale; run cap sync android");
  return built;
}
