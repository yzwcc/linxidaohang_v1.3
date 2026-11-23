"""Score reviewed X2 voice trials on a PCM sample clock, without robot access."""

import argparse
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import wave


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class Recording:
    rate: int
    frames: int
    sha256: str


def require(condition, reason):
    if not condition:
        raise EvidenceError(reason)


def shape(value, keys, reason):
    require(isinstance(value, dict) and value.keys() == set(keys.split()), reason)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def audit_recording(root, record):
    shape(record, "file sha256", "invalid_recording_entry")
    require(isinstance(record["file"], str) and valid_sha(record["sha256"]), "invalid_recording_reference")
    relative = Path(record["file"])
    require(not relative.anchor and ".." not in relative.parts and relative.suffix.lower() == ".wav",
            "invalid_recording_path")
    audio = (root / relative).resolve(strict=True)
    require(audio.is_relative_to(root) and audio.is_file(), "recording_outside_evidence_directory")

    def fingerprint(stream):
        info = os.fstat(stream.fileno())
        return info.st_size, info.st_mtime_ns, info.st_ctime_ns

    with audio.open("rb") as stream:
        before = fingerprint(stream)
        try:
            with wave.open(stream, "rb") as pcm:
                frames, rate = pcm.getnframes(), pcm.getframerate()
                channels = pcm.getnchannels()
                require(pcm.getcomptype() == "NONE" and pcm.getsampwidth() == 2 and channels in (1, 2)
                        and 8000 <= rate <= 96000 and frames > 0, "unsupported_pcm_recording")
                # wave trusts the length declared by the data chunk. Verify the
                # declared last frame is actually readable before counting time.
                pcm.setpos(frames - 1)
                require(len(pcm.readframes(1)) == channels * 2, "truncated_pcm_recording")
        except (wave.Error, EOFError) as error:
            raise EvidenceError("unreadable_pcm_recording") from error
        stream.seek(0)
        sha = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
        require(before == fingerprint(stream), "recording_changed_during_review")
        digest = sha.hexdigest()
        require(digest == record["sha256"], "recording_digest_mismatch")
        return Recording(rate, frames, digest)


def location(value, recordings):
    require(isinstance(value, str) and value in recordings, "unknown_trial_recording")
    return recordings[value]


def frame(value, limit, *, end=False):
    require(type(value) is int and 0 <= value < limit + int(end), "invalid_sample_frame")
    return value


