"""Task storage must survive failed saves and concurrent HTTP worker updates."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from robot_local_navigation_v3 import map_point_store
from robot_local_navigation_v3.models import NavPoint


def point(name):
    return NavPoint.from_dict({"id": name, "name": name, "map_name": "123", "map_id": 123})


def test_failed_replace_keeps_old_complete_task_and_removes_temp_file(tmp_path, monkeypatch):
    store = map_point_store.MapPointStore(tmp_path)
    store.upsert_point(point("p1"))
    store.save_tour_task("123", 123, {"id": "original", "loop_count": 3, "steps": ["p1"]})
    original = store.path_for_map("123").read_bytes()

    def fail_replace(source, target):
        assert source.parent == target.parent
        assert json.loads(source.read_text(encoding="utf-8"))["tour_task"]["id"] == "new"
        raise OSError("injected storage replacement failure")

    monkeypatch.setattr(map_point_store.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected storage"):
        store.save_tour_task("123", 123, {"id": "new", "steps": ["p1"]})
    assert store.path_for_map("123").read_bytes() == original
    assert store.load_tour_tasks("123")["active_task"]["loop_count"] == 3
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_task_updates_do_not_overwrite_other_tasks(tmp_path):
    store = map_point_store.MapPointStore(tmp_path)
    store.upsert_point(point("p1"))
    with ThreadPoolExecutor(max_workers=8) as workers:
        futures = [workers.submit(store.save_tour_task, "123", 123,
                   {"id": f"task-{index}", "name": f"Tour {index}", "steps": ["p1"]})
                   for index in range(16)]
        for future in futures:
            future.result()
    restored = map_point_store.MapPointStore(tmp_path).load_tour_tasks("123")
    assert {task["id"] for task in restored["tasks"]} == {f"task-{index}" for index in range(16)}
    assert all(task["steps"][0]["point_id"] == "p1" for task in restored["tasks"])
