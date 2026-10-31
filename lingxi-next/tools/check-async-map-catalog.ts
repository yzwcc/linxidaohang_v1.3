/** Read actual native product receipts through the API catalog; no robot or writes. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { parseArgs } from "node:util";
import { SavedSlamMapCatalog } from "../robot-api/src/slam-map-catalog";

const { values } = parseArgs({ options: { root: { type: "string" }, report: { type: "string" } } });
assert(values.root && values.report, "explicit_native_trial_catalog_and_report_required");
const report = JSON.parse(await readFile(values.report, "utf8"));
assert.equal(report.result, "recorded_full_mapper_product_receipt_saved_reloaded_not_live_acceptance");
const catalog = new SavedSlamMapCatalog(values.root);
const [map, ...others] = await catalog.list();
assert(map && others.length === 0, "exactly_one_native_product_map_required");
assert.equal(map.mappingEngine, "glim-full-async-v1");
assert.equal(map.inputMode, "recorded");
assert.deepEqual(map.counts, report.counts);
assert.equal(map.navigationReady, false);
assert.equal(map.localizationReady, false);
const preview = await catalog.preview(map.id);
assert.equal(preview.receiptSha256, report.receiptSha256);
assert.equal(preview.totalPoints, map.counts.points);
assert(preview.points.length > 0 && preview.points.every(Number.isFinite));
assert(preview.points.length / 3 <= 20000);
assert.equal(preview.seedReferences?.frames, map.counts.savedFrames);
console.log(JSON.stringify({ result: "actual_native_full_map_catalog_and_preview_verified",
  frames: map.counts.savedFrames, points: map.counts.points, previewPoints: preview.points.length / 3,
  endpointReferences: Boolean(preview.seedReferences), robotContacted: false, navigationValidated: false }));
