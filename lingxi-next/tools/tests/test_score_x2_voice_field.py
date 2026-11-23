import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from score_x2_voice_field import EvidenceError, Recording, audit_recording, score_manifest, score_trials


def fixture():
    rate = 8000
    stride = 6 * rate
    wake = [dict(recording="trials", start=i * stride, end=(i + 1) * stride, speechEnd=i * stride + rate // 2,
                 wakeConfirmed=True, firstAudible=i * stride + rate // 2 + 2 * rate) for i in range(100)]
    negatives = [dict(recording="trials", start=(100 + i) * stride, end=(101 + i) * stride,
                      case=kind, executed=False) for i, kind in enumerate(("ambiguous", "unauthorized"))]
    recordings = {"trials": Recording(rate, 102 * stride, "a" * 64),
                  "noise": Recording(rate, 8 * 3600 * rate, "b" * 64)}
    evidence = dict(schemaVersion=1, captureKind="synthetic", recordings={}, wakeTrials=wake, negativeTrials=negatives,
                    target=dict(deviceRef="test", firmware="standin", aimdk="test", release="test", profileSha256="c" * 64),
                    noise=dict(recording="noise", monitoringComplete=True, falseWakeFrames=[rate]))
    return evidence, recordings


def wav(path, rate, frames, value=0):
    with wave.open(str(path), "wb") as file:
        file.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        for start in range(0, frames, 32000):
            file.writeframesraw(bytes((value, 0)) * min(frames - start, 32000))
    with path.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(stream.read()).hexdigest()
    return {"file": path.name, "sha256": sha}


class VoiceFieldTests(unittest.TestCase):
    def test_boundary_wake_and_latency_use_all_100_trials(self):
        evidence, recordings = fixture()
        for trial in evidence["wakeTrials"][:5]:
            trial.update(firstAudible=None, wakeConfirmed=False)
        report = score_trials(evidence, recordings)
        self.assertEqual((report["wakeConfirmed"], report["firstAudibleMissing"], report["firstAudibleP95Ms"]), (95, 5, 2000))
        self.assertTrue(report["allNumericGatesMet"])
        self.assertFalse(report["productionAccepted"])
        evidence["wakeTrials"][5]["firstAudible"] += 1
        self.assertFalse(score_trials(evidence, recordings)["gates"]["firstAudibleP95Within2s"])
        evidence["wakeTrials"][5].update(firstAudible=None, wakeConfirmed=False)
        report = score_trials(evidence, recordings)
        self.assertIsNone(report["firstAudibleP95Ms"])
        self.assertFalse(report["gates"]["wake95of100"])
        self.assertFalse(report["gates"]["firstAudibleP95Within2s"])

    def test_a_wake_without_feedback_is_not_discarded_from_latency(self):
        evidence, recordings = fixture()
        for trial in evidence["wakeTrials"][:6]:
            trial["firstAudible"] = None
        gates = score_trials(evidence, recordings)["gates"]
        self.assertTrue(gates["wake95of100"])
        self.assertFalse(gates["firstAudibleP95Within2s"])

    def test_noise_requires_duration_complete_audit_and_at_most_one_event(self):
        evidence, recordings = fixture()
        gate = "noise8hAtMostOneFalseWake"
        self.assertTrue(score_trials(evidence, recordings)["gates"][gate])
        actual = recordings["noise"]
        recordings["noise"] = Recording(actual.rate, actual.frames - 1, actual.sha256)
        self.assertFalse(score_trials(evidence, recordings)["gates"][gate])
        recordings["noise"] = actual
        evidence["noise"]["monitoringComplete"] = False
        self.assertFalse(score_trials(evidence, recordings)["gates"][gate])
        evidence["noise"].update(monitoringComplete=True, falseWakeFrames=[1, 2])
        self.assertFalse(score_trials(evidence, recordings)["gates"][gate])
        evidence["noise"]["falseWakeFrames"] = [1, 1]
        with self.assertRaisesRegex(EvidenceError, "duplicate_or_unordered_false_wakes"):
            score_trials(evidence, recordings)

    def test_negative_trials_require_both_categories_and_no_wrong_execution(self):
        evidence, recordings = fixture()
        evidence["negativeTrials"][0]["executed"] = True
        report = score_trials(evidence, recordings)
        self.assertEqual(report["wrongExecutions"], 1)
        self.assertFalse(report["allNumericGatesMet"])
        evidence["negativeTrials"].pop()
        with self.assertRaisesRegex(EvidenceError, "missing_negative_case_coverage"):
            score_trials(evidence, recordings)

    def test_rejects_overlapping_windows_out_of_window_audio_and_wrong_firmware(self):
        evidence, recordings = fixture()
        trial = evidence["wakeTrials"][0]
        trial["firstAudible"] = trial["speechEnd"] - 1
        with self.assertRaisesRegex(EvidenceError, "response_not_owned"):
            score_trials(evidence, recordings)
        trial["firstAudible"] = trial["end"]
        with self.assertRaisesRegex(EvidenceError, "invalid_sample_frame"):
            score_trials(evidence, recordings)
        trial.update(evidence["wakeTrials"][1])
        with self.assertRaisesRegex(EvidenceError, "overlapping_trial_windows"):
            score_trials(evidence, recordings)
        evidence["captureKind"] = "field"
        with self.assertRaisesRegex(EvidenceError, "field_firmware_not_x2_v1_1_5"):
            score_trials(evidence, recordings)

    def test_manifest_reads_real_pcm_and_cli_does_not_echo_filenames_or_target(self):
        evidence, recordings = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            evidence["recordings"] = {"trials": wav(root / "private-phrases.wav", 8000, recordings["trials"].frames),
                                      "noise": wav(root / "private-site.wav", 8000, 2 * 8000, value=1)}
            evidence["target"]["deviceRef"] = "private-device"
            path = root / "input.json"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            report = score_manifest(path)
            self.assertEqual(report["noiseRecordedSeconds"], 2)
            self.assertFalse(report["allNumericGatesMet"])
            result = subprocess.run([sys.executable, str(TOOLS / "score_x2_voice_field.py"), str(path)],
                                    capture_output=True, check=False, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), report)
            self.assertNotIn(b"private-", result.stdout)

    def test_rejects_truncation_swapped_digest_duplicate_json_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "short.wav"
            reference = wav(path, 8000, 8)
            self.assertEqual(audit_recording(root, reference).frames, 8)
            with self.assertRaisesRegex(EvidenceError, "recording_digest_mismatch"):
                audit_recording(root, {**reference, "sha256": "a" * 64})
            with self.assertRaisesRegex(EvidenceError, "invalid_recording_path"):
                audit_recording(root, {**reference, "file": "../short.wav"})
            path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaisesRegex(EvidenceError, "truncated_pcm_recording"):
                audit_recording(root, reference)
            manifest = root / "manifest.json"
            manifest.write_text('{"target": {}, "target": {}}', encoding="utf-8")
            with self.assertRaisesRegex(EvidenceError, "duplicate_json_key"):
                score_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
