"""On-demand owner tests; synthetic protocols are not robot/localization acceptance."""
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam")]
AVAILABLE = sys.platform == "linux" and os.geteuid() != 0 and all(importlib.util.find_spec(name) is not None
    for name in ("numpy", "scipy"))
if AVAILABLE:
    import localization_service as service
    from lingxi_slam.activity_lease import ActivityGate
    from mapping_service import MappingManager

MAP = "slam-" + "b" * 32
RECEIPT = "c" * 64


@unittest.skipUnless(AVAILABLE, "requires isolated Linux localization dependencies")
class ManagerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lxls-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runs = self.root / "r"
        self.runs.mkdir(mode=0o700)
        self.owners, self.commands, self.exchanges = [], [], []
        self.resolve_entered, self.resolve_release = threading.Event(), threading.Event()
        self.resolve_release.set()
        self.stop_release = threading.Event()
        self.stop_release.set()
        self.resolve_error, self.close_error, self.unclean = False, False, False
        self.now_binding = service.LocalizationBinding("0" * 32, "live", MAP, RECEIPT, "d" * 64, "e" * 64)
        case = self

        class Owner:
            def __init__(self, command, **kwargs):
                self.session = ("%032x" % (len(case.owners) + 1))
                self.phase, self.failure = "new", None
                self.process = None
                self.stopped = False
                case.owners.append(self)

            def start(self):
                self.phase = "running"

            def snapshot(self):
                return {"state": self.phase, "failure": self.failure,
                        "session": self.session, "completedMessages": 1,
                        "processedScans": 0, "estimatedPoses": 0,
                        "cleanupComplete": self.stopped and not case.unclean,
                        "processAlive": not self.stopped, "monitorAlive": not self.stopped,
                        "forcedTermination": False, "exitCode": 0 if self.stopped else None}

            def stop(self):
                if not case.stop_release.wait(3):
                    raise RuntimeError("test_stop_release_deadline")
                self.stopped, self.phase = True, "stopped"
                if case.close_error:
                    raise RuntimeError("private native failure")
                return self.snapshot()

        def resolve(map_id, receipt):
            self.resolve_entered.set()
            if not self.resolve_release.wait(3):
                raise RuntimeError("test_resolver_release_deadline")
            if self.resolve_error:
                raise ValueError("private path and map error")
            return service.SelectedMap(self.root, self.now_binding)

        def command(chosen, output):
            self.commands.append((chosen, output))
            return [sys.executable, "synthetic-only"]

        def exchange(endpoint, value):
            self.exchanges.append(value)
            binding = asdict(self.manager.current["binding"])
            return {"ok": True, "value": {"binding": binding, "sourceFrame": "lidar_chest_front",
                    "localizationReady": False, "navigationReady": False, "state": "unseeded"}}

        self.manager = service.LocalizationManager(self.runs, resolve, command, {}, input_mode="live",
                                                    owner_factory=Owner, exchange=exchange)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.resolve_release.set()
        self.stop_release.set()
        try:
            self.manager.close()
        except RuntimeError:
            if not (self.close_error or self.unclean):
                raise

    def wait(self, condition):
        deadline = time.monotonic() + 5
        while not condition():
            if time.monotonic() >= deadline:
                self.fail("localization service test deadline")
            time.sleep(.01)

    def start(self):
        current = self.manager.start(MAP, RECEIPT, self.manager.runtime()["generation"], self.manager.instance)
        self.wait(lambda: self.manager.runtime()["phase"] == "running")
        return current

    def finish(self):
        current = self.manager.runtime()
        self.manager.shutdown(current["runId"], current["generation"], self.manager.instance)
        self.wait(lambda: not self.manager.current["thread"].is_alive())

    def shared_mapping(self):
        path = self.root / "activity.lock"
        self.manager.activity_gate = ActivityGate(path)
        mapping = MappingManager(self.root, lambda output: [str(output)], {},
                                 owner_factory=self.manager.owner_factory, activity_gate=ActivityGate(path))
        self.addCleanup(mapping.close)
        return mapping

    def assert_activity_busy(self, callback):
        with self.assertRaises(service.ControlError) as caught:
            callback()
        self.assertEqual("slam_activity_busy", caught.exception.code)
        self.assertEqual(409, caught.exception.status)

    def test_shared_gate_blocks_both_service_directions_without_mutating_rejected_generation(self):
        mapping = self.shared_mapping()
        first = mapping.start()
        before = self.manager.runtime()
        self.assert_activity_busy(lambda: self.manager.start(MAP, RECEIPT, 0, self.manager.instance))
        self.assertEqual(before, self.manager.runtime())
        self.assertFalse(self.resolve_entered.is_set())
        self.assertEqual(1, len(self.owners))
        mapping.finish(first["session"], save=False)
        self.start()
        self.assert_activity_busy(mapping.start)
        self.assertEqual(2, len(self.owners))
        self.finish()
        self.assertEqual("running", mapping.start()["phase"])

    def test_simultaneous_service_starts_create_only_one_owner(self):
        mapping = self.shared_mapping()
        barrier, results = threading.Barrier(2), []

        def request(callback):
            barrier.wait(timeout=3)
            try:
                callback()
                results.append("accepted")
            except service.ControlError as error:
                results.append(error.code)

        threads = [threading.Thread(target=request, args=(callback,)) for callback in (
            mapping.start, lambda: self.manager.start(MAP, RECEIPT, 0, self.manager.instance))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(["accepted", "slam_activity_busy"], results)
        self.wait(lambda: len(self.owners) == 1)

    def test_shared_gate_is_held_during_resolution_and_released_on_cancel_before_spawn(self):
        mapping = self.shared_mapping()
        self.resolve_release.clear()
        current = self.manager.start(MAP, RECEIPT, 0, self.manager.instance)
        self.assertTrue(self.resolve_entered.wait(2))
        self.assert_activity_busy(mapping.start)
        self.assertEqual([], self.owners)
        self.manager.shutdown(current["runId"], current["generation"], self.manager.instance)
        self.assert_activity_busy(mapping.start)
        self.resolve_release.set()
        self.wait(lambda: not self.manager.current["thread"].is_alive())
        self.assertEqual("running", mapping.start()["phase"])

    def test_selection_and_factory_failure_release_shared_gate_without_child_launch(self):
        mapping = self.shared_mapping()
        self.resolve_error = True
        self.manager.start(MAP, RECEIPT, 0, self.manager.instance)
        self.wait(lambda: not self.manager.current["thread"].is_alive())
        self.assertEqual([], self.owners)
        current = mapping.start()
        mapping.finish(current["session"], save=False)
        self.resolve_error = False
        with patch.object(self.manager, "owner_factory", side_effect=RuntimeError("synthetic constructor failure")):
            self.manager.start(MAP, RECEIPT, self.manager.runtime()["generation"], self.manager.instance)
            self.wait(lambda: not self.manager.current["thread"].is_alive())
        self.assertEqual("running", mapping.start()["phase"])

    def test_unconfirmed_localization_cleanup_keeps_shared_gate_after_thread_exit(self):
        mapping = self.shared_mapping()
        self.unclean = True
        self.start()
        self.addCleanup(self.manager.current["lease"].close)
        self.finish()
        self.assertFalse(self.manager.runtime()["cleanupComplete"])
        self.assert_activity_busy(mapping.start)

    def test_localization_thread_start_failure_releases_shared_gate(self):
        mapping = self.shared_mapping()
        with patch("localization_service.threading.Thread.start", side_effect=RuntimeError("synthetic thread failure")):
            with self.assertRaises(RuntimeError):
                self.manager.start(MAP, RECEIPT, 0, self.manager.instance)
        self.assertTrue(self.manager.runtime()["cleanupComplete"])
        self.assertEqual([], self.owners)
        self.assertEqual("running", mapping.start()["phase"])

    def test_mapping_factory_and_start_failure_release_shared_gate(self):
        mapping = self.shared_mapping()
        for error in (RuntimeError("synthetic constructor failure"), service.ActivityBusy("synthetic admission failure")):
            with self.subTest(error=type(error).__name__), patch.object(mapping, "owner_factory", side_effect=error):
                with self.assertRaises((RuntimeError, service.ControlError)):
                    mapping.start()
            lease = self.manager.activity_gate.acquire()
            lease.close()

        original = mapping.owner_factory

        def factory(*args, **kwargs):
            owner = original(*args, **kwargs)

            def failed():
                owner.phase, owner.stopped = "failed", True
                raise RuntimeError("synthetic start failure")
            owner.start = failed
            return owner

        with patch.object(mapping, "owner_factory", factory), self.assertRaises(service.ControlError):
            mapping.start()
        self.start()

    def test_idle_start_proxy_shutdown_restart_uses_distinct_process_binding(self):
        self.assertEqual("idle", self.manager.runtime()["phase"])
        first = self.start()
        self.assertFalse(first["localizationReady"])
        state = self.manager.proxy({"action": "state"})
        self.assertEqual(self.owners[0].session, state["binding"]["session"])
        self.assertEqual(1, len(self.commands))
        self.finish()
        self.assertTrue(self.manager.runtime()["cleanupComplete"])
        second = self.start()
        self.assertNotEqual(first["runId"], second["runId"])
        self.assertNotEqual(state["binding"]["session"], self.manager.proxy({"action": "state"})["binding"]["session"])
        with self.assertRaises(service.ControlError) as caught:
            self.manager.proxy({"action": "pause", "binding": state["binding"], "expectedGeneration": 0})
        self.assertEqual(409, caught.exception.status)
        self.assertEqual(2, len(self.exchanges))

    def test_duplicate_start_and_stale_shutdown_cannot_replace_current_slot(self):
        first = self.start()
        for expected in (0, first["generation"]):
            with self.assertRaises(service.ControlError):
                self.manager.start(MAP, RECEIPT, expected, self.manager.instance)
        with self.assertRaises(service.ControlError):
            self.manager.shutdown("f" * 32, first["generation"], self.manager.instance)
        self.assertEqual(1, len(self.owners))
        self.assertEqual(first["runId"], self.manager.runtime()["runId"])

    def test_shutdown_during_selection_prevents_child_launch_and_all_proxying(self):
        self.resolve_release.clear()
        current = self.manager.start(MAP, RECEIPT, 0, self.manager.instance)
        self.assertTrue(self.resolve_entered.wait(2))
        self.manager.shutdown(current["runId"], current["generation"], self.manager.instance)
        with self.assertRaises(service.ControlError):
            self.manager.proxy({"action": "state"})
        self.resolve_release.set()
        self.wait(lambda: not self.manager.current["thread"].is_alive())
        self.assertEqual([], self.owners)
        self.assertEqual("stopped", self.manager.runtime()["phase"])

    def test_stopping_cannot_be_replaced_before_cleanup_is_observed(self):
        current = self.start()
        self.stop_release.clear()
        stopped = self.manager.shutdown(current["runId"], current["generation"], self.manager.instance)
        self.assertEqual("stopping", stopped["phase"])
        self.assertFalse(stopped["cleanupComplete"])
        with self.assertRaises(service.ControlError):
            self.manager.start(MAP, RECEIPT, stopped["generation"], self.manager.instance)
        self.stop_release.set()
        self.wait(lambda: not self.manager.current["thread"].is_alive())
        self.assertTrue(self.manager.runtime()["cleanupComplete"])

    def test_selection_failure_is_fixed_and_can_be_retried_with_new_generation(self):
        self.resolve_error = True
        self.manager.start(MAP, RECEIPT, 0, self.manager.instance)
        self.wait(lambda: not self.manager.current["thread"].is_alive())
        self.assertEqual("map_selection_unavailable", self.manager.runtime()["failureCode"])
        self.assertNotIn("private", json.dumps(self.manager.runtime()))
        self.assertEqual([], self.owners)
        self.resolve_error = False
        self.start()

    def test_health_failure_revokes_queries_without_waiting_for_cleanup(self):
        self.start()
        self.stop_release.clear()
        self.owners[0].phase = "failed"
        self.owners[0].failure = "child_reported_failure"
        with self.assertRaises(service.ControlError):
            self.manager.proxy({"action": "state"})
        self.wait(lambda: self.manager.runtime()["phase"] == "failed")
        self.assertFalse(self.manager.runtime()["cleanupComplete"])
        self.assertEqual("child_reported_failure", self.manager.runtime()["failureCode"])
        self.stop_release.set()
        self.wait(lambda: not self.manager.current["thread"].is_alive())
        self.assertEqual("child_reported_failure", self.manager.runtime()["failureCode"])

    def test_uncertain_cleanup_blocks_replacement_even_after_thread_exits(self):
        self.unclean = True
        self.start()
        self.finish()
        self.assertFalse(self.manager.runtime()["cleanupComplete"])
        self.assertEqual("failed", self.manager.runtime()["phase"])
        with self.assertRaises(service.ControlError):
            self.manager.start(MAP, RECEIPT, self.manager.runtime()["generation"], self.manager.instance)

    def test_cleanup_exception_does_not_report_success_or_allow_replacement(self):
        self.close_error = True
        self.start()
        self.finish()
        self.assertFalse(self.manager.runtime()["cleanupComplete"])
        with self.assertRaises(service.ControlError):
            self.manager.start(MAP, RECEIPT, self.manager.runtime()["generation"], self.manager.instance)

    def test_late_response_cannot_republish_after_shutdown(self):
        self.start()
        original = self.manager.exchange

        def late(endpoint, value):
            response = original(endpoint, value)
            state = self.manager.runtime()
            self.manager.shutdown(state["runId"], state["generation"], self.manager.instance)
            return response
        self.manager.exchange = late
        with self.assertRaises(service.ControlError) as caught:
            self.manager.proxy({"action": "state"})
        self.assertEqual(409, caught.exception.status)

    def test_missing_acknowledgement_is_not_retried(self):
        self.start()
        calls = []

        def lost(*_):
            calls.append(True)
            raise TimeoutError("private internal details")
        self.manager.exchange = lost
        with self.assertRaises(service.ControlError) as caught:
            self.manager.proxy({"action": "pause", "binding": asdict(self.manager.current["binding"]),
                                "expectedGeneration": 0})
        self.assertEqual(503, caught.exception.status)
        self.assertEqual([True], calls)

    def test_wrong_binding_pose_frame_or_readiness_cannot_escape_proxy(self):
        self.start()
        original = self.manager.exchange
        for field, changed in (("binding", {}), ("sourceFrame", "IMU"),
                               ("localizationReady", True), ("navigationReady", True)):
            def mismatch(endpoint, value):
                response = original(endpoint, value)
                response["value"][field] = changed
                return response
            self.manager.exchange = mismatch
            with self.subTest(field=field), self.assertRaises(service.ControlError) as caught:
                self.manager.proxy({"action": "state"})
            self.assertEqual(503, caught.exception.status)
        self.assertEqual(4, len(self.exchanges))

    def test_invalid_dispatch_and_generations_do_not_create_processes(self):
        for value in (None, {}, {"action": "start", "path": "/tmp/map"},
                      {"action": "runtime", "extra": True}, {"action": "move"}):
            with self.subTest(value=value), self.assertRaises(service.ControlError):
                service.dispatch(self.manager, value)
        for generation in (True, -1, 1.5, "0", 2**53):
            with self.subTest(generation=generation), self.assertRaises(service.ControlError):
                self.manager.start(MAP, RECEIPT, generation, self.manager.instance)
        for map_id, receipt in (("../map", RECEIPT), (MAP, "x"), (None, RECEIPT)):
            with self.assertRaises(service.ControlError):
                self.manager.start(map_id, receipt, 0, self.manager.instance)
        self.assertEqual([], self.owners)
        self.assertEqual("idle", self.manager.runtime()["phase"])

    def test_runtime_is_owned_copy_and_close_is_terminal(self):
        current = self.start()
        current["selectedMap"]["mapId"] = "modified"
        self.assertEqual(MAP, self.manager.runtime()["selectedMap"]["mapId"])
        self.manager.close()
        self.assertTrue(self.manager.runtime()["closed"])
        with self.assertRaises(service.ControlError):
            self.manager.start(MAP, RECEIPT, self.manager.runtime()["generation"], self.manager.instance)

    def test_service_restart_rejects_old_start_even_when_both_generations_are_zero(self):
        old = self.manager
        request = {"action": "start", "mapId": MAP, "receiptSha256": RECEIPT,
                   "expectedGeneration": 0, "serviceId": old.instance}
        old.close()
        self.manager = service.LocalizationManager(self.runs, old.resolve_map, old.command_factory, {},
            input_mode="live", owner_factory=old.owner_factory, exchange=old.exchange)
        self.assertNotEqual(old.instance, self.manager.instance)
        with self.assertRaises(service.ControlError) as caught:
            service.dispatch(self.manager, request)
        self.assertEqual(409, caught.exception.status)
        self.assertEqual("idle", self.manager.runtime()["phase"])
        self.assertEqual([], self.owners)
        self.assertEqual("starting", service.dispatch(self.manager,
            {**request, "serviceId": self.manager.instance})["phase"])

    def test_control_requires_well_formed_service_identity(self):
        for identity in (None, "", "not-an-id", True):
            with self.subTest(identity=identity), self.assertRaises(service.ControlError) as caught:
                self.manager.start(MAP, RECEIPT, 0, identity)
            self.assertEqual(400, caught.exception.status)
        with self.assertRaises(service.ControlError):
            service.dispatch(self.manager, {"action": "start", "mapId": MAP,
                "receiptSha256": RECEIPT, "expectedGeneration": 0})
        self.assertEqual([], self.owners)

    def test_real_supervisor_and_child_socket_can_restart_without_overlap(self):
        self.manager.close()
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        fixture = ROOT / "tools/tests/fixtures/managed_localization_child.py"
        self.manager = service.LocalizationManager(self.runs,
            lambda *_: service.SelectedMap(self.root, self.now_binding),
            lambda chosen, output: [sys.executable, "-B", str(fixture), "native", "--output", str(output),
                                    "--scenario", "live-normal"], environment, input_mode="live")
        bindings = []
        for _ in range(2):
            self.manager.start(MAP, RECEIPT, self.manager.runtime()["generation"], self.manager.instance)
            self.wait(lambda: self.manager.runtime()["phase"] in ("running", "failed"))
            self.assertEqual("running", self.manager.runtime()["phase"])
            state = self.manager.proxy({"action": "state"})
            bindings.append(state["binding"])
            paused = self.manager.proxy({"action": "pause", "binding": state["binding"],
                                         "expectedGeneration": state["generation"]})
            self.assertEqual("paused", paused["state"])
            self.finish()
            self.assertEqual("stopped", self.manager.runtime()["phase"])
            self.assertTrue(self.manager.runtime()["cleanupComplete"])
            owner = self.manager.current["owner"].snapshot()
            self.assertFalse(owner["processAlive"] or owner["monitorAlive"] or owner["forcedTermination"])
        self.assertNotEqual(bindings[0]["session"], bindings[1]["session"])

    def test_real_service_socket_requires_exact_contract_and_closes_owned_session(self):
        endpoint = self.root / "s"
        server = service.LocalizationServiceServer(endpoint, self.manager)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        try:
            self.assertEqual(0o600, endpoint.stat().st_mode & 0o777)

            def send(value):
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(2)
                    client.connect(str(endpoint))
                    client.sendall((json.dumps(value) + "\n").encode())
                    return json.loads(client.makefile("rb").readline())

            self.assertEqual("idle", send({"action": "runtime"})["value"]["phase"])
            self.assertEqual(400, send({"action": "start", "path": "/arbitrary"})["status"])
            result = send({"action": "start", "mapId": MAP, "receiptSha256": RECEIPT,
                           "expectedGeneration": 0, "serviceId": self.manager.instance})
            self.assertTrue(result["ok"])
            self.wait(lambda: self.manager.runtime()["phase"] == "running")
            self.assertTrue(send({"action": "state"})["ok"])
        finally:
            server.shutdown()
            thread.join(2)
            server.server_close()
        self.assertFalse(endpoint.exists())
        self.assertTrue(self.manager.runtime()["cleanupComplete"])
        self.assertTrue(self.manager.runtime()["closed"])

    def test_actual_service_parent_eof_stops_owned_process_and_removes_socket(self):
        self.check_service_exit(terminate=False)

    def test_actual_service_sigterm_stops_owned_process_and_removes_socket(self):
        self.check_service_exit(terminate=True)

    def check_service_exit(self, *, terminate):
        fixture = ROOT / "tools/tests/fixtures/localization_service_owner.py"
        endpoint = self.root / "s"
        process = subprocess.Popen([sys.executable, "-B", str(fixture), str(self.runs), str(endpoint)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            import selectors
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(5))
                status = json.loads(process.stdout.readline())
            self.assertEqual("lingxi-localization", status["service"])
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(2)
                client.connect(str(endpoint))
                client.sendall(b'{"action":"runtime"}\n')
                runtime = json.loads(client.makefile("rb").readline())["value"]
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(2)
                client.connect(str(endpoint))
                client.sendall(json.dumps({"action": "start", "mapId": MAP,
                    "receiptSha256": RECEIPT, "expectedGeneration": 0,
                    "serviceId": runtime["serviceId"]}).encode() + b"\n")
                self.assertTrue(json.loads(client.makefile("rb").readline())["ok"])
            pid_files = []

            def launched():
                pid_files[:] = list(self.runs.glob("*/child.pid"))
                return bool(pid_files)
            self.wait(launched)
            pid = int(pid_files[0].read_text())
            if terminate:
                process.terminate()
            else:
                process.stdin.close()
            self.assertEqual(0, process.wait(timeout=12), process.stderr.read().decode())
            self.assertFalse(endpoint.exists())
            self.assertFalse(Path("/proc", str(pid)).exists())
            self.assertTrue(json.loads((self.runs / "service-final.json").read_bytes())["cleanupComplete"])
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=12)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


@unittest.skipUnless(AVAILABLE, "requires isolated Linux localization dependencies")
class ResolverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lxls-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.provenance = {"schemaVersion": 1, "profileSha256": "e" * 64,
                           "timingHypothesis": "header_is_scan_start",
                           "configurationSha256": {"lidar.json": "f" * 64}}
        self.receipt = {"schemaVersion": 2, "session": "b" * 32, "inputMode": "recorded",
                        "freshProcessReloadPassed": True, "savedPointsSha256": "d" * 64,
                        "sensorProvenance": self.provenance}
        self.folder = self.root / "synthetic-only"
        self.folder.mkdir()
        self.resolver = service.SavedMapResolver(self.root, "e" * 64, "live", self.provenance)

    def write(self):
        payload = json.dumps(self.receipt).encode()
        (self.folder / "map-ready.json").write_bytes(payload)
        return service.digest(payload)

    def test_selection_resolves_receipt_identity_not_directory_name(self):
        result = self.resolver(MAP, self.write())
        self.assertEqual(self.folder, result.directory)
        self.assertEqual("live", result.binding.input_mode)
        self.assertEqual("d" * 64, result.binding.points_sha256)
        # Resolver admission does not claim to load or validate nonexistent native map files.
        self.assertFalse((self.folder / "map").exists())

    def test_changed_duplicate_and_missing_receipts_are_not_selected(self):
        digest = self.write()
        for map_id, receipt in (("slam-" + "a" * 32, digest), (MAP, "a" * 64)):
            with self.assertRaises(ValueError):
                self.resolver(map_id, receipt)
        other = self.root / "duplicate"
        other.mkdir()
        (other / "map-ready.json").write_bytes((self.folder / "map-ready.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "unambiguous"):
            self.resolver(MAP, digest)

    def test_full_async_map_resolves_without_loading_or_claiming_readiness(self):
        self.receipt.update(schemaVersion=3, mappingEngine="glim-full-async-v1")
        receipt_sha256 = self.write()
        result = self.resolver(MAP, receipt_sha256)
        self.assertEqual(self.folder, result.directory)
        self.assertEqual(MAP, result.binding.map_id)
        self.assertEqual(receipt_sha256, result.binding.receipt_sha256)
        self.assertEqual("d" * 64, result.binding.points_sha256)
        self.assertFalse((self.folder / "map").exists())

    def test_version_and_mapping_engine_must_match_exactly(self):
        original = self.receipt.copy()
        for changes in ({"schemaVersion": 2.0}, {"schemaVersion": 3.0},
                        {"schemaVersion": True}, {"schemaVersion": "3"},
                        {"schemaVersion": 3},
                        {"schemaVersion": 3, "mappingEngine": None},
                        {"schemaVersion": 3, "mappingEngine": "unknown"},
                        {"schemaVersion": 2, "mappingEngine": "glim-full-async-v1"},
                        {"schemaVersion": 2, "mappingEngine": None}):
            self.receipt = {**original, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.resolver(MAP, self.write())

    def test_legacy_unreloaded_or_different_provenance_requires_new_valid_map(self):
        original = json.loads(json.dumps(self.receipt))
        for version in ({}, {"schemaVersion": 3, "mappingEngine": "glim-full-async-v1"}):
            for changes in ({"schemaVersion": 1}, {"freshProcessReloadPassed": False},
                            {"sensorProvenance": {**self.provenance, "timingHypothesis": "header_is_scan_end"}},
                            {"savedPointsSha256": "invalid"}):
                self.receipt = {**original, **version, **changes}
                with self.subTest(version=version, changes=changes), self.assertRaises(ValueError):
                    self.resolver(MAP, self.write())


if __name__ == "__main__":
    unittest.main()
