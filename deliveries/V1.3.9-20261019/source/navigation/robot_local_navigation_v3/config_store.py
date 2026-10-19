from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from robot_local_navigation_v3.models import UnifiedNavConfig


class UnifiedConfigStore:
    """JSON-backed store for v2 runtime configuration."""

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path).expanduser()
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.file_path.exists()

    def load(self) -> UnifiedNavConfig:
        if not self.file_path.exists():
            return UnifiedNavConfig()
        raw_text = self.file_path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return UnifiedNavConfig()
        payload = json.loads(raw_text)
        if not isinstance(payload, dict):
            raise ValueError("unified config root must be a JSON object")
        return UnifiedNavConfig.from_dict(payload)

    def save(self, config: UnifiedNavConfig) -> UnifiedNavConfig:
        config.updated_at = time.time()
        self.file_path.write_text(
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return config

    def update(self, patch: dict[str, Any]) -> UnifiedNavConfig:
        config = self.load()
        payload = config.to_dict()
        _deep_update(payload, patch)
        return self.save(UnifiedNavConfig.from_dict(payload))


def _deep_update(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
