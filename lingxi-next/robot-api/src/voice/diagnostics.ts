import type { VoiceDiagnostics, VoiceReply } from "../../../shared/contracts";

export type VoiceSample = NonNullable<VoiceDiagnostics["latest"]>;

function percentile95(values: number[]): number | undefined {
  const ordered = values.filter(Number.isFinite).sort((left, right) => left - right);
  return ordered.length ? ordered[Math.max(0, Math.ceil(ordered.length * 0.95) - 1)] : undefined;
}

export function measureVadToFirstAudio(
  vadEndedAt: number | undefined,
  receivedAt: number,
  firstAudioMs: number,
): number | undefined {
  if (vadEndedAt === undefined || !Number.isSafeInteger(vadEndedAt) ||
      !Number.isFinite(receivedAt) || !Number.isFinite(firstAudioMs) || firstAudioMs < 0) return undefined;
  const ingressDelay = receivedAt - vadEndedAt;
  if (ingressDelay < 0 || ingressDelay > 30_000) return undefined;
  return Math.round(ingressDelay + firstAudioMs);
}

export function summarizeVoiceSamples(samples: VoiceSample[]): VoiceDiagnostics {
  const audible = samples.flatMap((sample) =>
    sample.firstAudioMs === undefined ? [] : [sample.firstAudioMs],
  );
  const vadMeasured = samples.flatMap((sample) =>
    sample.vadToFirstAudioMs === undefined ? [] : [sample.vadToFirstAudioMs],
  );
  return {
    sampleCount: samples.length,
    audibleSampleCount: audible.length,
    recognitionToFirstAudioP95Ms: percentile95(audible),
    vadMeasuredSampleCount: vadMeasured.length,
    vadToFirstAudioP95Ms: percentile95(vadMeasured),
    latest: samples.at(-1),
  };
}

export type { VoiceReply };
