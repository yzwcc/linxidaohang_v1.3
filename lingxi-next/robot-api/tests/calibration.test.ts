import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtemp, readFile, readdir, rm, symlink, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import type { FastifyInstance } from "fastify";
import type { SavedSlamPointPreview } from "../../shared/contracts";
import { calibrationSurveySchema, calibrationCandidateSchema, type CalibrationSurvey } from "../../shared/calibration";
import { CalibrationService, computeCalibration, previewCalibrationBinding } from "../src/calibration";
import { SavedSlamMapCatalog } from "../src/slam-map-catalog";
import { savedSlamFixture } from "./fixtures/saved-slam-map";
import { buildServer } from "../src/server";
import { pairingCodeHash } from "../src/auth";
import { lidarToScreen, lidarViewport, nearestLidarPoint } from "../../web/src/lidar-view";

const profile = { schemaVersion: 1 as const, profileSha256: "a".repeat(64),
  timingHypothesis: "header_is_scan_start" as const, configurationSha256: { "lidar.json": "b".repeat(64) } };
function previewFixture(): SavedSlamPointPreview {
  return { mapId: `slam-${"a".repeat(32)}`, inputMode: "recorded", coordinateUnits: "native-raw",
    receiptSha256: "b".repeat(64), pointsSha256: "c".repeat(64), totalPoints: 6, sampled: false,
    points: [0, 0, 2, 2, 0, 2, 0, 2, 2, 0, 0, 4, 2, 2, 2, 1, 1, 3],
    seedReferences: { source: "glim-final-lidar-trajectory", sourceFrame: "lidar_chest_front",
      coordinateUnits: "native-raw", profileSha256: profile.profileSha256, trajectorySha256: "d".repeat(64), frames: 2,
      first: { frameIndex: 1, mapFromSensor: [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]] },
      last: { frameIndex: 2, mapFromSensor: [[1, 0, 0, 1], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]] } } };
}
function surveyFor(preview: SavedSlamPointPreview, indices = [0, 1, 2, 3, 4, 5]): CalibrationSurvey {
  return { binding: previewCalibrationBinding(preview), method: "laser", measuredAt: "2026-11-03T09:00:00.000Z",
    operator: "Synthetic test operator", originDescription: "Synthetic origin", xAxisDescription: "Synthetic +X",
    zAxisDescription: "Synthetic +Z, not gravity evidence",
    landmarks: indices.map((pointIndex, i) => {
      const [x, y, z] = preview.points.slice(pointIndex * 3, pointIndex * 3 + 3);
      return { pointIndex, label: `P${i + 1}`, role: i < 4 ? "fit" : "check",
        // Proper 3D rotation: raw X -> target Y, raw Y -> target Z, raw Z -> target X.
        measured: [z * .5 + 2, x * .5 - 3, y * .5 + 1] };
    }) };
}

