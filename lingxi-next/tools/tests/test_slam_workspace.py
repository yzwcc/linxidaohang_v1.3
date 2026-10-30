"""Shared runtime configuration and real HTTPS/Unix/process flow using synthetic workers."""
import base64
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(os.environ.get("LINGXI_SLAM_TEST_SOURCE", ROOT))
sys.path[:0] = [str(SOURCE / "robot-runtime/python"), str(SOURCE / "robot-slam")]
AVAILABLE = sys.platform == "linux" and os.geteuid() != 0 and all(
    importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
NODE, BUNDLE = os.environ.get("LINGXI_SLAM_TEST_NODE"), os.environ.get("LINGXI_SLAM_TEST_BUNDLE")
if AVAILABLE:
    from lingxi_slam.activity_lease import ActivityGate
    from mapping_owner import ManagedMappingOwner
    from slam_owner import ManagedSlamOwner
    from managed_mapping import validate_output
    import managed_slam


@unittest.skipUnless(AVAILABLE, "requires ordinary-user Linux localization dependencies")
class WorkspaceConfiguration(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="lxwsc-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.maps = self.root / "m"
        self.maps.mkdir(mode=0o700)

    def owner(self, **changes):
        return ManagedSlamOwner([sys.executable, "-c", "pass"], [sys.executable, "-c", "pass"],
            **{"maps": self.maps, "output": self.root / "r", "service_environment": {}, "api_environment": {}, **changes})

    def test_systemd_readiness_is_sent_only_from_the_verified_listening_callback(self):
        endpoint = self.root / "notify.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
            receiver.bind(str(endpoint))
            receiver.settimeout(1)
            with patch.dict(os.environ, {"NOTIFY_SOCKET": str(endpoint)}, clear=True), patch("builtins.print") as printed:
                managed_slam.notify_ready({"service": "lingxi-next", "event": "listening", "port": 3443})
            self.assertEqual(b"READY=1", receiver.recv(512))
            self.assertIn('"port": 3443', printed.call_args.args[0])

    def test_owner_uses_persistent_catalog_and_explicit_distinct_service_endpoints(self):
        sentinel = self.maps / "preserved"
        sentinel.write_bytes(b"synthetic retained catalog item")
        owner = self.owner()
        self.assertEqual(self.maps, owner.maps)
        self.assertNotEqual(owner.endpoint, owner.localization_endpoint)
        self.assertTrue(owner.continuous)
        self.assertEqual("lingxi-slam", owner.service_name)
        self.assertEqual("1", owner._api_configuration()["LINGXI_LOCALIZATION_SERVICE"])
        self.assertIn(str(owner.runs), owner._service_arguments())
        self.assertEqual(b"synthetic retained catalog item", sentinel.read_bytes())

    def test_catalog_must_be_private_and_separate_from_run_directory(self):
        for output in (self.maps / "r", self.root):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.owner(output=output)
        os.chmod(self.maps, 0o755)
        with self.assertRaises(ValueError):
            self.owner()
        os.chmod(self.maps, 0o700)
        with self.assertRaises(ValueError):
            self.owner(api_environment={"LINGXI_PORT": "3000"})
        self.assertFalse((self.root / "r").exists())

    def test_existing_catalog_validation_preserves_frozen_inputs(self):
        frozen = self.maps / "frozen"
        frozen.mkdir()
        with self.assertRaises(ValueError):
            validate_output(self.maps, {"pointAdapter": str(frozen)}, (), existing=True)
        with self.assertRaises(ValueError):
            validate_output(self.maps, {}, (self.maps / "config.json",), existing=True)
        validate_output(self.maps, {"pointAdapter": str(self.root / "separate")}, (), existing=True)

    def test_old_mapping_owner_still_requires_a_bounded_duration(self):
        owner = ManagedMappingOwner([sys.executable, "-c", "pass"], [sys.executable, "-c", "pass"],
            output=self.root / "bounded", service_environment={}, api_environment={})
        with self.assertRaises(ValueError):
            owner.run(seconds=None)
        self.assertIsNone(owner.service)

    def test_persistent_launcher_keeps_native_inputs_and_only_forwards_reviewed_environment(self):
        frozen = self.root / "frozen"
        frozen.mkdir()
        api = frozen / "api.mjs"
        api.write_bytes(b"// synthetic runtime identity, not executed\n")
        executable = Path(sys.executable).resolve()
        arguments = ["managed_slam.py", "--config", str(frozen / "config.json"), "--maps", str(self.maps),
            "--output", str(self.root / "r"), "--node", str(executable),
            "--node-sha256", hashlib.sha256(executable.read_bytes()).hexdigest(),
            "--api-bundle", str(api), "--api-sha256", hashlib.sha256(api.read_bytes()).hexdigest()]
        required = {"LINGXI_TLS_KEY": "/synthetic/key", "LINGXI_TLS_CERT": "/synthetic/cert",
                    "LINGXI_PAIRING_CODE_SHA256": "a" * 64}
        with patch.object(sys, "argv", arguments), patch("managed_slam.verified_configuration", return_value=(
                {"pointAdapter": str(frozen)}, {})), patch("managed_slam.signal.signal"), \
                patch.dict(os.environ, {**required, "LINGXI_DEMO_MODE": "1", "NODE_OPTIONS": "--inspect"}, clear=True), \
                patch("managed_slam.ManagedSlamOwner") as owner:
            owner.return_value.run.return_value = {"result": "stopped"}
            self.assertEqual(0, managed_slam.main())
        self.assertIsNone(owner.return_value.run.call_args.kwargs["seconds"])
        self.assertEqual({"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", **required}, owner.call_args.kwargs["api_environment"])
        self.assertFalse((self.root / "r").exists())


@unittest.skipUnless(AVAILABLE and NODE and BUNDLE, "requires explicit Linux Node and compiled shared SLAM API")
class WorkspaceProcesses(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lxws-")
        self.root = Path(self.temporary.name)
        self.maps = self.root / "m"
        self.maps.mkdir(mode=0o700)
        self.owners = []
        self.key, self.cert = self.root / "key.pem", self.root / "cert.pem"
        subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(self.key), "-out", str(self.cert), "-days", "1", "-subj", "/CN=Synthetic SLAM test",
            "-addext", "subjectAltName=IP:127.0.0.1"], check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ssl = ssl.create_default_context(cafile=str(self.cert))

    def tearDown(self):
        try:
            for process, _ in self.owners:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=25)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
        finally:
            self.temporary.cleanup()

    def start(self):
        output = self.root / ("r" + str(len(self.owners)))
        process = subprocess.Popen([sys.executable, "-B", str(ROOT / "tools/tests/fixtures/slam_workspace_child.py"),
            "owner", "--maps", str(self.maps), "--output", str(output), "--node", NODE, "--bundle", BUNDLE,
            "--tls-key", str(self.key), "--tls-cert", str(self.cert)],
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                 "LINGXI_SLAM_TEST_SOURCE": str(SOURCE),
                 "LINGXI_SLAM_TEST_WEB": os.environ.get("LINGXI_SLAM_TEST_WEB", str(ROOT / "dist"))},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.owners.append((process, output))
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(22), "shared owner startup deadline")
            line = process.stdout.readline()
        if not line:
            self.fail(process.stderr.read(4096).decode())
        status = json.loads(line)
        self.status, self.base = status, "https://127.0.0.1:" + str(status["port"])
        self.token = None
        self.token = self.request("POST", "/session/pair", {"pairingCode": "synthetic-slam-workspace"}, 201)["token"]
        return process, output

    def request(self, method, route, payload=None, expected=200):
        request = Request(self.base + "/api/v1" + route, method=method,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + self.token} if self.token else {})})
        try:
            response = urlopen(request, context=self.ssl, timeout=8)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            self.assertEqual(expected, response.code, body.decode()[:1000])
            return json.loads(body) if body else None

    def wait(self, callback, seconds=8):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = callback()
            if value:
                return value
            time.sleep(.04)
        self.fail("shared workspace observation deadline")

    def mapping(self):
        self.request("POST", "/mapping/start", {}, 202)
        return self.wait(lambda: (value if (value := self.request("GET", "/state"))["mappingStatus"] == "running" else None))

    def save(self):
        state = self.mapping()
        result = self.request("POST", "/mapping/stop-save", {"session": state["mappingSession"], "name": "Synthetic workspace map"})
        map_id = result["savedSlamMap"]["id"]
        return map_id, self.request("GET", "/slam/maps/" + map_id + "/points")

    def localize(self, map_id, preview):
        runtime = self.request("GET", "/slam/localization/runtime")
        self.request("POST", "/slam/localization/start", {"serviceId": runtime["serviceId"],
            "expectedGeneration": runtime["generation"], "mapId": map_id, "receiptSha256": preview["receiptSha256"]}, 202)
        return self.wait(lambda: (value if (value := self.request("GET", "/slam/localization/runtime"))["phase"] == "running" else None))

    def stop(self, process, output, expected=0):
        process.terminate()
        self.assertEqual(expected, process.wait(timeout=22), process.stderr.read().decode())
        report = json.loads((output / "owner-report.json").read_bytes())
        self.assertEqual(0, report["remainingChildren"])
        self.assertTrue(report["adoptedChildren"]["cleanupComplete"])
        self.assertFalse((output / "control.sock").exists())
        self.assertFalse((output / "localization.sock").exists())
        ActivityGate(self.maps / ".slam-activity.lock").acquire().close()
        return report

    def test_save_localize_shutdown_remap_and_restart_preserves_catalog(self):
        process, output = self.start()
        self.assertTrue(self.request("GET", "/state")["slamWorkspace"])
        map_id, preview = self.save()
        runtime = self.localize(map_id, preview)
        self.wait(lambda: self.request("GET", "/slam/localization")["binding"]["map_id"] == map_id)
        self.assertFalse(self.request("GET", "/state")["mappingControlReady"])
        self.request("POST", "/mapping/start", {}, 409)
        self.request("POST", "/slam/localization/shutdown", {"serviceId": runtime["serviceId"],
            "expectedGeneration": runtime["generation"], "runId": runtime["runId"]}, 202)
        self.wait(lambda: self.request("GET", "/slam/localization/runtime")["cleanupComplete"])
        state = self.mapping()
        self.request("POST", "/mapping/stop-discard", {"session": state["mappingSession"]})
        self.assertEqual("stopped", self.stop(process, output)["result"])
        process, output = self.start()
        self.assertEqual([map_id], [item["id"] for item in self.request("GET", "/slam/maps")["maps"]])
        self.localize(map_id, preview)
        self.assertEqual("stopped", self.stop(process, output)["result"])

    def test_web_index_and_referenced_scripts_are_served_by_the_same_https_owner(self):
        process, output = self.start()
        scripts = []

        class Index(HTMLParser):
            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag == "script" and values.get("src"):
                    scripts.append(values["src"])

        with urlopen(self.base + "/", context=self.ssl, timeout=8) as response:
            self.assertEqual(200, response.code)
            Index().feed(response.read().decode())
        self.assertTrue(scripts)
        for source in scripts:
            self.assertTrue(source.startswith("/assets/"))
            with urlopen(self.base + source, context=self.ssl, timeout=8) as response:
                self.assertEqual(200, response.code)
                self.assertGreater(len(response.read()), 100)
        self.stop(process, output)

    def test_mapping_blocks_localization_before_resolver_or_child_creation(self):
        process, output = self.start()
        self.mapping()
        runtime = self.request("GET", "/slam/localization/runtime")
        self.request("POST", "/slam/localization/start", {"serviceId": runtime["serviceId"],
            "expectedGeneration": 0, "mapId": "slam-" + "b" * 32, "receiptSha256": "c" * 64}, 409)
        self.assertEqual(runtime, self.request("GET", "/slam/localization/runtime"))
        self.assertEqual([], list((output / "r").iterdir()))
        self.stop(process, output)

    def test_api_or_service_peer_failure_closes_active_localization(self):
        for peer in ("apiPid", "servicePid"):
            with self.subTest(peer=peer):
                process, output = self.start()
                map_id, preview = self.save()
                runtime = self.localize(map_id, preview)
                pid_file = output / "r" / runtime["runId"] / "child.pid"
                self.wait(pid_file.exists)
                pid = int(pid_file.read_text())
                os.kill(self.status[peer], signal.SIGKILL if peer == "apiPid" else signal.SIGTERM)
                self.assertEqual(1, process.wait(timeout=22), process.stderr.read().decode())
                self.assertEqual("failed", json.loads((output / "owner-report.json").read_bytes())["result"])
                self.assertFalse(Path("/proc", str(pid)).exists())
                ActivityGate(self.maps / ".slam-activity.lock").acquire().close()

    def test_owner_death_propagates_parent_eof_to_both_interfaces_and_active_worker(self):
        process, output = self.start()
        self.mapping()
        pid = int((self.maps / "synthetic-native.pid").read_text())
        process.kill()
        process.wait(timeout=5)

        def retired():
            for value in (pid, self.status["apiPid"], self.status["servicePid"]):
                try:
                    if Path("/proc", str(value), "stat").read_text().rsplit(")", 1)[1].split()[0] not in ("Z", "X"):
                        return False
                except FileNotFoundError:
                    pass
            return True
        self.wait(retired, seconds=15)
        self.assertFalse((output / "control.sock").exists())
        self.assertFalse((output / "localization.sock").exists())
        ActivityGate(self.maps / ".slam-activity.lock").acquire().close()


