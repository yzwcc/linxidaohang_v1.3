import { createHash, randomUUID } from "node:crypto";
import { lstat, mkdir, open, readFile, readdir, unlink } from "node:fs/promises";
import path from "node:path";
import { Matrix, SingularValueDecomposition, determinant } from "ml-matrix";
import { calibrationLimits as limits, calibrationSurveySchema, calibrationReviewSchema,
  calibrationCandidateSchema, sameCalibrationBinding,
  type CalibrationSurvey, type CalibrationCandidate, type CalibrationBinding, type CalibrationHistory,
} from "../../shared/calibration";
import type { SavedSlamPointPreview } from "../../shared/contracts";
import type { RigidMatrix } from "../../shared/localization";
import type { SavedSlamMapCatalog } from "./slam-map-catalog";

const fail = (statusCode: number, message: string) => Object.assign(new Error(message), { statusCode });
function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
      .map(([key, entry]) => `${JSON.stringify(key)}:${stableJson(entry)}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}
const hash = (value: unknown) => createHash("sha256").update(stableJson(value)).digest("hex");
export function previewCalibrationBinding(preview: SavedSlamPointPreview): CalibrationBinding {
  if (!preview.seedReferences) throw fail(409, "地图缺少传感器配置来源，不能建立标定测量");
  return { mapId: preview.mapId, receiptSha256: preview.receiptSha256, pointsSha256: preview.pointsSha256,
    profileSha256: preview.seedReferences.profileSha256 };
}
const distance = (a: number[], b: number[]) => Math.hypot(...a.map((v, i) => v - b[i]));
const centroid = (points: number[][]) => [0, 1, 2].map((axis) => points.reduce((sum, p) => sum + p[axis], 0) / points.length);
const span = (points: number[][]) => Math.max(...points.flatMap((a, i) => points.slice(i + 1).map((b) => distance(a, b))));

export function computeCalibration(input: CalibrationSurvey, preview: SavedSlamPointPreview): CalibrationCandidate {
  const survey = calibrationSurveySchema.parse(input);
  if (!sameCalibrationBinding(survey.binding, previewCalibrationBinding(preview))) {
    throw fail(409, "地图或传感器配置已变化，请重新读取点云并测量");
  }
  const rawPoints = survey.landmarks.map(({ pointIndex }) => {
    const point = preview.points.slice(pointIndex * 3, pointIndex * 3 + 3);
    if (point.length !== 3 || !point.every(Number.isFinite)) throw fail(400, "测量点不在当前点云预览中");
    return point as [number, number, number];
  });
  const fitIndices = survey.landmarks.flatMap((p, i) => p.role === "fit" ? [i] : []);
  const checkIndices = survey.landmarks.flatMap((p, i) => p.role === "check" ? [i] : []);
  const source = fitIndices.map((i) => rawPoints[i]), target = fitIndices.map((i) => survey.landmarks[i].measured);
  const measured = survey.landmarks.map((p) => p.measured);
  if (span(target) < limits.minSpanMetres || span(checkIndices.map((i) => measured[i])) < limits.minSpanMetres ||
      measured.some((a, i) => measured.slice(i + 1).some((b) => distance(a, b) < limits.minIndependentSeparationMetres)) ||
      rawPoints.some((a, i) => rawPoints.slice(i + 1).some((b) => distance(a, b) < 1e-8))) {
    throw fail(400, "测量点跨度不足或检查点不独立，请选择分散的真实地标");
  }
  const meanX = centroid(source), meanY = centroid(target);
  const x = new Matrix(source.map((p) => p.map((v, i) => v - meanX[i])));
  const y = new Matrix(target.map((p) => p.map((v, i) => v - meanY[i])));
  for (const coordinates of [x, y]) {
    const singular = new SingularValueDecomposition(coordinates, { autoTranspose: true }).diagonal;
    if (!(singular[0] > 1e-8) || singular[1] / singular[0] < limits.minShapeRatio) {
      throw fail(400, "拟合点近似共线，无法确定三维方向，请补充横向地标");
    }
  }
  // Umeyama similarity fit: target = R * (scale * raw) + t. Holdouts never enter the fit.
  const svd = new SingularValueDecomposition(y.transpose().mmul(x).div(source.length));
  const correction = Matrix.eye(3);
  correction.set(2, 2, determinant(svd.leftSingularVectors.mmul(svd.rightSingularVectors.transpose())) < 0 ? -1 : 1);
  const rotation = svd.leftSingularVectors.mmul(correction).mmul(svd.rightSingularVectors.transpose());
  const variance = x.to1DArray().reduce((sum, v) => sum + v * v, 0) / source.length;
  const scale = svd.diagonal.reduce((sum, v, i) => sum + v * correction.get(i, i), 0) / variance;
  if (!Number.isFinite(scale) || scale <= 0) throw fail(400, "测量不能形成有效的统一尺度");
  const offset = meanY.map((v, i) => v - scale * meanX.reduce((sum, value, j) => sum + rotation.get(i, j) * value, 0));
  const transform = [...rotation.to2DArray().map((row, i) => [...row, offset[i]]), [0, 0, 0, 1]] as RigidMatrix;
  const errors = rawPoints.map((point, index) => distance(point.map((_, i) => offset[i] +
    scale * point.reduce((sum, v, j) => sum + rotation.get(i, j) * v, 0)), measured[index]));
  const residuals = (indices: number[]) => ({ count: indices.length,
    rmseMetres: Math.sqrt(indices.reduce((sum, i) => sum + errors[i] ** 2, 0) / indices.length),
    maxMetres: Math.max(...indices.map((i) => errors[i])) });
  const fit = residuals(fitIndices), check = residuals(checkIndices);
  const issues: CalibrationCandidate["issues"] = [];
  if (fit.rmseMetres > limits.maxRmseMetres || fit.maxMetres > limits.maxErrorMetres) issues.push("fit_residual");
  if (check.rmseMetres > limits.maxRmseMetres || check.maxMetres > limits.maxErrorMetres) issues.push("check_residual");
  if (scale < limits.minScale || scale > limits.maxScale || offset.some((v) => Math.abs(v) > 500)) issues.push("scale_out_of_range");
  if (preview.inputMode === "synthetic") issues.push("synthetic_input");
  const content = { algorithm: "umeyama-survey-v1" as const, survey, inputMode: preview.inputMode, rawPoints,
    metresPerMapUnit: scale, navigationFromRaw: transform, fit, check, errors, issues, reviewable: issues.length === 0,
    physicalScaleValidated: false as const, groundSupportValidated: false as const, navigationReady: false as const };
  return calibrationCandidateSchema.parse({ ...content, candidateSha256: hash(content) });
}

export class CalibrationService {
  private queue: Promise<unknown> = Promise.resolve();
  constructor(private readonly catalog: SavedSlamMapCatalog, private readonly directory: string) {}

  async candidate(survey: CalibrationSurvey) {
    return computeCalibration(survey, await this.catalog.preview(survey.binding.mapId));
  }

  async history(mapId: string): Promise<CalibrationHistory> {
    const binding = previewCalibrationBinding(await this.catalog.preview(mapId));
    const directory = await this.reviewDirectory(mapId);
    const entries = await readdir(directory);
    if (entries.length > 64) throw fail(409, "标定审核记录已达上限，请先归档");
    const reviews = [];
    for (const name of entries.sort()) {
      if (!/^[a-f0-9-]{36}\.json$/.test(name)) throw fail(409, "标定审核目录包含异常文件");
      const file = path.join(directory, name), info = await lstat(file);
      if (!info.isFile() || info.isSymbolicLink() || info.size > 128_000) throw fail(409, "标定审核文件无效");
      const review = calibrationReviewSchema.parse(JSON.parse(await readFile(file, "utf8")));
      const { candidateSha256, ...content } = review.candidate;
      if (review.id + ".json" !== name || hash(content) !== candidateSha256 || !review.candidate.reviewable) {
        throw fail(409, "标定审核记录校验未通过");
      }
      if (sameCalibrationBinding(review.candidate.survey.binding, binding)) reviews.push(review);
    }
    return { binding, reviews: reviews.sort((a, b) => b.reviewedAt.localeCompare(a.reviewedAt)) };
  }

  review(survey: CalibrationSurvey, candidateSha256: string, reviewer: string) {
    const operation = this.queue.then(async () => {
      const candidate = await this.candidate(survey);
      if (candidate.candidateSha256 !== candidateSha256) throw fail(409, "候选已变化，请重新计算并审核");
      if (!candidate.reviewable) throw fail(409, "候选尚未通过测量检查，不能保存审核");
      const history = await this.history(survey.binding.mapId);
      if (!sameCalibrationBinding(history.binding, candidate.survey.binding)) throw fail(409, "地图已变化，请重新测量");
      const existing = history.reviews.find((item) => item.candidate.candidateSha256 === candidateSha256);
      if (existing) return existing;
      const directory = await this.reviewDirectory(survey.binding.mapId);
      if ((await readdir(directory)).length >= 64) throw fail(409, "标定审核记录已达上限，请先归档");
      const review = calibrationReviewSchema.parse({ id: randomUUID(), reviewedAt: new Date().toISOString(), reviewer,
        acknowledgeCandidateOnly: true, candidate });
      const filePath = path.join(directory, `${review.id}.json`);
      const file = await open(filePath, "wx", 0o600);
      try { await file.writeFile(JSON.stringify(review, null, 2)); await file.sync(); }
      catch (error) { await unlink(filePath).catch(() => undefined); throw error; }
      finally { await file.close(); }
      return review;
    });
    this.queue = operation.catch(() => undefined);
    return operation;
  }

  private async reviewDirectory(mapId: string) {
    if (!/^slam-[a-f0-9]{32}$/.test(mapId)) throw fail(404, "SLAM 地图不存在");
    for (const directory of [this.directory, path.join(this.directory, mapId)]) {
      await mkdir(directory, { recursive: true, mode: 0o700 });
      const info = await lstat(directory);
      if (!info.isDirectory() || info.isSymbolicLink()) throw fail(409, "标定审核目录不能是链接");
    }
    return path.join(this.directory, mapId);
  }
}
