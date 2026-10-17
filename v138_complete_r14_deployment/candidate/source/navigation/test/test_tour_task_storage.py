from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

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


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"通过 {len(tests)} 项讲解任务存储兼容测试")