def serve_fixture():
    if not (AVAILABLE and NODE and BUNDLE):
        raise ValueError("explicit_linux_compiled_slam_ui_fixture_required")
    case = WorkspaceProcesses()
    case.setUp()
    process = output = None
    try:
        process, output = case.start()
        public_key = subprocess.check_output(["/usr/bin/openssl", "x509", "-in", str(case.cert), "-pubkey", "-noout"])
        der = subprocess.run(["/usr/bin/openssl", "pkey", "-pubin", "-outform", "der"], input=public_key,
                             stdout=subprocess.PIPE, check=True).stdout
        stopping = threading.Event()
        for value in (signal.SIGINT, signal.SIGTERM):
            signal.signal(value, lambda *_: stopping.set())
        print(json.dumps({"classification": "isolated_synthetic_workspace", "url": case.base,
            "spki": base64.b64encode(hashlib.sha256(der).digest()).decode(), "controllerPid": os.getpid()}), flush=True)
        while not stopping.wait(.1):
            if process.poll() is not None:
                raise RuntimeError("synthetic_workspace_owner_exited")
        case.stop(process, output)
    finally:
        case.tearDown()


if __name__ == "__main__":
    if sys.argv[1:] == ["--serve-fixture"]:
        serve_fixture()
    else:
        unittest.main()
