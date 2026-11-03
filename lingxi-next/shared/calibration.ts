import { z } from "zod";
import { rigidMatrixSchema } from "./localization";

const sha256 = z.string().regex(/^[a-f0-9]{64}$/);
export const calibrationBindingSchema = z.object({
  mapId: z.string().regex(/^slam-[a-f0-9]{32}$/), receiptSha256: sha256,
  pointsSha256: sha256, profileSha256: sha256,
}).strict();
const description = z.string().trim().min(1).max(200);
export const surveyLandmarkSchema = z.object({
  pointIndex: z.number().int().nonnegative().max(19_999),
  label: z.string().trim().min(1).max(40), role: z.enum(["fit", "check"]),
  measured: z.tuple([z.number().finite().min(-500).max(500),
    z.number().finite().min(-500).max(500), z.number().finite().min(-500).max(500)]),
}).strict();
export const calibrationSurveySchema = z.object({
  binding: calibrationBindingSchema,
  method: z.enum(["tape", "laser", "survey_instrument"]),
  measuredAt: z.string().datetime(), operator: description,
  originDescription: description, xAxisDescription: description, zAxisDescription: description,
  landmarks: z.array(surveyLandmarkSchema).min(5).max(32),
}).strict().superRefine((survey, context) => {
  if (survey.landmarks.filter((point) => point.role === "fit").length < 3 ||
      survey.landmarks.filter((point) => point.role === "check").length < 2 ||
      new Set(survey.landmarks.map((point) => point.pointIndex)).size !== survey.landmarks.length ||
      new Set(survey.landmarks.map((point) => point.label)).size !== survey.landmarks.length) {
    context.addIssue({ code: "custom", message: "至少需要三个拟合点和两个独立检查点，点和名称不能重复" });
  }
});
export const calibrationLimits = Object.freeze({
  minSpanMetres: 0.5, minShapeRatio: 0.02, minIndependentSeparationMetres: 0.1,
  maxRmseMetres: 0.03, maxErrorMetres: 0.05, minScale: 0.01, maxScale: 100,
});
const residualsSchema = z.object({ rmseMetres: z.number().nonnegative().finite(),
  maxMetres: z.number().nonnegative().finite(), count: z.number().int().positive() }).strict();
export const calibrationCandidateSchema = z.object({
  algorithm: z.literal("umeyama-survey-v1"), candidateSha256: sha256,
  survey: calibrationSurveySchema, inputMode: z.enum(["live", "recorded", "synthetic"]),
  rawPoints: z.array(z.tuple([z.number().finite(), z.number().finite(), z.number().finite()])).min(5).max(32),
  metresPerMapUnit: z.number().positive().finite(), navigationFromRaw: rigidMatrixSchema,
  fit: residualsSchema, check: residualsSchema,
  errors: z.array(z.number().finite().nonnegative()).min(5).max(32),
  issues: z.array(z.enum(["fit_residual", "check_residual", "scale_out_of_range", "synthetic_input"])),
  reviewable: z.boolean(), physicalScaleValidated: z.literal(false),
  groundSupportValidated: z.literal(false), navigationReady: z.literal(false),
}).strict();
export const calibrationReviewRequestSchema = z.object({
  survey: calibrationSurveySchema, candidateSha256: sha256,
  reviewer: description, acknowledgeCandidateOnly: z.literal(true),
}).strict();
export const calibrationReviewSchema = z.object({
  id: z.string().uuid(), reviewedAt: z.string().datetime(), reviewer: description,
  acknowledgeCandidateOnly: z.literal(true), candidate: calibrationCandidateSchema,
}).strict();
export const calibrationHistorySchema = z.object({
  binding: calibrationBindingSchema, reviews: z.array(calibrationReviewSchema).max(64),
}).strict();
export type CalibrationBinding = z.infer<typeof calibrationBindingSchema>;
export type CalibrationSurvey = z.infer<typeof calibrationSurveySchema>;
export type SurveyLandmark = z.infer<typeof surveyLandmarkSchema>;
export type CalibrationCandidate = z.infer<typeof calibrationCandidateSchema>;
export type CalibrationReview = z.infer<typeof calibrationReviewSchema>;
export type CalibrationHistory = z.infer<typeof calibrationHistorySchema>;

export function sameCalibrationBinding(a: CalibrationBinding, b: CalibrationBinding) {
  return a.mapId === b.mapId && a.receiptSha256 === b.receiptSha256 &&
    a.pointsSha256 === b.pointsSha256 && a.profileSha256 === b.profileSha256;
}
