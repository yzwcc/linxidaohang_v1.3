"""Mapping control contracts with isolated synthetic owners; no native or robot work."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-runtime/python"))
from mapping_service import ControlError, MappingManager, MappingSocketServer, dispatch


class MappingControl(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.maps = Path(temporary.name).resolve()
        self.owners = []
        parent = self

        class SyntheticOwner:
            def __init__(self, command, **kwargs):
                self.output = Path(command[0])
                self.session = "bbaaccdd00112233445566778899aabb"
                self.process, self.stops = None, 0
                self.state, self.cleaned = "new", False
                self.save_receipt, self.start_error, self.terminal_error = True, False, False
                self.entered, self.release = threading.Event(), threading.Event()
                self.release.set()
                parent.owners.append(self)

            def start(self):
                if self.start_error:
                    self.state, self.cleaned = "failed", True
                    raise RuntimeError("synthetic_start_failed")
                self.state = "running"

            def snapshot(self):
                return {
                    "session": self.session, "state": self.state, "cleanupComplete": self.cleaned,
                    "completedMessages": 12, "processedScans": 2, "estimatedPoses": 1,
                    "exitCode": 0, "forcedTermination": self.terminal_error,
                    "processAlive": False, "monitorAlive": False,
                }

            def stop(self):
                self.stops += 1
                self.entered.set()
                if not self.release.wait(3):
                    raise RuntimeError("synthetic_stop_not_released")
                self.state, self.cleaned = "stopped", True
                intent = self.output.parent / (self.output.name + ".save-request.json")
                if intent.exists() and self.save_receipt:
                    value = json.loads(intent.read_text())
                    self.output.mkdir(exist_ok=True)
                    (self.output / "map-ready.json").write_text(json.dumps({
                        **value, "inputMode": "recorded", "freshProcessReloadPassed": True,
                        "navigationReady": False, "localizationReady": False,
                        "geometricAccuracyValidated": False,
                    }))
                return self.snapshot()

        self.owner_type = SyntheticOwner
        self.manager = MappingManager(
            self.maps, lambda output: [str(output)], {}, owner_factory=SyntheticOwner)
        self.addCleanup(self.manager.close)

    def assert_code(self, code, callback):
        with self.assertRaises(ControlError) as raised:
            callback()
        self.assertEqual(code, raised.exception.code)
        return raised.exception

    def test_start_state_and_named_save_are_owned_and_idempotent(self):
        self.assertEqual("idle", self.manager.state()["phase"])
        started = self.manager.start()
        self.assertEqual("running", started["phase"])
        self.assertTrue(started["mapping"])
        owner = self.owners[0]
        self.assert_code("mapping_busy", self.manager.start)
        result = self.manager.finish(owner.session, save=True, name=" Synthetic map ")
        self.assertEqual({"session": owner.session, "saved": True, "confirmed": True,
                          "mapId": "slam-" + owner.session}, result)
        self.assertEqual(result, self.manager.finish(owner.session, save=True, name="Synthetic map"))
        self.assertEqual(1, owner.stops)
        self.assertFalse(self.manager.state()["mapping"])
        self.assert_code("mapping_already_finished",
                         lambda: self.manager.finish(owner.session, save=False))

    def test_discard_does_not_create_a_receipt_and_allows_a_new_session(self):
        self.manager.start()
        owner = self.owners[0]
        self.assertFalse(self.manager.finish(owner.session, save=False)["saved"])
        self.assertFalse((owner.output / "map-ready.json").exists())
        self.manager.start()
        self.assertEqual(2, len(self.owners))
        self.assertNotEqual(self.owners[0].output, self.owners[1].output)

    def test_optional_points_follow_output_identity_and_stop_advertising_on_finish(self):
        self.manager.points_enabled = True
        started = self.manager.start()
        owner = self.owners[0]
        self.assertNotEqual(owner.session, owner.output.name)
        self.assertEqual({"socket": owner.output.name + ".points.sock"}, started["pointStream"])
        self.manager.finish(owner.session, save=False)
        self.assertNotIn("pointStream", self.manager.state())

    def test_stale_session_and_invalid_names_do_not_stop_current_owner(self):
        self.manager.start()
        owner = self.owners[0]
        self.assert_code("mapping_session_changed",
                         lambda: self.manager.finish("0" * 32, save=False))
        for session in ("invalid", None, 3):
            self.assert_code("invalid_mapping_session",
                             lambda: self.manager.finish(session, save=False))
        for name in ("", " ", "x" * 65, None, 3):
            self.assert_code("invalid_mapping_save",
                             lambda: self.manager.finish(owner.session, save=True, name=name))
        self.assertEqual(0, owner.stops)

    def test_missing_receipt_and_unconfirmed_cleanup_never_confirm_save(self):
        for kind in ("receipt", "cleanup"):
            with self.subTest(kind=kind):
                self.manager.start()
                owner = self.owners[-1]
                owner.save_receipt = kind != "receipt"
                owner.terminal_error = kind == "cleanup"
                self.assert_code("mapping_save_unconfirmed" if kind == "receipt" else "mapping_stop_unconfirmed",
                                 lambda: self.manager.finish(owner.session, save=True, name="Synthetic map"))
                self.assertEqual("failed", self.manager.state()["phase"])
                self.assertIsNone(self.manager.current["result"])
                self.assertFalse((owner.output / "map-ready.json").exists())
                if kind == "cleanup":
                    self.assertTrue((owner.output / "map-ready.unconfirmed").is_file())

    def test_failed_intent_write_stops_admission_and_cleans_owner(self):
        self.manager.start()
        owner = self.owners[0]
        (self.maps / (owner.output.name + ".save-request.json")).write_text("invalid synthetic fixture")
        # The fake owner must not interpret malformed input as an actual save.
        owner.save_receipt = False
        self.assert_code("mapping_finish_unconfirmed",
                         lambda: self.manager.finish(owner.session, save=True, name="Synthetic map"))
        self.assertEqual(1, owner.stops)
        self.assertEqual("failed", self.manager.state()["phase"])
        self.assertTrue(owner.cleaned)

    def test_concurrent_finish_is_rejected_without_duplicating_stop(self):
        self.manager.start()
        owner = self.owners[0]
        owner.release.clear()
        results = []
        thread = threading.Thread(target=lambda: results.append(self.manager.finish(owner.session, save=False)))
        thread.start()
        try:
            self.assertTrue(owner.entered.wait(1))
            self.assert_code("mapping_busy", lambda: self.manager.finish(owner.session, save=False))
            self.assert_code("mapping_busy", self.manager.start)
            self.assertEqual("stopping", self.manager.state()["phase"])
        finally:
            owner.release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(results))
        self.assertEqual(1, owner.stops)

    def test_start_failure_and_service_close_do_not_reopen(self):
        def broken(*args, **kwargs):
            owner = self.owner_type(*args, **kwargs)
            owner.start_error = True
            return owner
        self.manager.owner_factory = broken
        self.assert_code("mapping_start_failed", self.manager.start)
        self.assertEqual("failed", self.manager.state()["phase"])
        self.manager.close()
        self.assertEqual(503, self.assert_code("mapping_service_closed", self.manager.start).status)

    def test_dispatch_rejects_unknown_fields_and_requires_explicit_session(self):
        for request in (None, [], {}, {"action": 1}, {"action": "unknown"},
                        {"action": "start", "name": "unaccepted"},
                        {"action": "discard"}, {"action": "save", "session": "bad", "name": "map"}):
            with self.subTest(request=request), self.assertRaises(ControlError):
                dispatch(self.manager, request)
        self.assertEqual("idle", dispatch(self.manager, {"action": "state"})["phase"])
        self.assertEqual([], self.owners)

    @unittest.skipUnless(sys.platform == "linux" and os.geteuid() != 0,
                         "requires ordinary-user Linux Unix socket")
    def test_actual_private_socket_serves_state_and_removes_endpoint(self):
        os.chmod(self.maps, 0o700)
        endpoint = self.maps / "control.sock"
        with MappingSocketServer(endpoint, self.manager) as server:
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
            thread.start()
            try:
                with socket.socket(socket.AF_UNIX) as connection:
                    connection.settimeout(2.)
                    connection.connect(str(endpoint))
                    connection.sendall(b'{"action":"state"}\n')
                    result = json.loads(connection.makefile("rb").readline(8192))
                self.assertTrue(result["ok"])
                self.assertEqual("idle", result["value"]["phase"])
                self.assertEqual(0o600, endpoint.stat().st_mode & 0o777)
            finally:
                server.shutdown()
                thread.join(2.)
                self.assertFalse(thread.is_alive())
        self.assertFalse(endpoint.exists())


class PublishedControlEvidence(unittest.TestCase):
    def test_native_service_evidence_keeps_current_sources_and_explicit_boundaries(self):
        result = json.loads((ROOT / "docs/slam-mapping-control-evidence.json").read_text())
        self.assertEqual("recorded_native_service_api_save_discard_verified", result["result"])
        self.assertEqual("recorded", result["inputMode"])
        self.assertEqual(0, result["serviceExitCode"])
        self.assertTrue(result["socketRemoved"])
        self.assertTrue(all(value is False for value in result["boundaries"].values()))
        self.assertEqual([True, False], [item["save"] for item in result["runs"]])
        for item in result["runs"]:
            self.assertEqual(4144, item["completedMessages"])
        self.assertEqual(result["runs"][0]["counts"]["points"], result["runs"][0]["previewPoints"])
        self.assertEqual("2026-10-30", result["date"])
        self.assertEqual(2, result["pointStream"]["browserReconnects"])
        self.assertTrue(all(result["pointStream"].values()))
        for item in result["runs"]:
            self.assertGreater(item["points"]["uniqueFrames"], 3)
            self.assertGreater(item["points"]["maximumPoints"], 0)
            self.assertTrue(item["browserReconnected"])
            self.assertTrue(item["unavailableAfterStop"])
        for child in result["children"]:
            self.assertEqual({"sent": 164, "replaced": 0, "failed": False,
                              "threadAlive": False}, child["points"])
        for name, expected in result["sourceSha256"].items():
            self.assertEqual(expected, hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), name)
        text = json.dumps(result)
        for private in ('"/home/', '"/mnt/', '"session":', '"password":', '"points": ['):
            self.assertNotIn(private, text)


if __name__ == "__main__":
    unittest.main()
