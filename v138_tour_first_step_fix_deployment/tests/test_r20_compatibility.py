"""Reuse the shipped r20 tour behavior suite against this patch."""
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
baseline = HERE / "reference/navigation/test/test_tour_bridge_behavior.py"
source = baseline.read_text(encoding="utf-8")
old_path = 'Path(__file__).resolve().parents[1] / "robot_local_navigation_v3" / "lingxi_nav_bridge.py"'
new_path = 'Path(__file__).resolve().parents[1] / "source/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py"'
assert source.count(old_path) == 1
source = source.replace(old_path, new_path)
marker = '    bridge._begin_task_arrival_action(event)\n'
assert source.count(marker) == 1
# The fixture models an accepted navigation; give it its real session identity.
source = source.replace(marker, '    bridge._task_navigation_session_id = 1\n' + marker)
exec(compile(source, str(baseline), "exec"), globals())