describe("measured survey similarity candidate", () => {
  it("recovers scale, full rotation and metric translation without multiplying translation by scale", () => {
    const result = computeCalibration(surveyFor(previewFixture()), previewFixture());
    expect(result.metresPerMapUnit).toBeCloseTo(.5, 12);
    const expected = [[0, 0, 1, 2], [1, 0, 0, -3], [0, 1, 0, 1], [0, 0, 0, 1]];
    result.navigationFromRaw.forEach((row, i) => row.forEach((v, j) => expect(v).toBeCloseTo(expected[i][j], 12)));
    expect(result.fit.rmseMetres).toBeLessThan(1e-12);
    expect(result.check.maxMetres).toBeLessThan(1e-12);
    expect(result).toMatchObject({ reviewable: true, physicalScaleValidated: false, groundSupportValidated: false, navigationReady: false });
    expect(calibrationCandidateSchema.parse(result)).toEqual(result);
    expect(computeCalibration(surveyFor(previewFixture()), previewFixture()).candidateSha256).toBe(result.candidateSha256);
  });
  it("supports noncollinear planar fit points but never promotes them to gravity or ground evidence", () => {
    const survey = surveyFor(previewFixture()); survey.landmarks[3].role = "check";
    const candidate = computeCalibration(survey, previewFixture());
    expect(candidate.fit.count).toBe(3); expect(candidate.check.count).toBe(3);
    expect(candidate.navigationFromRaw[2][1]).toBeCloseTo(1, 10);
    expect(candidate.groundSupportValidated).toBe(false);
  });
  it("keeps independent checks out of fitting and rejects their residuals", () => {
    const survey = surveyFor(previewFixture()); survey.landmarks[5].measured[1] += .2;
    const result = computeCalibration(survey, previewFixture());
    expect(result.fit.maxMetres).toBeLessThan(1e-12); expect(result.check.maxMetres).toBeCloseTo(.2);
    expect(result.metresPerMapUnit).toBeCloseTo(.5); expect(result.issues).toEqual(["check_residual"]);
    expect(result.reviewable).toBe(false);
  });
  it("rejects reflected 3D data instead of returning an improper rotation", () => {
    const survey = surveyFor(previewFixture()); survey.landmarks.forEach((point) => { point.measured[0] *= -1; });
    const result = computeCalibration(survey, previewFixture());
    expect(result.issues).toContain("fit_residual"); expect(result.reviewable).toBe(false);
  });
  it.each(["collinear", "same-raw", "same-measured", "small-span"])("rejects unobservable or nonindependent data: %s", (kind) => {
    const preview = previewFixture(), survey = surveyFor(preview);
    if (kind === "collinear") for (let i = 0; i < 4; i++) preview.points.splice(i * 3, 3, i, 0, 2);
    if (kind === "same-raw") preview.points.splice(15, 3, ...preview.points.slice(0, 3));
    if (kind === "same-measured") survey.landmarks[5].measured = [...survey.landmarks[0].measured];
    if (kind === "small-span") survey.landmarks.forEach((p) => { p.measured = p.measured.map((v) => v * .01) as [number, number, number]; });
    expect(() => computeCalibration(survey, preview)).toThrow();
  });
  it.each(["receiptSha256", "pointsSha256", "profileSha256", "mapId"] as const)("rejects stale %s", (field) => {
    const preview = previewFixture(), survey = surveyFor(preview); survey.binding[field] = "e".repeat(field === "mapId" ? 32 : 64);
    expect(() => computeCalibration(survey, preview)).toThrow();
  });
  it("has bounded typed measurements, unique roles/points/labels and no caller-supplied raw coordinates", () => {
    const survey = surveyFor(previewFixture());
    for (const mutation of [
      { rawPoints: [1, 2, 3] }, { landmarks: survey.landmarks.slice(0, 4) },
      { landmarks: [...survey.landmarks, survey.landmarks[0]] },
      { landmarks: survey.landmarks.map((p) => ({ ...p, role: "fit" })) },
      { landmarks: survey.landmarks.map((p) => ({ ...p, measured: [NaN, 0, 0] })) },
    ]) expect(calibrationSurveySchema.safeParse({ ...survey, ...mutation }).success).toBe(false);
    survey.landmarks[0].pointIndex = 19_999;
    expect(() => computeCalibration(survey, previewFixture())).toThrow("测量点不在当前点云预览中");
  });
  it("allows diagnostic synthetic calculation but not field candidate review", () => {
    const preview = { ...previewFixture(), inputMode: "synthetic" as const };
    expect(computeCalibration(surveyFor(preview), preview)).toMatchObject({ reviewable: false, issues: ["synthetic_input"] });
  });
  it("does not guess profile provenance for a historical map", () => {
    const preview = previewFixture(); delete preview.seedReferences;
    expect(() => previewCalibrationBinding(preview)).toThrow("地图缺少传感器配置来源");
  });
});

