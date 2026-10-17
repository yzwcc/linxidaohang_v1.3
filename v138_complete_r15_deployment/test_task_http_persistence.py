"""Exercise Node validation -> real Python HTTP -> task storage, without ROS."""
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

from robot_local_navigation_v3.config_store import UnifiedConfigStore
from robot_local_navigation_v3.map_label_server_v3 import V3LabelApp, V3LabelRequestHandler
from robot_local_navigation_v3.map_point_store import MapPointStore
from robot_local_navigation_v3.models import NavPoint


class TaskHttpPersistenceTests(unittest.TestCase):
    def test_console_to_python_preserves_wait_and_commands_after_store_reload(self):
        with tempfile.TemporaryDirectory(prefix="r15-task-http-") as directory:
            root = Path(directory)
            store = MapPointStore(root / "maps")
            map_name = "1789696800000"
            store.save_points(map_name, int(map_name), [NavPoint.from_dict({
                "id": "p1", "name": "Reception", "map_name": map_name, "map_id": int(map_name),
            })])
            reloads = []

            class BridgeFixture:
                def reload_catalog(self):
                    reloads.append(True)

            class Handler(V3LabelRequestHandler):
                app = V3LabelApp(UnifiedConfigStore(root / "config.json"), store, BridgeFixture(), None)
                default_map_root = str(root)

                def log_message(self, *args):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                script = r'''
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { readFile } from 'node:fs/promises';
const base = pathToFileURL(process.env.TASK_CONSOLE_DIST + '/');
const { buildApp } = await import(new URL('app.js', base));
const { loadConfig } = await import(new URL('config.js', base));
const config = loadConfig();
config.auth.devBypass = true;
config.robot.connectionMode = 'direct';
config.robot.host = '127.0.0.1';
config.robot.webPort = Number(process.env.TASK_MAP_PORT);
const app = buildApp(config);
const url = '/api/robots/default/maps/1789696800000/task';
const task = { id: 'customer-tour', name: 'Customer tour', enabled: true,
  command_topic: '参观接待', start_phrases: ['开始参观接待'],
  steps: [{ point_id: 'p1', enabled: true, speech_text: '欢迎参观。', wait_sec: 0 }] };
try {
  for (const wait of [0, 5, 45, 300]) {
    task.steps[0].wait_sec = wait;
    const reply = await app.inject({ method: 'POST', url, payload: task });
    assert.equal(reply.statusCode, 200, reply.body);
    assert.equal(reply.json().steps[0].wait_sec, wait);
    const stored = JSON.parse(await readFile(process.env.TASK_STORE_FILE, 'utf8'));
    assert.equal(stored.tour_task.steps[0].wait_sec, wait);
    assert.equal(stored.tour_task.steps[0].speech_text, '欢迎参观。');
    assert.deepEqual(stored.tour_task.start_phrases, ['开始参观接待']);
  }
  const before = await readFile(process.env.TASK_STORE_FILE, 'utf8');
  task.steps[0].wait_sec = 301;
  assert.equal((await app.inject({ method: 'POST', url, payload: task })).statusCode, 400);
  task.steps[0].wait_sec = 45;
  task.steps[0].point_id = 'unknown-point';
  assert((await app.inject({ method: 'POST', url, payload: task })).statusCode >= 400);
  assert.equal(await readFile(process.env.TASK_STORE_FILE, 'utf8'), before);
  console.log('TASK_HTTP_PERSISTENCE_PASSED');
} finally { await app.close(); }
'''
                env = os.environ | {
                    "DEPLOYMENT_MODE": "robot", "TASK_MAP_PORT": str(server.server_port),
                    "TASK_STORE_FILE": str(store.path_for_map(map_name)),
                }
                result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                                        env=env, cwd=root, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=45)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("TASK_HTTP_PERSISTENCE_PASSED", result.stdout)
                task = MapPointStore(root / "maps").load_tour_task(map_name)
                self.assertEqual(task["steps"][0]["wait_sec"], 300)
                self.assertEqual(task["command_topic"], "参观接待")
                self.assertEqual(task["start_phrases"], ["开始参观接待"])
                self.assertEqual(len(reloads), 4)
                self.assertEqual(len(json.loads(store.path_for_map(map_name).read_text(encoding="utf-8"))["points"]), 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
