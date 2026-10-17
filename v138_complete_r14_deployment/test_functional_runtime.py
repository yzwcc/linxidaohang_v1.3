"""Connection-state validation and Linux process orchestration; never contacts a robot."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
SCRIPTS = Path(os.environ.get("R14_SCRIPTS", HERE / "candidate/scripts"))
spec = importlib.util.spec_from_file_location("connection_status", SCRIPTS / "agent_connection_status.py")
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)


class ConnectionStatusTests(unittest.TestCase):
    def test_current_auth_and_robot_online(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "state.json"
            file.write_text(json.dumps({"pid": 123, "auth_code": 0, "robot_online": True}))
            self.assertEqual(status.connection_fields(file, 123, True)["robot_online"], "true")
            self.assertEqual(status.connection_fields(file, 123, True)["lingxi_connected"], "true")

    def test_old_malformed_unauthenticated_and_stopped_are_not_online(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "state.json"
            for value in (None, [], {"pid": 12}, {"pid": 123, "auth_code": 401, "robot_online": True},
                          {"pid": 123, "auth_code": False, "robot_online": True},
                          {"pid": 123, "auth_code": "0", "robot_online": True}):
                file.write_text(json.dumps(value))
                fields = status.connection_fields(file, 123, True)
                self.assertEqual(fields["lingxi_connected"], "false")
                self.assertEqual(fields["robot_online"], "false")
            file.write_text("not-json")
            self.assertEqual(status.connection_fields(file, 123, True)["robot_online"], "false")
            file.write_text('{"pid":123,"auth_code":0,"robot_online":true}')
            self.assertEqual(status.connection_fields(file, 123, False)["robot_online"], "false")
            self.assertEqual(status.connection_fields(file, 456, True)["robot_online"], "false")


@unittest.skipUnless(os.name == "posix", "requires Linux /proc and Bash")
class NavigationControlTests(unittest.TestCase):
    def test_repeat_start_partial_recovery_concurrency_and_stale_pid(self):
        fixture = '''import argparse, os
from http.server import BaseHTTPRequestHandler, HTTPServer
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    args, _ = parser.parse_known_args()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
        def log_message(self, *_): pass
    server = HTTPServer(('127.0.0.1', args.port), Handler)
    with open(os.environ['QA_LAUNCHES'], 'a') as trace: trace.write(str(args.port) + '\\n')
    server.serve_forever()
if __name__ == '__main__': main()
'''
        with tempfile.TemporaryDirectory(prefix="navigation-control-") as root:
            root = Path(root)
            product = root / "product"
            modules = product / "navigation/robot_local_navigation_v3"
            modules.mkdir(parents=True)
            (modules / "__init__.py").touch()
            rclpy = product / "navigation/rclpy"
            rclpy.mkdir()
            (rclpy / "__init__.py").touch(); (rclpy / "action.py").touch()
            for module in ("lingxi_nav_bridge", "map_label_server_v3", "voice_nav_gateway"):
                (modules / (module + ".py")).write_text(fixture)
            gateway = (SCRIPTS.parent / "source/navigation/robot_local_navigation_v3/voice_nav_gateway.py").read_text()
            gateway = gateway.replace("from __future__ import annotations", """from __future__ import annotations
import os, sys
with open(os.environ['QA_LAUNCHES'], 'a') as trace:
    trace.write(sys.argv[sys.argv.index('--port') + 1] + '\\n')
""")
            (modules / "voice_nav_gateway.py").write_text(gateway)
            ports = []
            reservations = []
            for _ in range(4):
                sock = socket.socket(); sock.bind(("127.0.0.1", 0))
                ports.append(sock.getsockname()[1]); reservations.append(sock)
            env = os.environ | {
                "SPARKROBOT_PRODUCT_ROOT": str(product), "SPARKROBOT_STATE_ROOT": str(root / "state"),
                "SPARKROBOT_LOG_DIR": str(root / "logs"), "ROBOT_LOCAL_NAV_ROS_SETUP": str(root / "absent"),
                "ROBOT_LOCAL_NAV_AIMDK_SETUP": str(root / "absent"), "QA_LAUNCHES": str(root / "launches"),
                "ROBOT_LOCAL_NAVIGATION_V3_BRIDGE_PORT": str(ports[0]),
                "ROBOT_LOCAL_NAVIGATION_V3_WEB_PORT": str(ports[1]),
                "ROBOT_LOCAL_NAVIGATION_V3_VOICE_GATEWAY_PORT": str(ports[2]),
                "ROBOT_LOCAL_NAVIGATION_V3_VOICE_PORT": str(ports[3]),
            }
            for sock in reservations: sock.close()
            script = str(SCRIPTS / "navigation_control.sh")
            def invoke(action="start"):
                result = subprocess.run(["bash", script, action], env=env, capture_output=True, text=True, timeout=25)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            def pids():
                return {file.stem: int(file.read_text()) for file in (root / "state/pids").glob("*.pid")}
            extra = subprocess.Popen(["sleep", "120"])
            try:
                first = subprocess.Popen(["bash", script, "start"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                second = subprocess.Popen(["bash", script, "start"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for process in (first, second):
                    output = process.communicate(timeout=25)
                    self.assertEqual(process.returncode, 0, output)
                before = pids()
                self.assertEqual(len(before), 3)
                self.assertEqual(len((root / "launches").read_text().splitlines()), 3)
                with self.assertRaises(HTTPError) as degraded:
                    urlopen(f"http://127.0.0.1:{ports[2]}/gateway/health", timeout=5)
                self.assertEqual(degraded.exception.code, 503)
                health = json.loads(degraded.exception.read())
                self.assertTrue(health["services"]["navigation"]["ok"])
                self.assertFalse(health["services"]["voice"]["ok"])
                invoke()
                self.assertEqual(before, pids(), "repeated start must not stop healthy services")
                os.kill(before["map_label_server_v3"], signal.SIGTERM)
                time.sleep(0.4)
                (root / "state/pids/map_label_server_v3.pid").write_text(str(extra.pid))
                invoke()
                after = pids()
                self.assertEqual(before["lingxi_nav_bridge"], after["lingxi_nav_bridge"])
                self.assertEqual(before["voice_nav_gateway"], after["voice_nav_gateway"])
                self.assertNotEqual(before["map_label_server_v3"], after["map_label_server_v3"])
                self.assertIsNone(extra.poll(), "stale PID file must not terminate an unrelated process")
                self.assertEqual(len((root / "launches").read_text().splitlines()), 4)
            finally:
                invoke("stop")
                extra.terminate(); extra.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
