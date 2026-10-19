from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from robot_local_navigation_v3.map_label_server_v3 import V3LabelApp
from robot_local_navigation_v3.map_point_store import MapPointStore
from robot_local_navigation_v3.tour_step_actions import DEFAULT_TASK_WAIT_SEC


def test_legacy_single_task_gets_new_customer_defaults():
    with TemporaryDirectory() as directory:
        map_path = Path(directory) / "1783577672897.json"
        map_path.write_text(
            json.dumps({
                "map_name": "1783577672897",
                "map_id": 1783577672897,
                "points": [{"id": "p1"}],
                "tour_task": {
                    "name": "旧版讲解任务",
                    "steps": [{"point_id": "p1", "speech_text": "欢迎参观"}],
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        result = MapPointStore(directory).load_tour_tasks("1783577672897")

        task = result["active_task"]
        assert task["name"] == "旧版讲解任务"
        assert task["command_topic"] == ""
        assert task["start_phrases"] == []
        assert task["loop_count"] == 1
        assert task["steps"][0]["wait_sec"] == DEFAULT_TASK_WAIT_SEC


def test_legacy_trigger_phrases_are_migrated_without_data_loss():
    with TemporaryDirectory() as directory:
        map_path = Path(directory) / "map-a.json"
        map_path.write_text(
            json.dumps({
                "points": [{"id": "lobby"}],
                "tour_tasks": [{
                    "id": "business",
                    "name": "业务办理",
                    "trigger_phrases": ["我要办理业务", "带我办理业务"],
                    "steps": [{"point_id": "lobby", "wait_sec": 45}],
                }],
                "active_tour_task_id": "business",
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        task = MapPointStore(directory).load_tour_tasks("map-a")["active_task"]

        assert task["id"] == "business"
        assert task["start_phrases"] == ["我要办理业务", "带我办理业务"]
        assert task["steps"][0]["wait_sec"] == 45.0


def test_label_server_save_keeps_requested_loop_count():
    class FakeConfig:
        navigation = None

    class FakeConfigStore:
        def load(self):
            return FakeConfig()

    class FakePointStore:
        def __init__(self):
            self.saved = None

        def load_points(self, _map_name, defaults=None):
            return []

        def save_tour_task(self, _map_name, _map_id, task):
            self.saved = task
            return task

    class FakeBridgeManager:
        def reload_catalog(self):
            pass

    for loop_count in (2, 5, 20):
        app = V3LabelApp.__new__(V3LabelApp)
        app.config_store = FakeConfigStore()
        app.point_store = FakePointStore()
        app.bridge_manager = FakeBridgeManager()

        result = app.save_tour_task({
            "map_name": "map-a",
            "map_id": 1,
            "name": "tour",
            "loop_count": loop_count,
            "steps": [],
        })

        assert app.point_store.saved["loop_count"] == loop_count
        assert result["loop_count"] == loop_count


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"通过 {len(tests)} 项讲解任务存储兼容测试")
