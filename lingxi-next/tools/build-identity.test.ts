import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { assertCurrentWebBuild, assertSyncedWebAssets, fileInventory, productVersion, webBuildIdentity } from "./build-identity.mjs";

const roots: string[] = [];
async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "lingxi-build-"));
  roots.push(root);
  for (const relative of ["web", "shared", "tools", "dist", "android/app/src/main/assets/public"]) {
    await mkdir(path.join(root, relative), { recursive: true });
  }
  await writeFile(path.join(root, "package.json"), JSON.stringify({ name: "lingxi-next", version: "0.1.19", androidVersionCode: 20 }));
  for (const relative of ["web/index.html", "web/z.ts", "shared/a.ts", "package-lock.json", "tsconfig.json",
    "vite.config.ts", "capacitor.config.ts", "tools/build-identity.mjs"]) await writeFile(path.join(root, relative), relative);
  return root;
}
afterEach(async () => { await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true }))); });

describe("Android product build identity", () => {
  it("requires a single product version and a valid Android version code", () => {
    expect(productVersion({ name: "lingxi-next", version: "0.1.19", androidVersionCode: 20 }))
      .toEqual({ name: "lingxi-next", version: "0.1.19", androidVersionCode: 20 });
    for (const androidVersionCode of [undefined, 0, -1, 1.5, "20", 2100000001]) {
      expect(() => productVersion({ name: "lingxi-next", version: "0.1.19", androidVersionCode })).toThrow();
    }
    expect(() => productVersion({ name: "lingxi-next", version: "../invalid", androidVersionCode: 20 })).toThrow();
  });
  it("generates stable source identities independent of inventory traversal order", async () => {
    const root = await fixture();
    expect(await fileInventory(root, ["web", "shared"])).toEqual(await fileInventory(root, ["shared", "web"]));
    await expect(fileInventory(root, ["web", "web/z.ts"])).rejects.toThrow("Duplicate");
    const first = await webBuildIdentity(root);
    await writeFile(path.join(root, "shared/a.ts"), "changed source");
    expect((await webBuildIdentity(root)).source.sha256).not.toBe(first.source.sha256);
  });
  it("rejects missing or stale web manifests even when application versions match", async () => {
    const root = await fixture();
    await expect(assertCurrentWebBuild(root)).rejects.toThrow();
    const identity = await webBuildIdentity(root);
    await writeFile(path.join(root, "dist/lingxi-build.json"), JSON.stringify(identity));
    expect(await assertCurrentWebBuild(root)).toEqual(identity);
    await writeFile(path.join(root, "web/z.ts"), "changed after build");
    await expect(assertCurrentWebBuild(root)).rejects.toThrow("does not match");
  });
  it("rejects stale, missing and unexpected synchronized assets, allowing only Capacitor bridge additions", async () => {
    const root = await fixture();
    const built = path.join(root, "dist/index.html");
    const synced = path.join(root, "android/app/src/main/assets/public/index.html");
    await writeFile(built, "current");
    await expect(assertSyncedWebAssets(root)).rejects.toThrow("stale");
    await writeFile(synced, await readFile(built));
    await writeFile(path.join(path.dirname(synced), "cordova.js"), "bridge");
    expect((await assertSyncedWebAssets(root)).files).toHaveLength(1);
    await writeFile(synced, "old");
    await expect(assertSyncedWebAssets(root)).rejects.toThrow("stale");
    await writeFile(synced, "current");
    await writeFile(path.join(path.dirname(synced), "extra.js"), "not built");
    await expect(assertSyncedWebAssets(root)).rejects.toThrow("stale");
  });
});