def score_trials(evidence, recordings):
    """Compute gates from media-checked frames and explicitly reviewed annotations."""
    shape(evidence, "schemaVersion captureKind target recordings wakeTrials negativeTrials noise", "invalid_evidence_schema")
    require(type(evidence["schemaVersion"]) is int and evidence["schemaVersion"] == 1
            and evidence["captureKind"] in ("field", "synthetic"), "unsupported_evidence_schema")
    target = evidence["target"]
    shape(target, "deviceRef firmware aimdk release profileSha256", "invalid_target_identity")
    require(all(isinstance(target[field], str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", target[field])
                for field in ("deviceRef", "firmware", "aimdk", "release"))
            and valid_sha(target["profileSha256"]), "invalid_target_identity")
    require(evidence["captureKind"] != "field" or target["firmware"] == "release-lx2501_3_t2d5-soc1-v1.1.5",
            "field_firmware_not_x2_v1_1_5")
    require(len({item.sha256 for item in recordings.values()}) == len(recordings), "reused_recording_bytes")

    noise = evidence["noise"]
    shape(noise, "recording monitoringComplete falseWakeFrames", "invalid_noise_annotation")
    noise_audio = location(noise["recording"], recordings)
    false_wakes = noise["falseWakeFrames"]
    require(isinstance(false_wakes, list) and type(noise["monitoringComplete"]) is bool, "invalid_noise_annotation")
    for item in false_wakes:
        frame(item, noise_audio.frames)
    require(false_wakes == sorted(set(false_wakes)), "duplicate_or_unordered_false_wakes")

    windows = {}
    samples = []
    successful_wakes = 0
    wrong_executions = 0
    negative_cases = set()

    def trial_window(trial):
        key = trial["recording"]
        audio = location(key, recordings)
        require(key != noise["recording"], "trial_in_noise_recording")
        start = frame(trial["start"], audio.frames)
        stop = frame(trial["end"], audio.frames, end=True)
        require(stop > start, "empty_trial_window")
        windows.setdefault(key, []).append((start, stop))
        return audio.rate, start, stop

    wake_trials = evidence["wakeTrials"]
    require(isinstance(wake_trials, list) and len(wake_trials) == 100, "need_exactly_100_wake_trials")
    for trial in wake_trials:
        shape(trial, "recording start end speechEnd wakeConfirmed firstAudible", "invalid_wake_trial")
        rate, start, stop = trial_window(trial)
        speech_end = frame(trial["speechEnd"], stop)
        require(start < speech_end and stop - speech_end >= rate * 5, "need_five_seconds_after_speech")
        woke = trial["wakeConfirmed"]
        require(type(woke) is bool, "unreviewed_wake_trial")
        audible = trial["firstAudible"]
        if audible is not None:
            frame(audible, stop)
            require(woke and speech_end <= audible, "response_not_owned_by_current_wake_trial")
            samples.append(Fraction((audible - speech_end) * 1000, rate))
        successful_wakes += int(woke)

    negative_trials = evidence["negativeTrials"]
    require(isinstance(negative_trials, list), "invalid_negative_trials")
    for trial in negative_trials:
        shape(trial, "recording start end case executed", "invalid_negative_trial")
        rate, start, stop = trial_window(trial)
        require(trial["case"] in ("ambiguous", "unauthorized") and type(trial["executed"]) is bool
                and stop - start >= rate * 5, "unreviewed_negative_trial")
        negative_cases.add(trial["case"])
        wrong_executions += int(trial["executed"])
    require(negative_cases == {"ambiguous", "unauthorized"}, "missing_negative_case_coverage")

    for intervals in windows.values():
        ordered = sorted(intervals)
        require(all(before[1] <= after[0] for before, after in zip(ordered, ordered[1:])), "overlapping_trial_windows")
    require(set(windows) | {noise["recording"]} == recordings.keys(), "unused_recording")

    # Nearest rank on all 100 trials. Missing audible responses rank last,
    # rather than silently shortening the denominator to successful replies.
    p95 = sorted(samples)[94] if len(samples) >= 95 else None
    noise_s = Fraction(noise_audio.frames, noise_audio.rate)
    gates = {
        "wake95of100": successful_wakes >= 95,
        "firstAudibleP95Within2s": p95 is not None and p95 <= 2000,
        "noise8hAtMostOneFalseWake": noise["monitoringComplete"] and noise_s >= 8 * 3600 and len(false_wakes) <= 1,
        "zeroAmbiguousOrUnauthorizedExecution": wrong_executions == 0,
    }
    return {
        "schemaVersion": 1,
        "captureKind": evidence["captureKind"],
        "wakeTrials": 100,
        "wakeConfirmed": successful_wakes,
        "firstAudibleMeasured": len(samples),
        "firstAudibleMissing": 100 - len(samples),
        "firstAudibleP95Ms": None if p95 is None else float(p95),
        "noiseRecordedSeconds": float(noise_s),
        "noiseMonitoringComplete": noise["monitoringComplete"],
        "falseWakes": len(false_wakes),
        "negativeTrials": len(negative_trials),
        "wrongExecutions": wrong_executions,
        "gates": gates,
        "allNumericGatesMet": all(gates.values()),
        "requiresIndependentFieldReview": True,
        "productionAccepted": False,
    }


def score_manifest(path):
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    require(len(raw) <= 1024 * 1024, "evidence_manifest_too_large")
    evidence = json.loads(raw, object_pairs_hook=unique_keys)
    shape(evidence, "schemaVersion captureKind target recordings wakeTrials negativeTrials noise", "invalid_evidence_schema")
    records = evidence["recordings"]
    require(isinstance(records, dict) and 2 <= len(records) <= 256, "invalid_recordings")
    root = path.resolve().parent
    recordings = {key: audit_recording(root, entry) for key, entry in records.items()}
    report = score_trials(evidence, recordings)
    report["manifestSha256"] = hashlib.sha256(raw).hexdigest()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        result = score_manifest(args.manifest)
    except EvidenceError as error:
        print(json.dumps({"error": str(error)}))
        return 2
    except (OSError, ValueError, RecursionError):
        print(json.dumps({"error": "unreadable_evidence"}))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["allNumericGatesMet"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
