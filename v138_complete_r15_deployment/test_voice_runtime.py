"""Linux voice-process product scenarios without ROS or robot access."""
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SCRIPTS = Path(os.environ.get("R15_SCRIPTS", HERE / "candidate/scripts"))


@unittest.skipUnless(os.name == "posix", "requires Linux /proc, Bash and runuser")
class VoiceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="r15-voice-")
        self.root = Path(self.temp.name)
        self.root.chmod(0o755)
        self.product = self.root / "product"
        modules = self.product / "navigation/robot_local_navigation_v3"
        modules.mkdir(parents=True)
        (modules / "__init__.py").touch()
        rclpy = self.product / "navigation/rclpy"
        rclpy.mkdir()
        (rclpy / "__init__.py").touch()
        (rclpy / "action.py").touch()
        (modules / "robot_lingxi_bridge.py").write_text('''import os
from http.server import BaseHTTPRequestHandler, HTTPServer
def main():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
        def log_message(self, *_): pass
    HTTPServer(('127.0.0.1', int(os.environ['LINGXI_BRIDGE_PORT'])), Handler).serve_forever()
''')
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.env = os.environ | {
            "SPARKROBOT_PRODUCT_ROOT": str(self.product),
            "SPARKROBOT_STATE_ROOT": str(self.root / "state"),
            "SPARKROBOT_LOG_DIR": str(self.root / "logs"),
            "ROBOT_LOCAL_NAV_ROS_SETUP": str(self.root / "absent"),
            "ROBOT_LOCAL_NAV_AIMDK_SETUP": str(self.root / "absent"),
            "LINGXI_BRIDGE_PORT": str(self.port),
        }
        if os.geteuid() == 0:
            self.env.update(SPARKROBOT_RUNTIME_USER="nobody", SPARKROBOT_RUNTIME_GROUP="nogroup")
        self.pidfile = self.root / "state/pids/robot_lingxi_bridge.pid"
        self.script = self.root / "robot_voice_bridge_control.sh"
        shutil.copyfile(SCRIPTS / self.script.name, self.script)
        self.script.chmod(0o755)
        self.external = []

    def invoke(self, action, **extra):
        return subprocess.run(["bash", str(self.script), action], env=self.env | extra,
                              capture_output=True, text=True, timeout=25)

    def spawn_unrelated(self):
        if os.geteuid() == 0:
            result = subprocess.run(["runuser", "-u", "nobody", "--", "bash", "-c",
                                     "sleep 120 </dev/null >/dev/null 2>&1 & echo $!"],
                                    capture_output=True, text=True, check=True)
            pid = int(result.stdout)
        else:
            child = subprocess.Popen(["sleep", "120"])
            self.external.append(child)
            pid = child.pid
        self.external.append(pid)
        return pid

    def alive(self, pid):
        stat = Path(f"/proc/{pid}/stat")
        return stat.exists() and stat.read_text().split()[2] != "Z"

    def test_stale_pid_cannot_stop_unrelated_process(self):
        self.invoke("status")
        self.pidfile.parent.mkdir(exist_ok=True, parents=True)
        pid = self.spawn_unrelated()
        self.pidfile.write_text(str(pid))
        if os.geteuid() == 0:
            import pwd, grp
            os.chown(self.pidfile, pwd.getpwnam("nobody").pw_uid, grp.getgrnam("nogroup").gr_gid)
        result = self.invoke("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.alive(pid), "stale voice PID must not kill an unrelated process")
        self.assertFalse(self.pidfile.exists())

    def test_repeat_and_concurrent_start_preserve_healthy_voice_session(self):
        first = self.invoke("start")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = int(self.pidfile.read_text())
        pending = [subprocess.Popen(["bash", str(self.script), "start"], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        for process in pending:
            output = process.communicate(timeout=25)
            self.assertEqual(process.returncode, 0, output)
        self.assertEqual(int(self.pidfile.read_text()), before, "start must preserve an existing voice session")
        self.assertTrue(self.alive(before))

    def test_unrelated_listener_does_not_make_voice_ready(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", self.port))
            sock.listen()
            result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0, "a foreign listener is not the voice service")
        self.assertIn("端口已被其他进程占用", result.stderr)

    def tearDown(self):
        try:
            self.invoke("stop")
        finally:
            marker = ("SPARKROBOT_PRODUCT_ROOT=" + str(self.product)).encode()
            for process in Path("/proc").iterdir():
                if process.name.isdigit():
                    try:
                        if marker in (process / "environ").read_bytes().split(b"\0"):
                            os.kill(int(process.name), signal.SIGTERM)
                    except (PermissionError, FileNotFoundError, ProcessLookupError): pass
            for process in self.external:
                try:
                    if isinstance(process, int): os.kill(process, signal.SIGTERM)
                    else: process.wait(timeout=3)
                except (ProcessLookupError, subprocess.TimeoutExpired): pass
            self.temp.cleanup()


if __name__ == "__main__":
    unittest.main()