describe("saved-map survey API and audit persistence", () => {
  let root: string, maps: string, reviews: string, catalog: SavedSlamMapCatalog, service: CalibrationService;
  let app: FastifyInstance | undefined;
  beforeEach(async () => {
    root = await mkdtemp(path.join(tmpdir(), "lingxi-calibration-")); maps = path.join(root, "maps");
    reviews = path.join(root, "state", "calibration-reviews");
    await mkdir(maps); catalog = new SavedSlamMapCatalog(maps); service = new CalibrationService(catalog, reviews);
  });
  afterEach(async () => { await app?.close(); app = undefined; await rm(root, { recursive: true, force: true }); });
  async function fixture(inputMode: "recorded" | "synthetic" = "recorded") {
    const saved = await savedSlamFixture(maps, { sensorProvenance: profile, inputMode });
    const preview = await catalog.preview(saved.id);
    return { saved, preview, survey: surveyFor(preview, [0, 240, 480, 720, 960, 1200]) };
  }
  it("recomputes before review, persists across restart, deduplicates retries and never modifies map artifacts", async () => {
    const { saved, survey } = await fixture(), before = await readFile(saved.ready);
    const candidate = await service.candidate(survey);
    const review = await service.review(survey, candidate.candidateSha256, "Synthetic reviewer");
    expect(await service.review(survey, candidate.candidateSha256, "Synthetic retry")).toEqual(review);
    const restarted = new CalibrationService(new SavedSlamMapCatalog(maps), reviews);
    expect((await restarted.history(saved.id)).reviews).toEqual([review]);
    expect(await readFile(saved.ready)).toEqual(before);
    expect((await catalog.list())[0]).toMatchObject({ navigationReady: false, localizationReady: false, geometricAccuracyValidated: false });
  });
  it("rejects tampered candidate hashes, failed checks and synthetic inputs", async () => {
    const { survey } = await fixture();
    await expect(service.review(survey, "f".repeat(64), "Reviewer")).rejects.toMatchObject({ statusCode: 409 });
    survey.landmarks[5].measured[0] += 1;
    const failed = await service.candidate(survey);
    await expect(service.review(survey, failed.candidateSha256, "Reviewer")).rejects.toMatchObject({ statusCode: 409 });
  });
  it("rejects a changed map and excludes stale saved reviews", async () => {
    const { saved, survey } = await fixture(), candidate = await service.candidate(survey);
    await service.review(survey, candidate.candidateSha256, "Reviewer");
    saved.receipt.name = "Changed identity"; await writeFile(saved.ready, JSON.stringify(saved.receipt));
    await expect(service.review(survey, candidate.candidateSha256, "Reviewer")).rejects.toMatchObject({ statusCode: 409 });
    expect((await service.history(saved.id)).reviews).toEqual([]);
  });
  it("fails closed on altered saved review content", async () => {
    const { saved, survey } = await fixture(), candidate = await service.candidate(survey);
    const review = await service.review(survey, candidate.candidateSha256, "Reviewer");
    review.candidate.metresPerMapUnit = 2;
    await writeFile(path.join(reviews, saved.id, `${review.id}.json`), JSON.stringify(review));
    await expect(service.history(saved.id)).rejects.toMatchObject({ statusCode: 409 });
  });
  it("does not follow a linked audit directory", async () => {
    const { saved } = await fixture(); await mkdir(path.join(root, "outside")); await mkdir(path.dirname(reviews), { recursive: true });
    await symlink(path.join(root, "outside"), reviews, process.platform === "win32" ? "junction" : "dir");
    await expect(service.history(saved.id)).rejects.toMatchObject({ statusCode: 409 });
    expect(await readdir(path.join(root, "outside"))).toEqual([]);
  });
  it("requires pairing and explicit review, and returns audit records without filesystem paths or readiness promotion", async () => {
    const { saved, survey } = await fixture();
    app = await buildServer({ demoMode: true, stateDirectory: path.join(root, "state"), savedSlamDirectory: maps,
      staticDirectory: path.join(root, "absent"), pairingCodeHash: pairingCodeHash("synthetic-calibration-test") });
    const route = `/api/v1/slam/maps/${saved.id}/calibration`;
    for (const [method, url, payload] of [["GET", route, undefined], ["POST", `${route}/candidate`, survey],
      ["POST", `${route}/review`, {}]] as const) {
      expect((await app.inject({ method, url, payload })).statusCode).toBe(401);
    }
    const token = (await app.inject({ method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: "synthetic-calibration-test" } })).json().token;
    const headers = { authorization: `Bearer ${token}` };
    const response = await app.inject({ method: "POST", url: `${route}/candidate`, headers, payload: survey });
    expect(response.statusCode).toBe(200); const candidate = response.json();
    expect(candidate.reviewable).toBe(true);
    const payload = { survey, candidateSha256: candidate.candidateSha256, reviewer: "Synthetic reviewer", acknowledgeCandidateOnly: true };
    expect((await app.inject({ method: "POST", url: `${route}/review`, headers,
      payload: { ...payload, acknowledgeCandidateOnly: false } })).statusCode).toBe(400);
    expect((await app.inject({ method: "POST", url: `${route}/review`, headers, payload })).statusCode).toBe(200);
    const history = await app.inject({ url: route, headers });
    expect(history.json().reviews).toHaveLength(1); expect(history.body).not.toContain(root);
    expect(history.json().reviews[0].candidate.navigationReady).toBe(false);
    expect((await app.inject({ method: "POST", url: `/api/v1/maps/${saved.id}/activate`, headers, payload: {} })).statusCode).toBe(404);
    const state = (await app.inject({ url: "/api/v1/state", headers })).json();
    expect(state.maps.some((map: { id: string }) => map.id === saved.id)).toBe(false);
    expect((await app.inject({ method: "POST", url: `${route}/candidate`, headers,
      payload: { ...survey, binding: { ...survey.binding, mapId: `slam-${"e".repeat(32)}` } } })).statusCode).toBe(400);
  });
});

describe("survey point picking", () => {
  const view = lidarViewport({ minX: -5, maxX: 5, minY: -5, maxY: 5 }, 600, 400, 1.3, { x: 10, y: -5 });
  const points = [0, 0, .05, 0, 0, 1, 2, 1, .7];
  it("returns original sampled indices and respects the displayed Z range", () => {
    const mouse = lidarToScreen(view, { x: 0, y: 0 });
    expect(nearestLidarPoint(points, view, mouse, { min: .1, max: 1.4 })).toBe(1);
    expect(nearestLidarPoint(points, view, mouse)).toBe(0);
    expect(nearestLidarPoint(points, view, lidarToScreen(view, { x: 2, y: 1 }), { min: .1, max: 1.4 })).toBe(2);
  });
  it("does not select empty space, off-canvas gestures or hidden points", () => {
    expect(nearestLidarPoint(points, view, { x: 0, y: 0 })).toBeUndefined();
    expect(nearestLidarPoint(points, view, { x: -1, y: 100 })).toBeUndefined();
    expect(nearestLidarPoint(points, view, lidarToScreen(view, { x: 0, y: 0 }), { min: 1.1, max: 1.4 })).toBeUndefined();
  });
});
