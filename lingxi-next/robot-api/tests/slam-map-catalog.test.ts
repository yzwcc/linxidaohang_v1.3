import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createHash } from "node:crypto";
import { cp, mkdir, mkdtemp, readFile, rename, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import type { FastifyInstance } from "fastify";
import { SavedSlamMapCatalog } from "../src/slam-map-catalog";
import { savedSlamFixture } from "./fixtures/saved-slam-map";
import { buildServer } from "../src/server";
import { pairingCodeHash } from "../src/auth";
import { DemoAdapter } from "../src/adapters/demo";
import { JsonStateStore } from "../src/store";

describe("saved SLAM receipt catalog", () => {
  let root: string;
  let maps: string;
  let catalog: SavedSlamMapCatalog;
  let app: FastifyInstance | undefined;
  beforeEach(async () => {
    root = await mkdtemp(path.join(tmpdir(), "lingxi-saved-slam-"));
    maps = path.join(root, "maps");
    await mkdir(maps);
    catalog = new SavedSlamMapCatalog(maps);
  });
  afterEach(async () => {
    await app?.close();
    app = undefined;
    await rm(root, { recursive: true, force: true });
  });

  it("reads the Python receipt inventory format and preserves all readiness boundaries", async () => {
    const fixture = await savedSlamFixture(maps);
    const before = await readFile(fixture.ready);
    expect(await catalog.list()).toEqual([{
      id: fixture.id, name: "Synthetic saved map", source: "glim", inputMode: "synthetic",
      counts: fixture.receipt.counts, coordinateUnits: "native-raw",
      freshProcessReloadPassed: true, navigationReady: false, localizationReady: false,
      geometricAccuracyValidated: false,
    }]);
    const preview = await catalog.preview(fixture.id);
    expect(preview).toMatchObject({ mapId: fixture.id, sampled: false, totalPoints: 1440, coordinateUnits: "native-raw" });
    expect(preview.points.slice(0, 3)).toEqual([6, 0, 0]);
    expect(preview.points).toHaveLength(1440 * 3);
    expect(await readFile(fixture.ready)).toEqual(before);
    expect(JSON.stringify(await catalog.list())).not.toContain(root);
  });

  it.each(["live", "recorded", "synthetic"] as const)("keeps %s classification without adding AIMDK identity", async (inputMode) => {
    await savedSlamFixture(maps, { inputMode });
    const [map] = await catalog.list();
    expect(map.inputMode).toBe(inputMode);
    expect(map).not.toHaveProperty("mapId");
    expect(map).not.toHaveProperty("active");
  });

  it("excludes pending, abandoned, corrupt and duplicate-session saves", async () => {
    const fixture = await savedSlamFixture(maps);
    await rename(fixture.ready, path.join(fixture.directory, "map-ready.pending"));
    expect(await catalog.list()).toEqual([]);
    await rename(path.join(fixture.directory, "map-ready.pending"), fixture.ready);
    await cp(fixture.directory, path.join(maps, "duplicate"), { recursive: true });
    expect(await catalog.list()).toEqual([]);
    await rm(path.join(maps, "duplicate"), { recursive: true });
    await writeFile(fixture.ready, "not-json");
    expect(await catalog.list()).toEqual([]);
  });

  it.each(["navigationReady", "localizationReady", "geometricAccuracyValidated"] as const)(
    "rejects an unsupported %s promotion in a receipt", async (key) => {
      const fixture = await savedSlamFixture(maps);
      await writeFile(fixture.ready, JSON.stringify({ ...fixture.receipt, [key]: true }));
      expect(await catalog.list()).toEqual([]);
    },
  );

  it("excludes changed map and point bytes, even when file sizes still match", async () => {
    const fixture = await savedSlamFixture(maps);
    const file = path.join(fixture.directory, "map/graph.bin");
    const original = await readFile(file);
    const altered = Buffer.from(original); altered[0] ^= 1;
    await writeFile(file, altered);
    expect(await catalog.list()).toEqual([]);
    await writeFile(file, original);
    const points = await readFile(fixture.points); points[0] ^= 1;
    await writeFile(fixture.points, points);
    expect(await catalog.list()).toEqual([]);
    await expect(catalog.preview(fixture.id)).rejects.toMatchObject({ statusCode: 404 });
  });

  it("excludes truncated points and mismatched frame counts", async () => {
    const fixture = await savedSlamFixture(maps);
    const points = await readFile(fixture.points);
    await writeFile(fixture.points, points.subarray(0, points.length - 1));
    expect(await catalog.list()).toEqual([]);
    await writeFile(fixture.points, points);
    fixture.receipt.counts.finalizedFrames += 1;
    await writeFile(fixture.ready, JSON.stringify(fixture.receipt));
    expect(await catalog.list()).toEqual([]);
  });

  it("bounds previews and checks unsampled coordinates for finite values", async () => {
    const fixture = await savedSlamFixture(maps, { points: 40_001 });
    const preview = await catalog.preview(fixture.id);
    expect(preview.sampled).toBe(true);
    expect(preview.points.length / 3).toBeLessThanOrEqual(20_000);
    const bytes = await readFile(fixture.points);
    bytes.writeDoubleLE(NaN, 24); // Not selected by the deterministic preview stride.
    fixture.receipt.savedPointsSha256 = createHash("sha256").update(bytes).digest("hex");
    await writeFile(fixture.points, bytes);
    await writeFile(fixture.ready, JSON.stringify(fixture.receipt));
    await expect(catalog.preview(fixture.id)).rejects.toThrow("finite_saved_slam_points_required");
  });

  it("does not traverse linked roots or session directories", async () => {
    const fixture = await savedSlamFixture(maps);
    const linkedRoot = path.join(root, "linked");
    await symlink(maps, linkedRoot, process.platform === "win32" ? "junction" : "dir");
    await expect(new SavedSlamMapCatalog(linkedRoot).list()).rejects.toThrow("direct_slam_artifact");
    await symlink(fixture.directory, path.join(maps, "linked-map"), process.platform === "win32" ? "junction" : "dir");
    expect(await catalog.list()).toHaveLength(1);
  });

  it("reports a missing root and rejects relative configuration and arbitrary map paths", async () => {
    expect(() => new SavedSlamMapCatalog("relative")).toThrow();
    await expect(new SavedSlamMapCatalog(path.join(root, "absent")).list()).rejects.toThrow();
    await expect(catalog.preview("../private")).rejects.toMatchObject({ statusCode: 404 });
  });

  it("serves paired catalog and points without importing them into navigable maps", async () => {
    const fixture = await savedSlamFixture(maps);
    const adapter = new DemoAdapter();
    const activate = vi.spyOn(adapter, "activateMap");
    const stateDirectory = path.join(root, "state");
    app = await buildServer({
      demoMode: true, adapter, stateDirectory, savedSlamDirectory: maps,
      staticDirectory: path.join(root, "absent"), pairingCodeHash: pairingCodeHash("synthetic-catalog-only"),
    });
    for (const url of ["/api/v1/slam/maps", `/api/v1/slam/maps/${fixture.id}/points`]) {
      expect((await app.inject(url)).statusCode).toBe(401);
    }
    const paired = await app.inject({
      method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: "synthetic-catalog-only" },
    });
    const headers = { authorization: `Bearer ${paired.json().token}` };
    const state = (await app.inject({ url: "/api/v1/state", headers })).json();
    expect(state.savedSlamCatalogStatus).toBe("ready");
    expect(state.savedSlamMaps).toHaveLength(1);
    expect(state.maps.some((map: { id: string }) => map.id === fixture.id)).toBe(false);
    const db = await new JsonStateStore(stateDirectory).read();
    expect(db.maps.some((map) => map.id === fixture.id)).toBe(false);
    const listed = await app.inject({ url: "/api/v1/slam/maps", headers });
    expect(listed.statusCode).toBe(200);
    expect(listed.json().maps).toEqual(state.savedSlamMaps);
    const points = await app.inject({ url: `/api/v1/slam/maps/${fixture.id}/points`, headers });
    expect(points.statusCode).toBe(200);
    expect(points.json().points).toHaveLength(1440 * 3);
    expect(points.body).not.toContain(root);
    expect((await app.inject({
      method: "POST", url: `/api/v1/maps/${fixture.id}/activate`, headers, payload: {},
    })).statusCode).toBe(404);
    expect((await app.inject({
      method: "POST", url: "/api/v1/points", headers,
      payload: { mapId: fixture.id, name: "unlocalized", x: 0, y: 0, yaw: 0 },
    })).statusCode).toBe(404);
    expect(activate).not.toHaveBeenCalled();
  });

  it("keeps normal management available when the optional catalog is unavailable", async () => {
    app = await buildServer({
      demoMode: true, stateDirectory: path.join(root, "state"), savedSlamDirectory: path.join(root, "absent"),
      staticDirectory: path.join(root, "absent"), pairingCodeHash: pairingCodeHash("synthetic-catalog-only"),
    });
    const paired = await app.inject({
      method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: "synthetic-catalog-only" },
    });
    const headers = { authorization: `Bearer ${paired.json().token}` };
    const state = (await app.inject({ url: "/api/v1/state", headers })).json();
    expect(state.savedSlamCatalogStatus).toBe("unavailable");
    expect(state.maps).toHaveLength(2);
    expect((await app.inject({ url: "/api/v1/slam/maps", headers })).statusCode).toBe(500);
  });
});
