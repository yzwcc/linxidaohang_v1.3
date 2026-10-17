from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from robot_local_navigation_v3.atomic_storage import atomic_write_text
from robot_local_navigation_v3.models import NavPoint, NavigationDefaults, UnifiedNavConfig
from robot_local_navigation_v3.tour_commands import (
    normalize_command_topic,
    normalize_start_phrases,
)
from robot_local_navigation_v3.tour_step_actions import normalize_tour_step


class MapPointStore:
    """Stores navigation points in one JSON file per map."""

    def __init__(self, maps_dir: str | Path):
        self.maps_dir = Path(maps_dir).expanduser()
        self.maps_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def load_points(
        self,
        map_name: str,
        defaults: NavigationDefaults | None = None,
    ) -> list[NavPoint]:
        path = self.path_for_map(map_name)
        if not path.exists():
            return []
        payload = self._load_payload(path)
        return [
            NavPoint.from_dict(item, defaults=defaults)
            for item in payload.get("points", [])
            if isinstance(item, dict)
        ]

    def load_tour_task(self, map_name: str) -> dict[str, Any]:
        path = self.path_for_map(map_name)
        if not path.exists():
            return _normalize_tour_task(None)
        tasks_payload = self.load_tour_tasks(map_name)
        return tasks_payload["active_task"]

    def load_tour_tasks(self, map_name: str) -> dict[str, Any]:
        path = self.path_for_map(map_name)
        if not path.exists():
            return _normalize_tour_tasks({})
        payload = self._load_payload(path)
        valid_point_ids = {
            str(item.get("id", "")).strip()
            for item in payload.get("points", [])
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
        return _normalize_tour_tasks(payload, valid_point_ids=valid_point_ids)

    def save_tour_task(self, map_name: str, map_id: int, task: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            points = self.load_points(map_name)
            valid_point_ids = {point.id for point in points}
            path = self.path_for_map(map_name)
            existing_payload = self._load_payload(path) if path.exists() else {}
            tasks_payload = _normalize_tour_tasks(existing_payload, valid_point_ids=valid_point_ids)
            normalized_task = _normalize_tour_task(
                task,
                valid_point_ids=valid_point_ids,
                default_id=str(task.get("id", tasks_payload["active_tour_task_id"] or "default")),
            )
            existing_tasks = [
                item for item in tasks_payload["tasks"]
                if not (
                    normalized_task["id"] != "default"
                    and item["id"] == "default"
                    and not item.get("steps")
                )
            ]
            tasks_by_id = {item["id"]: item for item in existing_tasks}
            tasks_by_id[normalized_task["id"]] = normalized_task
            self.save_points(
                map_name,
                map_id,
                points,
                tour_task=normalized_task,
                tour_tasks=list(tasks_by_id.values()),
                active_tour_task_id=normalized_task["id"],
            )
            return normalized_task

    def list_all_points(self, defaults: NavigationDefaults | None = None) -> list[NavPoint]:
        points: list[NavPoint] = []
        for path in sorted(self.maps_dir.glob("*.json")):
            payload = self._load_payload(path)
            for item in payload.get("points", []):
                if isinstance(item, dict):
                    points.append(NavPoint.from_dict(item, defaults=defaults))
        return points

    def upsert_point(
        self,
        point: NavPoint,
        defaults: NavigationDefaults | None = None,
    ) -> NavPoint:
        with self._lock:
            points = self.load_points(point.map_name, defaults=defaults)
            for index, current in enumerate(points):
                if current.id == point.id or current.name == point.name:
                    points[index] = point
                    self.save_points(point.map_name, point.map_id, points)
                    return point
            points.append(point)
            self.save_points(point.map_name, point.map_id, points)
            return point

    def delete_point_by_id(self, point_id: str, defaults: NavigationDefaults | None = None) -> bool:
        with self._lock:
            for path in sorted(self.maps_dir.glob("*.json")):
                payload = self._load_payload(path)
                map_name = str(payload.get("map_name", path.stem))
                map_id = int(payload.get("map_id", 0) or 0)
                points = [
                    NavPoint.from_dict(item, defaults=defaults)
                    for item in payload.get("points", [])
                    if isinstance(item, dict)
                ]
                filtered = [point for point in points if point.id != point_id]
                if len(filtered) == len(points):
                    continue
                self.save_points(map_name, map_id, filtered)
                return True
            return False

    def save_points(
        self,
        map_name: str,
        map_id: int,
        points: list[NavPoint],
        *,
        tour_task: dict[str, Any] | None = None,
        tour_tasks: list[dict[str, Any]] | None = None,
        active_tour_task_id: str | None = None,
    ) -> None:
        with self._lock:
            path = self.path_for_map(map_name)
            normalized_points = _normalize_start_points(points)
            existing_payload = self._load_payload(path) if path.exists() else {}
            if tour_task is None:
                tour_task = existing_payload.get("tour_task")
            if tour_tasks is None:
                tour_tasks = existing_payload.get("tour_tasks")
            tasks_payload = _normalize_tour_tasks(
                {
                    "tour_task": tour_task,
                    "tour_tasks": tour_tasks,
                    "active_tour_task_id": active_tour_task_id or existing_payload.get("active_tour_task_id"),
                },
                valid_point_ids={point.id for point in normalized_points},
            )
            payload = {
                "schema_version": 1,
                "kind": "robot_local_nav.map_points",
                "map_name": str(map_name),
                "map_id": int(map_id),
                "updated_at": time.time(),
                "points": [point.to_dict() for point in normalized_points],
                "tour_task": tasks_payload["active_task"],
                "tour_tasks": tasks_payload["tasks"],
                "active_tour_task_id": tasks_payload["active_tour_task_id"],
            }
            atomic_write_text(
                path,
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            )

    def migrate_from_unified(self, config: UnifiedNavConfig) -> bool:
        if not config.points:
            return False
        grouped: dict[tuple[str, int], list[NavPoint]] = defaultdict(list)
        for point in config.points:
            grouped[(point.map_name, point.map_id)].append(point)
        for (map_name, map_id), points in grouped.items():
            existing = self.load_points(map_name, defaults=config.navigation)
            by_id = {point.id: point for point in existing}
            for point in points:
                by_id[point.id] = point
            self.save_points(map_name, map_id, list(by_id.values()))
        config.points = []
        return True

    def path_for_map(self, map_name: str) -> Path:
        return self.maps_dir / f"{_safe_map_filename(map_name)}.json"

    def _load_payload(self, path: Path) -> dict[str, Any]:
        raw_text = path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return {}
        payload = json.loads(raw_text)
        if not isinstance(payload, dict):
            raise ValueError(f"Map point file root must be an object: {path}")
        return payload


def _safe_map_filename(map_name: str) -> str:
    normalized = str(map_name or "").strip()
    if not normalized:
        raise ValueError("map_name is required")
    return "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in normalized)


def _normalize_start_points(points: list[NavPoint]) -> list[NavPoint]:
    start_indices = [index for index, point in enumerate(points) if point.is_start]
    if len(start_indices) <= 1:
        return points
    keep_index = start_indices[-1]
    for index, point in enumerate(points):
        if index != keep_index:
            point.is_start = False
    return points


def _normalize_tour_task(
    payload: Any,
    *,
    valid_point_ids: set[str] | None = None,
    default_id: str = "default",
) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    valid_point_ids = valid_point_ids or set()
    steps_payload = payload.get("steps", [])
    steps: list[dict[str, Any]] = []
    if isinstance(steps_payload, list):
        for item in steps_payload:
            step = normalize_tour_step(item)
            if step is None:
                continue
            point_id = step["point_id"]
            if valid_point_ids and point_id not in valid_point_ids:
                continue
            steps.append(step)
    return {
        "id": _safe_task_id(payload.get("id") or default_id),
        "name": str(payload.get("name", "默认讲解任务")).strip() or "默认讲解任务",
        "enabled": bool(payload.get("enabled", True)),
        "command_topic": normalize_command_topic(payload.get("command_topic")),
        "start_phrases": normalize_start_phrases(
            payload.get("start_phrases", payload.get("trigger_phrases", []))
        ),
        "steps": steps,
    }


def _normalize_tour_tasks(
    payload: Any,
    *,
    valid_point_ids: set[str] | None = None,
) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    active_id = _safe_task_id(payload.get("active_tour_task_id") or "")
    raw_tasks = payload.get("tour_tasks")
    tasks: list[dict[str, Any]] = []
    if isinstance(raw_tasks, list):
        for index, item in enumerate(raw_tasks):
            if isinstance(item, dict):
                tasks.append(_normalize_tour_task(
                    item,
                    valid_point_ids=valid_point_ids,
                    default_id=f"task_{index + 1}",
                ))
    if not tasks:
        tasks.append(_normalize_tour_task(
            payload.get("tour_task"),
            valid_point_ids=valid_point_ids,
            default_id=active_id or "default",
        ))
    by_id = {task["id"]: task for task in tasks}
    if active_id not in by_id:
        legacy_active = _normalize_tour_task(
            payload.get("tour_task"),
            valid_point_ids=valid_point_ids,
            default_id=active_id or tasks[0]["id"],
        )
        if active_id and legacy_active["id"] == active_id:
            by_id[legacy_active["id"]] = legacy_active
        else:
            active_id = tasks[0]["id"]
    if not active_id:
        active_id = tasks[0]["id"]
    tasks = list(by_id.values())
    active_task = by_id.get(active_id) or tasks[0]
    return {
        "active_tour_task_id": active_task["id"],
        "active_task": active_task,
        "tasks": tasks,
    }


def _safe_task_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    return "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in normalized)
