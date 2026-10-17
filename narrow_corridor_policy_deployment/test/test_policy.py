from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("apply_policy", ROOT / "apply_policy.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def load_policy() -> dict:
    return json.loads((ROOT / "policy.json").read_text(encoding="utf-8"))


def test_policy_updates_nested_repeated_sections(tmp_path: Path) -> None:
    app2 = tmp_path / "app2.yaml"
    app2.write_text(
        "costmap_node:\n"
        "  costmap:\n"
        "    inflation_layer:\n"
        "      inflation_radius: 0.65\n"
        "  static_costmap:\n"
        "    inflation_layer:\n"
        "      inflation_radius: 0.65\n"
        "  local_costmap:\n"
        "    inflation_layer:\n"
        "      inflation_radius: 0.65\n"
        "map_costmap_node:\n"
        "  static_costmap:\n"
        "    inflation_layer:\n"
        "      inflation_radius: 1.0\n"
        "planner_server:\n"
        "  Ego:\n"
        "    obs_clearance: 0.4\n"
        "    dyna_obs_clearance: 0.8\n"
        "bt_task_manager:\n"
        "  PedestrianCollisionDetectionTask:\n"
        "    minimum_pedestrian_distance: 0.7\n"
        "  LocalTargetDeciderTask:\n"
        "    obstacle_boundary_clearance: 0.38\n"
        "    obstacle_drag_distance: 0.6\n"
        "    min_dist_to_ego_: 0.6\n"
        "    foot_print_padding_distance: 0.05\n"
        "    stop_length: 0.5\n"
        "    failsafe_obstacle_distance_threshold: 0.3\n",
        encoding="utf-8",
    )
    mppi = tmp_path / "mppi.yaml"
    mppi.write_text(
        "optimizer:\n  vx_planning:\n    near_obstacle_slowdown_distance: 0.5\n",
        encoding="utf-8",
    )
    p = load_policy()
    MODULE.apply_file(app2, p["files"]["app2.yaml"])
    MODULE.apply_file(mppi, p["files"]["mppi_controller.yaml"])
    MODULE.apply_file(app2, p["files"]["app2.yaml"], verify=True)
    MODULE.apply_file(mppi, p["files"]["mppi_controller.yaml"], verify=True)
    text = app2.read_text(encoding="utf-8")
    assert text.count("inflation_radius: 0.50") == 3
    assert "inflation_radius: 0.65" in text
    assert "near_obstacle_slowdown_distance: 0.35" in mppi.read_text(encoding="utf-8")


def test_policy_has_safety_floor_values() -> None:
    p = load_policy()
    app2 = {".".join(item["path"]): float(item["value"]) for item in p["files"]["app2.yaml"]}
    mppi = {".".join(item["path"]): float(item["value"]) for item in p["files"]["mppi_controller.yaml"]}
    assert app2["costmap_node.costmap.inflation_layer.inflation_radius"] >= 0.50
    assert app2["bt_task_manager.LocalTargetDeciderTask.obstacle_boundary_clearance"] >= 0.34
    assert app2["bt_task_manager.LocalTargetDeciderTask.failsafe_obstacle_distance_threshold"] >= 0.25
    assert mppi["optimizer.vx_planning.near_obstacle_slowdown_distance"] >= 0.35
