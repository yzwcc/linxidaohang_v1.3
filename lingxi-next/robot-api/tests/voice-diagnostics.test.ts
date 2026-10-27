import { describe, expect, it } from "vitest";
import { measureVadToFirstAudio, summarizeVoiceSamples, type VoiceSample } from "../src/voice/diagnostics";

const sample = (index: number, firstAudioMs: number, vadToFirstAudioMs?: number): VoiceSample => ({
  at: new Date(index).toISOString(),
  category: "question",
  accepted: true,
  speechDelivered: true,
  latencyMs: firstAudioMs + 20,
  firstAudioMs,
  vadToFirstAudioMs,
});

describe("VAD-to-playback diagnostics", () => {
  it("measures VAD EOS to the correlated SDK playing signal", () => {
    expect(measureVadToFirstAudio(10_000, 10_240, 310)).toBe(550);
  });

  it("omits missing, future, stale, or invalid VAD evidence", () => {
    expect(measureVadToFirstAudio(undefined, 10_000, 100)).toBeUndefined();
    expect(measureVadToFirstAudio(10_001, 10_000, 100)).toBeUndefined();
    expect(measureVadToFirstAudio(1, 40_002, 100)).toBeUndefined();
    expect(measureVadToFirstAudio(10_000, 10_001, Number.NaN)).toBeUndefined();
    expect(measureVadToFirstAudio(10_000, 10_001, -1)).toBeUndefined();
  });

  it("reports a separate P95 and sample count only for VAD-correlated playback", () => {
    const samples = [sample(1, 100, 500), sample(2, 200), sample(3, 300, 2_100)];
    expect(summarizeVoiceSamples(samples)).toMatchObject({
      sampleCount: 3,
      audibleSampleCount: 3,
      recognitionToFirstAudioP95Ms: 300,
      vadMeasuredSampleCount: 2,
      vadToFirstAudioP95Ms: 2_100,
      latest: samples[2],
    });
  });
});
