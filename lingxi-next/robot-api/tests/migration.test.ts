import { afterEach, describe, expect, it } from "vitest";
import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
// @ts-expect-error Shared operational JavaScript utility has no TS declaration.
import { convertLegacy, migrateLegacy, rollbackLegacyMigration } from "../../tools/migrate-legacy.mjs";

const legacy = () => ({
  map_name: "展厅",
  map_id: 7,
  points: [
    { id: "home", name: "入口", pose: { position: { x: 1, y: 2 }, orientation: { x: 0, y: 0, z: Math.SQRT1_2, w: Math.SQRT1_2 } }, is_start: true },
    { id: "display", name: "展台", x: 3, y: 4, yaw: 0 },
  ],
  tour_tasks: [{ id: "tour", name: "标准讲解", repeat_count: 2, steps: [{ point_id: "display", speech_text: "欢迎", wait_seconds: 9 }] }],
  active_tour_task_id: "tour",
});
const directories: string[] = [];
afterEach(async () => {
  for (const directory of directories.splice(0)) await rm(directory, { recursive: true, force: true });
});
describe("non-destructive legacy migration", () => {
  it("preserves map association, quaternion yaw, point order, repetitions and quiet wait", () => {
    const result = convertLegacy(legacy(), "map.json");
    expect(result.map.mapId).toBe(7);
    expect(result.points[0]).toMatchObject({ mapId: "展厅", isStart: true });
    expect(result.points[0].yaw).toBeCloseTo(Math.PI / 2);
    expect(result.tours[0]).toMatchObject({ repeatCount: 2, steps: [{ pointId: "展厅:display", waitSeconds: 9, narration: "欢迎" }] });
  });
  it("parses string booleans and numeric legacy identifiers without coercing IDs", () => {
    const payload = legacy();
    (payload.points[0] as unknown as { id: unknown; is_start: unknown }).id = 12;
    (payload.points[0] as unknown as { is_start: unknown }).is_start = "false";
    (payload.tour_tasks[0] as unknown as { id: unknown; steps: Array<{ point_id: unknown }> }).id = 31;
    (payload.tour_tasks[0] as unknown as { steps: Array<{ point_id: unknown }> }).steps[0].point_id = 12;
    (payload as unknown as { map_id: unknown }).map_id = "42";
    (payload as unknown as { active_tour_task_id: unknown }).active_tour_task_id = 31;

    const result = convertLegacy(payload, "map.json");
    expect(result.map.mapId).toBe(42);
    expect(result.points[0]).toMatchObject({ id: "展厅:12", isStart: false });
    expect(result.tours[0]).toMatchObject({
      id: "展厅:31",
      steps: [{ id: "展厅:31:1", pointId: "展厅:12" }],
    });
    expect(result.selectedTourId).toBe("展厅:31");
  });
  it("rejects invalid map IDs and active tour references", () => {
    for (const mapId of [0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, true, "1.2"]) {
      const payload = legacy();
      (payload as unknown as { map_id: unknown }).map_id = mapId;
      expect(() => convertLegacy(payload, "map.json")).toThrow("Invalid legacy map ID");
    }

    const payload = legacy();
    (payload as unknown as { active_tour_task_id: unknown }).active_tour_task_id = "missing";
    expect(() => convertLegacy(payload, "map.json")).toThrow("Active tour ID does not match");
  });
  it("rejects missing coordinates, unknown references and invalid limits", () => {
    const missing = legacy();
    delete (missing.points[1] as { x?: number }).x;
    expect(() => convertLegacy(missing, "map.json")).toThrow("Missing coordinates");
    const unknown = legacy();
    unknown.tour_tasks[0].steps[0].point_id = "missing";
    expect(() => convertLegacy(unknown, "map.json")).toThrow("Unknown tour point");
    const wait = legacy();
    wait.tour_tasks[0].steps[0].wait_seconds = 301;
    expect(() => convertLegacy(wait, "map.json")).toThrow("0..300");
  });
  it("dry-runs, backs up originals, preserves source bytes and refuses overwrite", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "lingxi-migration-"));
    directories.push(directory);
    const source = path.join(directory, "source.json");
    const destination = path.join(directory, "new");
    const bytes = JSON.stringify(legacy());
    await writeFile(source, bytes);
    expect((await migrateLegacy({ source, destination })).applied).toBe(false);
    expect(await readdir(directory)).toEqual(["source.json"]);
    const report = await migrateLegacy({ source, destination, apply: true });
    expect(await readFile(source, "utf8")).toBe(bytes);
    expect(await readFile(path.join(report.backupDirectory, "1-source.json"), "utf8")).toBe(bytes);
    expect(JSON.parse(await readFile(path.join(destination, "state.json"), "utf8")).points).toHaveLength(2);
    await expect(migrateLegacy({ source, destination, apply: true })).rejects.toThrow("already contains");
  });
  it("refuses to replace destination state changed before the commit", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "lingxi-migration-race-"));
    directories.push(directory);
    const source = path.join(directory, "source.json");
    const destination = path.join(directory, "new");
    const statePath = path.join(destination, "state.json");
    const initial = {
      schemaVersion: 1, robotName: "Lingxi X2", maps: [], points: [], tours: [],
      idleReturnSeconds: 30, jobRecords: [], ai: { provider: "ark", model: "configure-model-id" },
    };
    const concurrent = JSON.stringify({ ...initial, robotName: "Concurrent update" });
    await mkdir(destination, { recursive: true });
    await writeFile(source, JSON.stringify(legacy()));
    await writeFile(statePath, JSON.stringify(initial));

    await expect(migrateLegacy({
      source,
      destination,
      apply: true,
      beforeCommit: () => writeFile(statePath, concurrent),
    })).rejects.toThrow("Destination state changed during migration");
    expect(await readFile(statePath, "utf8")).toBe(concurrent);
    expect((await readdir(destination)).filter((file) => file.endsWith(".tmp"))).toEqual([]);
  });
  it("binds only manually confirmed coordinates to a rebuilt map", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "lingxi-rebuilt-migration-"));
    directories.push(directory);
    const source = path.join(directory, "source.json");
    const destination = path.join(directory, "new");
    const statePath = path.join(destination, "state.json");
    const mapId = `fastlio-${"9".repeat(32)}`;
    await mkdir(destination);
    const sourceData = legacy();
    sourceData.tour_tasks[0].steps.unshift({ point_id: "home", speech_text: "从这里开始", wait_seconds: 4 });
    await writeFile(source, JSON.stringify(sourceData));
    const originalState = JSON.stringify({ maps: [{ id: mapId, name: "新展厅" }], points: [], tours: [] });
    await writeFile(statePath, originalState);
    const assignments = { 展厅: { mapId, points: { "展厅:home": { x: 7, y: 8, yaw: 0 } } } };
    await expect(migrateLegacy({ source, destination, pointAssignments: assignments }))
      .rejects.toThrow("Confirm every rebuilt position");
    const complete = { 展厅: { mapId, points: { ...assignments.展厅.points, "展厅:display": { x: 2, y: 5, yaw: 0.4 } } } };
    const preview = await migrateLegacy({ source, destination, pointAssignments: complete });
    expect(preview.files[0]).toMatchObject({ targetMap: mapId,
      placements: [{ id: "展厅:home", x: 7, y: 8, yaw: 0 }, { id: "展厅:display", x: 2, y: 5, yaw: 0.4 }],
      tourReviews: [{ pointOrder: ["展厅:home", "展厅:display"] }],
    });
    const operation = { source, destination, pointAssignments: complete, apply: true };
    await expect(migrateLegacy(operation)).rejects.toThrow("review does not match");
    complete.展厅.points["展厅:display"].yaw += 0.1;
    await expect(migrateLegacy({ ...operation, expectedReview: preview.reviewSha256 })).rejects.toThrow("review does not match");
    expect(await readFile(statePath, "utf8")).toBe(originalState);
    complete.展厅.points["展厅:display"].yaw = 0.4;
    await writeFile(statePath, originalState + "\n");
    await expect(migrateLegacy({ ...operation, expectedReview: preview.reviewSha256 })).rejects.toThrow("review does not match");
    await writeFile(statePath, originalState);
    const { backupDirectory } = await migrateLegacy({ ...operation, expectedReview: preview.reviewSha256 });
    const migratedState = await readFile(statePath, "utf8");
    const state = JSON.parse(await readFile(statePath, "utf8"));
    expect(state.maps).toHaveLength(1);
    expect(state.points[0]).toMatchObject({ mapId, x: 7, y: 8, yaw: 0, isStart: true });
    expect(state.tours[0]).toMatchObject({ mapId, requiresReview: true, repeatCount: 2, steps: [
      { pointId: "展厅:home", narration: "从这里开始", waitSeconds: 4 },
      { pointId: "展厅:display", narration: "欢迎", waitSeconds: 9 },
    ] });
    expect(await rollbackLegacyMigration({ destination, backupDirectory })).toMatchObject({ applied: false });
    expect(await readFile(statePath, "utf8")).toBe(migratedState);
    const reverted = await rollbackLegacyMigration({ destination, backupDirectory, apply: true });
    expect(await readFile(reverted.migratedStateBackup, "utf8")).toBe(migratedState);
    expect(await readFile(statePath, "utf8")).toBe(originalState);
    await expect(rollbackLegacyMigration({ destination, backupDirectory, apply: true })).rejects.toThrow("Current state differs");
  });

  it("preserves a later edit and refuses a corrupted backup", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "lingxi-migration-rollback-"));
    directories.push(directory);
    const source = path.join(directory, "source.json");
    const destination = path.join(directory, "new");
    const statePath = path.join(destination, "state.json");
    await mkdir(destination);
    await writeFile(source, JSON.stringify(legacy()));
    await writeFile(statePath, JSON.stringify({ maps: [], points: [], tours: [] }));
    const { backupDirectory } = await migrateLegacy({ source, destination, apply: true });
    const migrated = JSON.parse(await readFile(statePath, "utf8"));
    const changed = JSON.stringify({ ...migrated, robotName: "现场操作员更新" });
    await writeFile(statePath, changed);
    const request = { destination, backupDirectory, apply: true };
    await expect(rollbackLegacyMigration(request)).rejects.toThrow("Current state differs");
    expect(await readFile(statePath, "utf8")).toBe(changed);
    await writeFile(statePath, JSON.stringify(migrated, null, 2));
    await writeFile(path.join(backupDirectory, "previous-state.json"), "{}");
    await expect(rollbackLegacyMigration(request)).rejects.toThrow("Original state backup does not match");
    expect(JSON.parse(await readFile(statePath, "utf8"))).toEqual(migrated);
  });

  it("archives rather than deletes a migration into a previously empty destination", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "lingxi-migration-revert-empty-"));
    directories.push(directory);
    const source = path.join(directory, "source.json");
    const destination = path.join(directory, "new");
    await writeFile(source, JSON.stringify(legacy()));
    const { backupDirectory } = await migrateLegacy({ source, destination, apply: true });
    const statePath = path.join(destination, "state.json");
    const migrated = await readFile(statePath, "utf8");
    const result = await rollbackLegacyMigration({ destination, backupDirectory, apply: true });
    expect(result.restoresPreviousState).toBe(false);
    await expect(readFile(statePath)).rejects.toMatchObject({ code: "ENOENT" });
    expect(await readFile(result.migratedStateBackup, "utf8")).toBe(migrated);
  });

  it("leaves a failed migration uncommitted when inputs change after backup", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "lingxi-migration-input-changed-"));
    directories.push(directory);
    const source = path.join(directory, "source.json");
    const destination = path.join(directory, "new");
    await writeFile(source, JSON.stringify(legacy()));
    await expect(migrateLegacy({ source, destination, apply: true,
      beforeCommit: () => writeFile(source, JSON.stringify({ ...legacy(), map_id: 6 })) }))
      .rejects.toThrow("Legacy source changed");
    const backups = path.join(destination, "backups");
    const [receipt] = await readdir(backups);
    await expect(rollbackLegacyMigration({ destination, backupDirectory: path.join(backups, receipt), apply: true }))
      .rejects.toThrow("unapplied receipt");
    expect(await readdir(destination)).toEqual(["backups"]);
  });

  it("rejects collisions between qualified point IDs from different legacy maps", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "lingxi-migration-collision-"));
    directories.push(directory);
    const source = path.join(directory, "incoming");
    await mkdir(source);
    const payload = { points: [{ id: "b:c", name: "入口", x: 1, y: 2 }] };
    await writeFile(path.join(source, "one.json"), JSON.stringify({ ...payload, map_name: "a" }));
    await writeFile(path.join(source, "two.json"), JSON.stringify({ map_name: "a:b",
      points: [{ ...payload.points[0], id: "c" }] }));
    await expect(migrateLegacy({ source, destination: path.join(directory, "new") })).rejects.toThrow("collide in points");
    expect(await readdir(directory)).toEqual(["incoming"]);
  });

  it("can preview, apply and restore from the operator CLI", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "lingxi-migration-cli-"));
    directories.push(directory);
    const source = path.join(directory, "source.json");
    const destination = path.join(directory, "new");
    const script = fileURLToPath(new URL("../../tools/migrate-legacy.mjs", import.meta.url));
    const run = (...args: string[]) => JSON.parse(execFileSync(process.execPath,
      [script, "--destination", destination, ...args], { encoding: "utf8", windowsHide: true }));
    await writeFile(source, JSON.stringify(legacy()));
    const preview = run("--source-file", source);
    expect(preview.applied).toBe(false);
    const applied = run("--source-file", source, "--expect-review", preview.reviewSha256, "--apply");
    expect(applied.applied).toBe(true);
    expect(run("--rollback", applied.backupDirectory).applied).toBe(false);
    expect(run("--rollback", applied.backupDirectory, "--apply").applied).toBe(true);
    await expect(readFile(path.join(destination, "state.json"))).rejects.toMatchObject({ code: "ENOENT" });
  });
});
