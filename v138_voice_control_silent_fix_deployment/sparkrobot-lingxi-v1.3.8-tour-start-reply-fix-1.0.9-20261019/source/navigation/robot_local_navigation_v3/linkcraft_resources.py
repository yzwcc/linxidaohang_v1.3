from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path
from typing import Any

import yaml


DEFAULT_RESOURCE_CONFIG_PATH = Path(
    "/agibot/nfs/soc0/var/robot_proxy/resources/resource_config.yaml"
)
DEFAULT_ACTION_DURATION_SEC = 30.0
MAX_ACTION_DURATION_SEC = 300.0
LINKCRAFT_RESOURCE_PREFIX = "linkcraft_resource_"
VALID_RESOURCE_TYPES = {"BODY_MONTION", "ARM_MONTION"}


def discover_linkcraft_resources(
    config_path: str | Path = DEFAULT_RESOURCE_CONFIG_PATH,
) -> list[dict[str, Any]]:
    """Read the robot resource catalog without invoking any action service."""

    path = Path(config_path)
    if not path.is_file():
        return []
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = payload.get("resource_config", []) if isinstance(payload, dict) else []
    if not isinstance(entries, list):
        return []

    resources: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        resource_key = str(entry.get("resource_key", "")).strip()
        if not resource_key.lower().startswith(LINKCRAFT_RESOURCE_PREFIX):
            continue
        current_version = entry.get("current_version")
        current_version = current_version if isinstance(current_version, dict) else {}
        resource_version = str(current_version.get("version", "")).strip()
        if not resource_version:
            continue
        configured_files = [
            str(item).strip()
            for item in current_version.get("files", [])
            if str(item).strip()
        ] if isinstance(current_version.get("files", []), list) else []
        resolved_paths = _resolve_resource_paths(configured_files)
        audio_path = _find_audio_file(resolved_paths)
        audio_duration_sec = _wav_duration_sec(audio_path) if audio_path else None
        duration_sec = audio_duration_sec or DEFAULT_ACTION_DURATION_SEC
        resources.append({
            "resource_key": resource_key,
            "resource_version": resource_version,
            "name": str(current_version.get("name", "")).strip() or resource_key,
            "resource_type": infer_resource_type(resource_key),
            "duration_sec": round(min(duration_sec, MAX_ACTION_DURATION_SEC), 3),
            "audio_duration_sec": round(audio_duration_sec, 3) if audio_duration_sec else None,
            "duration_source": "audio" if audio_duration_sec else "default",
            "available": any(item.exists() for item in resolved_paths),
            "configured_files": configured_files,
        })
    return sorted(resources, key=lambda item: (str(item["name"]), str(item["resource_key"])))


def normalize_linkcraft_action(value: Any) -> dict[str, Any] | None:
    """Normalize a saved task-step resource snapshot and reject unsafe keys."""

    if not isinstance(value, dict):
        return None
    resource_key = str(value.get("resource_key", "")).strip()
    resource_version = str(value.get("resource_version", "")).strip()
    if (
        not resource_key.lower().startswith(LINKCRAFT_RESOURCE_PREFIX)
        or not resource_version
    ):
        return None
    resource_type = str(value.get("resource_type", "")).strip().upper()
    if resource_type not in VALID_RESOURCE_TYPES:
        resource_type = infer_resource_type(resource_key)
    try:
        duration_sec = float(value.get("duration_sec", DEFAULT_ACTION_DURATION_SEC))
    except (TypeError, ValueError):
        duration_sec = DEFAULT_ACTION_DURATION_SEC
    duration_sec = min(MAX_ACTION_DURATION_SEC, max(0.1, duration_sec))
    return {
        "resource_key": resource_key,
        "resource_version": resource_version,
        "name": str(value.get("name", "")).strip() or resource_key,
        "resource_type": resource_type,
        "duration_sec": round(duration_sec, 3),
    }


def infer_resource_type(resource_key: str) -> str:
    # AimDK's public interface intentionally uses the historical MONTION spelling.
    return "BODY_MONTION" if "onnx" in str(resource_key).lower() else "ARM_MONTION"


def _resolve_resource_paths(configured_files: list[str]) -> list[Path]:
    resolved: list[Path] = []
    for raw_path in configured_files:
        candidates = [Path(raw_path)]
        if raw_path.startswith("/agibot/data/var/"):
            candidates.append(Path(raw_path.replace(
                "/agibot/data/var/", "/agibot/nfs/soc0/var/", 1
            )))
        if raw_path.startswith("/agibot/data/resources/"):
            candidates.append(Path(raw_path.replace(
                "/agibot/data/resources/",
                "/agibot/nfs/soc0/var/robot_proxy/resources/",
                1,
            )))
        existing = next((candidate for candidate in candidates if candidate.exists()), None)
        resolved.append(existing or candidates[-1])
    return resolved


def _find_audio_file(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file() and path.name.lower() == "audio.wav":
            return path
        if not path.is_dir():
            continue
        try:
            for candidate in path.rglob("*"):
                if candidate.is_file() and candidate.name.lower() == "audio.wav":
                    return candidate
        except OSError:
            continue
    return None


def _wav_duration_sec(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as audio:
            frame_rate = audio.getframerate()
            if frame_rate <= 0:
                return None
            return audio.getnframes() / float(frame_rate)
    except (OSError, EOFError, wave.Error):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="只读扫描机器人灵创资源")
    parser.add_argument("--config", default=str(DEFAULT_RESOURCE_CONFIG_PATH))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    resources = discover_linkcraft_resources(args.config)
    payload = {"ok": True, "resource_count": len(resources), "resources": resources}
    print(json.dumps(payload, ensure_ascii=False) if args.json else payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
