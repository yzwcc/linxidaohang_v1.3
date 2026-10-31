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
    expect(preview.receiptSha256).toBe(createHash("sha256").update(before).digest("hex"));
    expect(preview.pointsSha256).toBe(createHash("sha256").update(await readFile(fixture.points)).digest("hex"));
    expect(preview.points.slice(0, 3)).toEqual([6, 0, 0]);
    expect(preview.points).toHaveLength(1440 * 3);
    expect(preview.seedReferences).toBeUndefined();
    expect(await readFile(fixture.ready)).toEqual(before);
    expect(JSON.stringify(await catalog.list())).not.toContain(root);
  });

  it("reads v2 sensor provenance without treating it as readiness evidence", async () => {
    const sensorProvenance = {
      schemaVersion: 1 as const, profileSha256: "a".repeat(64),
      timingHypothesis: "header_is_scan_start" as const,
      configurationSha256: { "lidar.json": "b".repeat(64) },
    };
    const fixture = await savedSlamFixture(maps, { sensorProvenance });
    const [map] = await catalog.list();
    expect(map.sensorProvenance).toEqual(sensorProvenance);
    expect(map.localizationReady).toBe(false);
    expect(map.navigationReady).toBe(false);
    expect((await catalog.preview(fixture.id)).mapId).toBe(fixture.id);
    const references = (await catalog.preview(fixture.id)).seedReferences!;
    expect(references).toMatchObject({ source: "glim-final-lidar-trajectory", sourceFrame: "lidar_chest_front",
      profileSha256: sensorProvenance.profileSha256, frames: 2, first: { frameIndex: 1 }, last: { frameIndex: 2 } });
    expect(references.trajectorySha256).toBe(createHash("sha256").update(
      await readFile(path.join(fixture.directory, "map/traj_lidar.txt"))).digest("hex"));
    expect(references.last.mapFromSensor[0][3]).toBe(1);
  });

  it("omits endpoint references rather than inventing a pose for a valid map with unusable trajectory metadata", async () => {
    const fixture = await savedSlamFixture(maps, { lidarTrajectory: "0 0 0 0 0 0 0 1\n1 1 0 0 0 0 0 0\n",
      sensorProvenance: { schemaVersion: 1, profileSha256: "a".repeat(64), timingHypothesis: "header_is_scan_start",
        configurationSha256: { "lidar.json": "b".repeat(64) } } });
    expect(await catalog.list()).toHaveLength(1);
    expect((await catalog.preview(fixture.id)).seedReferences).toBeUndefined();
  });

  it("excludes malformed v2 provenance and refuses provenance smuggled into v1", async () => {
    const provenance = {
      schemaVersion: 1 as const, profileSha256: "a".repeat(64),
      timingHypothesis: "header_is_scan_start" as const,
      configurationSha256: { "lidar.json": "b".repeat(64) },
    };
    const fixture = await savedSlamFixture(maps, { sensorProvenance: provenance });
    await writeFile(fixture.ready, JSON.stringify({
      ...fixture.receipt, sensorProvenance: { ...provenance, unexpected: true },
    }));
    expect(await catalog.list()).toEqual([]);
    await writeFile(fixture.ready, JSON.stringify({
      ...fixture.receipt, schemaVersion: 1,
    }));
    expect(await catalog.list()).toEqual([]);
  });

  it("distinguishes changed receipt identity even when the map ID and point bytes are retained", async () => {
    const fixture = await savedSlamFixture(maps);
    const before = await catalog.preview(fixture.id);
    await writeFile(fixture.ready, JSON.stringify({ ...fixture.receipt, name: "Renamed map" }));
    const after = await catalog.preview(fixture.id);
    expect(after.mapId).toBe(before.mapId);
    expect(after.pointsSha256).toBe(before.pointsSha256);
    expect(after.receiptSha256).not.toBe(before.receiptSha256);
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
