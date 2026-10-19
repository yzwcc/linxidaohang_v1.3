"""Pytest functional check; unittest installer discovery does not import it."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from test.test_tour_bridge_behavior import bridge_module, rig
from robot_local_navigation_v3.config_store import UnifiedConfigStore
from robot_local_navigation_v3.map_label_server_v3 import (
    BridgeServiceManager, V3LabelApp, V3LabelRequestHandler,
)
from robot_local_navigation_v3.map_point_store import MapPointStore

HERE = Path(__file__).resolve().parent


def test_console_task_disk_reload_and_real_bridge_start(rig, tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    bridge = rig.bridge
    bridge._cancel_auto_return_timers()
    bridge._clear_task_locked("http_test")
    bridge._auto_return_state = rig.module.AUTO_RETURN_IDLE
    bridge.active_map_name = "123"
    bridge.config_store = UnifiedConfigStore(tmp_path / "config.json")
    bridge.config = bridge.config_store.load()
    bridge.point_store = MapPointStore(tmp_path / "maps")
    bridge.active_map_state_path = tmp_path / "active-map.txt"
    bridge.navigation_client.cancel.return_value = True
    bridge.reload_catalog()
    nav = ThreadingHTTPServer(("127.0.0.1", 0), rig.module.NavHttpHandler)
    nav.bridge = bridge
    manager = BridgeServiceManager(config_path=bridge.config_store.file_path,
                                   app_home=tmp_path, bridge_host="127.0.0.1",
                                   bridge_port=nav.server_port)
    app = V3LabelApp(bridge.config_store, bridge.point_store, manager, None)
    handler = type("IsolatedLabelHandler", (V3LabelRequestHandler,), {"app": app})
    web = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (nav, web)]
    map_root = tmp_path / "slam"
    map_dir = map_root / "123"
    map_dir.mkdir(parents=True)
    (map_dir / "occupancy_map.png").write_bytes(b"fixture-map")
    (map_dir / "grid_map_info.txt").write_text("20\n0 0\n100 100\n", encoding="utf-8")
    for thread in threads:
        thread.start()
    try:
        console = Path(os.environ.get("FUNCTIONAL_CONSOLE", HERE.parent / "source/server")).resolve()
        result = subprocess.run([
            shutil.which("node"), str(HERE / "check_task_flow.mjs"), str(console),
            str(web.server_port), str(nav.server_port), str(map_root),
        ], capture_output=True, text=True, encoding="utf-8", timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        print(next(line for line in result.stdout.splitlines() if line.startswith("TASK_HTTP_DISK_START_PASS")))
        # Construct a new store and bridge state, simulating restart rather than memory retention.
        bridge.point_store = MapPointStore(tmp_path / "maps")
        bridge._clear_task_locked("restart")
        bridge._auto_return_state = rig.module.AUTO_RETURN_IDLE
        bridge.reload_catalog()
        task = next(t for t in bridge.point_store.load_tour_tasks("123")["tasks"] if t["id"] == "persisted")
        assert task["steps"][0]["speech_text"] == "Second point first"
        assert task["loop_count"] == 3
        request = Request(f"http://127.0.0.1:{nav.server_port}/task/start",
                          data=json.dumps({"task_id": "persisted"}).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=3) as response:
            started = json.load(response)
        assert started["accepted"] is True
        assert started["goal_id"] == "p2"
        assert bridge.navigation_client.start.call_args.args[0].id == "p2"
        assert len(bridge._task_steps) == 2
        assert bridge._task_steps[0]["arrival_motion"]["area"] == "left_hand"
        # Real on-disk point deletion must prune references instead of navigating a stale point.
        bridge._clear_task_locked("delete")
        bridge._auto_return_state = rig.module.AUTO_RETURN_IDLE
        assert bridge.point_store.delete_point_by_id("p2")
        bridge.reload_catalog()
        assert bridge.start_task({"task_id": "persisted"})["goal_id"] == "p1"
    finally:
        bridge._cancel_auto_return_timers()
        for server in (web, nav):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=3)
