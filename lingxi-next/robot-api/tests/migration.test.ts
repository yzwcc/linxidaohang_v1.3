import { afterEach, describe, expect, it } from "vitest";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
// @ts-expect-error Shared operational JavaScript utility has no TS declaration.
import { convertLegacy, migrateLegacy } from "../../tools/migrate-legacy.mjs";

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
});
