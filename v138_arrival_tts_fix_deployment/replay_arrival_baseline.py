"""Compare the unchanged field distances against actual shipped session code."""
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NAV = ROOT / "v138_complete_r15_deployment/candidate/source/navigation"
sys.path.insert(0, str(NAV))
sys.path.insert(0, str(ROOT / "v138_complete_r15_deployment"))
from archive_utils import read_archive
from test.test_pnc_discovery import navigation_client as current
from test.test_near_goal_arrival import odom
from robot_local_navigation_v3.models import NavigationDefaults


def replay(module, distance):
    defaults = NavigationDefaults()
    defaults.arrival_policy.position_tolerance_m = .15
    clock = [100.0]
    node = Mock()
    node.get_clock.return_value.now.side_effect = lambda: NS(nanoseconds=int(clock[0] * 1e9))
    point = NS(id="p", name="field-point", yaw=0.0, pose=NS(position=NS(x=0.0, y=0.0)),
               navigation=NS(target_pose_radius=.15))
    session = module.NavigationSession(
        node=node, publisher=Mock(), defaults=defaults, point=point, listener=None,
        mode="execute", republish_period_sec=1, progress_period_sec=1, timeout_sec=120,
        allow_initial_arrival_hysteresis=False, on_finished=Mock(), task_id=123,
    )
    session._status = "navigating"
    session._complete_async = Mock()
    with patch.object(module.time, "monotonic", side_effect=lambda: clock[0]):
        for i in range(601):
            clock[0] = 100 + i * .2
            session.handle_odom(odom(clock[0], distance=distance))
            if session._complete_async.called:
                return {"completed": True, "elapsed_sec": round(clock[0] - 100, 3),
                        "reason": session._complete_async.call_args.args[0]}
    return {"completed": False, "elapsed_sec": 120.0}


def main():
    name = "lingxi-v1.3.8-complete-20261019-r16-deployment"
    outer = read_archive((ROOT / "v138_complete_r16_deployment/dist" / (name + ".tar.gz")).read_bytes(), name)
    component = "sparkrobot-lingxi-nav-internal-1.0.19-20261019-r16"
    shipped = read_archive(outer["components/" + component + ".tar.gz"][1], component)
    old = ModuleType("original_field_navigation")
    sys.modules[old.__name__] = old
    exec(compile(shipped["payload/navigation/robot_local_navigation_v3/navigation_client.py"][1],
                 "shipped-r16/navigation_client.py", "exec"), old.__dict__)
    results = []
    for distance in (.3589467, .4241447):
        before, after = replay(old, distance), replay(current, distance)
        assert not before["completed"]
        assert after["completed"] and after["elapsed_sec"] == 5 and after["reason"] == "near_goal_stationary"
        results.append({"distance_m": distance, "shipped_r16": before, "patched": after})
    output = HERE / "work/baseline-replay.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
