from __future__ import annotations

import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from robot_local_navigation_v3.linkcraft_resources import (
    DEFAULT_ACTION_DURATION_SEC,
    discover_linkcraft_resources,
    infer_resource_type,
    normalize_linkcraft_action,
)


def _write_wav(path: Path, duration_sec: float) -> None:
    frame_rate = 8000
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(frame_rate)
        audio.writeframes(b"\0\0" * int(frame_rate * duration_sec))


def test_discovers_only_linkcraft_resources_and_reads_wav_duration():
    with TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        dance_dir = tmp_path / "dance"
        dance_dir.mkdir()
        _write_wav(dance_dir / "audio.wav", 1.25)
        config_path = tmp_path / "resource_config.yaml"
        config_path.write_text(
            "resource_config:\n"
            "  - resource_key: app_other\n"
            "    current_version: {version: 1.0.0, name: other, files: []}\n"
            "  - resource_key: linkcraft_resource_onnx_test\n"
            "    current_version:\n"
            "      version: 0.0.1\n"
            "      name: 测试舞蹈\n"
            f"      files: [{dance_dir.as_posix()}]\n",
            encoding="utf-8",
        )

        resources = discover_linkcraft_resources(config_path)

        assert len(resources) == 1
        assert resources[0]["name"] == "测试舞蹈"
        assert resources[0]["resource_type"] == "BODY_MONTION"
        assert resources[0]["duration_sec"] == 1.25
        assert resources[0]["duration_source"] == "audio"
        assert resources[0]["available"] is True


def test_resource_without_audio_uses_bounded_default():
    with TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        action_dir = tmp_path / "action"
        action_dir.mkdir()
        (action_dir / "policy.bin").write_bytes(b"ok")
        config_path = tmp_path / "resource_config.yaml"
        config_path.write_text(
            "resource_config:\n"
            "  - resource_key: linkcraft_resource_arm_test\n"
            "    current_version:\n"
            "      version: 0.0.2\n"
            "      name: 测试动作\n"
            f"      files: [{action_dir.as_posix()}]\n",
            encoding="utf-8",
        )

        resource = discover_linkcraft_resources(config_path)[0]

        assert resource["duration_sec"] == DEFAULT_ACTION_DURATION_SEC
        assert resource["duration_source"] == "default"
        assert resource["resource_type"] == "ARM_MONTION"


def test_normalizes_safe_resource_snapshot():
    action = normalize_linkcraft_action({
        "resource_key": "LINKCRAFT_RESOURCE_ONNX_TEST",
        "resource_version": "0.0.1",
        "name": "太极",
        "resource_type": "BODY_MONTION",
        "duration_sec": 31.22,
    })
    assert action == {
        "resource_key": "LINKCRAFT_RESOURCE_ONNX_TEST",
        "resource_version": "0.0.1",
        "name": "太极",
        "resource_type": "BODY_MONTION",
        "duration_sec": 31.22,
    }
    assert normalize_linkcraft_action({"resource_key": "app_123", "resource_version": "1"}) is None
    assert infer_resource_type("linkcraft_resource_plain") == "ARM_MONTION"


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"通过 {len(tests)} 项灵创资源测试")
