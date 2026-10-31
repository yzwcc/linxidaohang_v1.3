"""Saved-map input verification, real local control and worker ownership."""
from copy import deepcopy
from dataclasses import asdict, replace
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam"),
                str(ROOT / "tools"), str(ROOT / "tools/tests")]
AVAILABLE = sys.platform == "linux" and all(importlib.util.find_spec(name) is not None
    for name in ("numpy", "scipy", "small_gicp"))
if AVAILABLE:
    import numpy as np
    import controlled_localization as runtime
    import localization_control as control
    from lingxi_slam.localization_session import LocalizationBinding
    from lingxi_slam.point_output import PointPacket
    from test_slam_seeded_localization import room, pose


@unittest.skipUnless(AVAILABLE, "requires isolated Linux localization dependencies")
class MapInputs(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.saved = self.root / "saved"
        self.saved.mkdir()
        native = self.saved / "map"
        native.mkdir()
        (native / "000000").mkdir()
        (native / "graph.txt").write_text(
            "num_submaps: 1\nnum_all_frames: 3\nnum_matching_cost_factors: 0\n")
        for name in ("graph.bin", "values.bin"):
            (native / name).write_bytes(b"synthetic-map-structure-only")
        for name in ("traj_lidar.txt", "traj_imu.txt", "odom_lidar.txt", "odom_imu.txt"):
            np.savetxt(native / name, [[1, 0, 0, 0, 0, 0, 0, 1],
                                      [2, 0, 0, 0, 0, 0, 0, 1],
                                      [3, 0, 0, 0, 0, 0, 0, 1]])
        self.points = room()
        self.point_file = self.saved / "private-saved-points.f64"
        self.point_file.write_bytes(self.points.astype("<f8").tobytes())
        self.profile = self.root / "private-profile.json"
        self.profile.write_text('{"synthetic":true}')
        self.receipt_file = self.saved / "map-ready.json"
        self.receipt = {
            "schemaVersion": 1, "session": "1" * 32, "inputMode": "recorded",
            "counts": {"points": len(self.points), "savedFrames": 3,
                       "finalizedFrames": 3, "submaps": 1},
            "mapInventory": control.inventory_map(native),
            "savedPointsSha256": control.digest(self.point_file.read_bytes()),
            "freshProcessReloadPassed": True, "navigationReady": False,
            "localizationReady": False, "geometricAccuracyValidated": False,
        }
        self.write_receipt()

    def write_receipt(self):
        self.receipt_file.write_text(json.dumps(self.receipt))

    def load(self, **kwargs):
        options = {
            "directory": self.saved, "map_id": "slam-" + "1" * 32,
            "receipt_sha256": control.digest(self.receipt_file.read_bytes()),
            "profile": self.profile, "profile_sha256": control.digest(self.profile.read_bytes()),
            "session": "2" * 32, "input_mode": "recorded",
        }
        return control.load_map(**{**options, **kwargs})

    def test_v2_receipt_requires_matching_current_sensor_provenance(self):
        provenance = {
            "schemaVersion": 1, "profileSha256": control.digest(self.profile.read_bytes()),
            "timingHypothesis": "header_is_scan_start",
            "configurationSha256": {"lidar.json": "a" * 64},
        }
        self.receipt.update(schemaVersion=2, sensorProvenance=provenance)
        self.write_receipt()
        binding, _ = self.load(sensor_provenance=provenance)
        self.assertEqual("slam-" + "1" * 32, binding.map_id)
        for current in (None, {**provenance, "timingHypothesis": "header_is_scan_end"},
                        {**provenance, "profileSha256": "b" * 64},
                        {**provenance, "configurationSha256": {"lidar.json": "b" * 64}}):
            with self.subTest(current=current), self.assertRaises(ValueError):
                self.load(sensor_provenance=current)
        self.receipt["sensorProvenance"] = {**provenance, "profileSha256": "b" * 64}
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, "matching_saved_map_sensor_provenance_required"):
            self.load(sensor_provenance=self.receipt["sensorProvenance"])

    def test_v1_receipt_with_unproven_sensor_field_is_rejected(self):
        self.receipt["sensorProvenance"] = {"schemaVersion": 1}
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, "versioned_saved_map_sensor_provenance_required"):
            self.load()

    def configure_full_map(self):
        from refine_saved_map import inventory
        native = self.saved / "map"
        for name in ("000001", "000002"):
            (native / name).mkdir()
        (native / "graph.txt").write_text(
            "num_submaps: 3\nnum_all_frames: 3\nnum_matching_cost_factors: 2\n"
            "matching_cost vgicp 0 1\nmatching_cost gicp 0 2\n")
        provenance = {"schemaVersion": 1, "profileSha256": control.digest(self.profile.read_bytes()),
                      "timingHypothesis": "header_is_scan_end", "configurationSha256": {"lidar.json": "a" * 64}}
        self.receipt["counts"]["submaps"] = 3
        self.receipt.update(schemaVersion=3, mappingEngine="glim-full-async-v1",
                            sensorProvenance=provenance, mapInventory=inventory(native))
        self.write_receipt()
        return provenance

    def test_v3_full_map_retains_matching_graph_and_requires_engine_and_provenance(self):
        provenance = self.configure_full_map()
        _, points = self.load(sensor_provenance=provenance)
        self.assertEqual(len(self.points), len(points))
        self.assertFalse(points.flags.writeable)
        self.assertEqual(1, self.receipt["mapInventory"]["nonAdjacentMatchingPairs"])
        with self.assertRaises(ValueError):
            self.load()
        for version, engine in ((3, None), (3, "other"), (2, "glim-full-async-v1")):
            self.receipt.update(schemaVersion=version, mappingEngine=engine)
            self.write_receipt()
            with self.subTest(version=version, engine=engine), self.assertRaises(ValueError):
                self.load(sensor_provenance=provenance)

    def test_load_owns_immutable_points_and_binds_actual_bytes(self):
        binding, points = self.load()
        np.testing.assert_array_equal(self.points, points)
        self.assertFalse(points.flags.writeable)
        self.assertTrue(points.flags.owndata)
        self.assertEqual(self.receipt["savedPointsSha256"], binding.points_sha256)
        self.point_file.write_bytes(b"changed")
        np.testing.assert_array_equal(self.points, points)

    def test_observed_map_load_requires_independent_archive_validation(self):
        provenance = self.configure_full_map()
        self.receipt.update(schemaVersion=3, mappingEngine="glim-full-async-v1",
                            observationImplementationSha256="d" * 64)
        self.write_receipt()
        # No binding/archive exists: the real verifier must reject it.
        with self.assertRaisesRegex(ValueError, "bounded_observation_binding_required"):
            self.load(sensor_provenance=provenance)
        with patch("lingxi_slam.observation_binding.verify_observations") as verify:
            self.load(sensor_provenance=provenance)
        verify.assert_called_once_with(self.saved, self.receipt["mapInventory"], provenance, "d" * 64)
        with patch("lingxi_slam.observation_binding.verify_observations", side_effect=ValueError("changed_archive")):
            with self.assertRaisesRegex(ValueError, "changed_archive"):
                self.load(sensor_provenance=provenance)

    def test_wrong_receipt_profile_map_identity_or_mode_is_rejected(self):
        for options in (
                {"receipt_sha256": "3" * 64}, {"profile_sha256": "3" * 64},
                {"map_id": "slam-" + "3" * 32}, {"input_mode": "synthetic"},
                {"input_mode": "unknown"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.load(**options)

    def test_saved_map_source_mode_is_independent_from_localization_input_mode(self):
        for saved_mode in ("recorded", "live"):
            self.receipt["inputMode"] = saved_mode
            self.write_receipt()
            for current_mode in ("recorded", "live"):
                with self.subTest(saved=saved_mode, current=current_mode):
                    binding, _ = self.load(input_mode=current_mode)
                    self.assertEqual(current_mode, binding.input_mode)
        for mode in ("synthetic", "unknown", None):
            self.receipt["inputMode"] = mode
            self.write_receipt()
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.load(input_mode="live")

    def test_counts_reload_and_readiness_cannot_be_forged(self):
        original = deepcopy(self.receipt)
        for kind in ("boolCount", "missingFrame", "submaps", "reload", "ready"):
            self.receipt = deepcopy(original)
            if kind == "boolCount":
                self.receipt["counts"]["points"] = True
            elif kind == "missingFrame":
                self.receipt["counts"]["finalizedFrames"] -= 1
            elif kind == "submaps":
                self.receipt["counts"]["submaps"] += 1
            elif kind == "reload":
                self.receipt["freshProcessReloadPassed"] = False
            else:
                self.receipt["localizationReady"] = True
            self.write_receipt()
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.load()

    def test_changed_native_inventory_and_point_bytes_are_rejected(self):
        (self.saved / "map/graph.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "unchanged_saved"):
            self.load()
        self.receipt["mapInventory"] = control.inventory_map(self.saved / "map")
        self.write_receipt()
        self.point_file.write_bytes(b"\0" * self.point_file.stat().st_size)
        with self.assertRaisesRegex(ValueError, "unchanged_saved"):
            self.load()

    def test_nonfinite_points_even_with_matching_receipt_are_rejected(self):
        self.points[0, 0] = float("nan")
        self.point_file.write_bytes(self.points.astype("<f8").tobytes())
        self.receipt["savedPointsSha256"] = control.digest(self.point_file.read_bytes())
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, "finite_saved"):
            self.load()

    def test_indirect_input_and_map_symlink_are_rejected(self):
        other = self.root / "link"
        other.symlink_to(self.saved, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.load(directory=other)
        (self.saved / "map/indirect").symlink_to(self.profile)
        with self.assertRaises(ValueError):
            self.load()

    def test_duplicate_receipt_fields_and_mutation_during_load_are_rejected(self):
        data = self.receipt_file.read_text()
        self.receipt_file.write_text('{"schemaVersion":1,' + data[1:])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.load()
        self.write_receipt()
        inventory = self.receipt["mapInventory"]
        with patch.object(control, "inventory_map", side_effect=[inventory, {}]):
            with self.assertRaisesRegex(ValueError, "changed_during"):
                self.load()


@unittest.skipUnless(AVAILABLE, "requires isolated Linux localization dependencies")
class ControlAndOwnership(unittest.TestCase):
    def setUp(self):
        self.world, self.pose = room(), pose()
        binding = LocalizationBinding(
            "2" * 32, "recorded", "slam-" + "1" * 32, "3" * 64, "4" * 64, "5" * 64)
        self.session = control.make_session(binding, self.world)

    def test_wrong_thread_close_does_not_poison_actual_owner(self):
        calls = []

        class Native:
            def imu(self, *args):
                calls.append("imu")

            def close(self):
                calls.append("close")

        native = control.LocalizedNative(self.session, lambda accept: Native())
        errors = []

        def other():
            for method in (native.close, native.imu):
                try:
                    method()
                except RuntimeError as error:
                    errors.append(str(error))
        thread = threading.Thread(target=other)
        thread.start()
        thread.join(2)
        self.assertEqual(2, len(errors))
        self.assertFalse(native.closed)
        self.assertFalse(native.failed)
        native.imu()
        native.close()
        native.close()
        self.assertEqual(["imu", "close"], calls)
        self.assertTrue(self.session.state()["cleanupComplete"])

    def test_runtime_stop_is_idempotent_and_cannot_be_reopened_by_late_seed_or_points(self):
        self.session.open_worker()
        packet = PointPacket(1, 1000.1, len(self.world), self.world, self.pose)
        self.session.observe(packet, session="2" * 32, input_mode="recorded",
                             received_at=control.time.monotonic())
        before = self.session.state()
        self.session.stop_admission()
        stopped = self.session.state()
        self.assertEqual(before["generation"] + 1, stopped["generation"])
        self.assertIsNone(stopped["mapFromSensor"])
        self.session.stop_admission()
        self.assertEqual(stopped["generation"], self.session.state()["generation"])
        with self.assertRaises(control.LocalizationControlError):
            self.session.request("seed", stopped["binding"], stopped["generation"],
                                 seed=self.pose, reference_frame=1)
        self.session.observe(replace(packet, native_frame_id=2, algorithm_stamp=1000.2),
                             session="2" * 32, input_mode="recorded",
                             received_at=control.time.monotonic())
        self.assertEqual("stopped", self.session.state()["state"])
        self.assertIsNone(self.session.state()["mapFromSensor"])
        self.session.close_worker()
        self.assertTrue(self.session.state()["cleanupComplete"])

    def test_failed_point_factory_and_failed_native_close_clean_localization(self):
        with self.assertRaisesRegex(RuntimeError, "injected"):
            control.LocalizedNative(self.session, lambda _: (_ for _ in ()).throw(RuntimeError("injected")))
        self.assertEqual("failed", self.session.state()["state"])
        self.assertTrue(self.session.state()["cleanupComplete"])
        self.setUp()

        class BrokenClose:
            def close(self):
                raise RuntimeError("injected close")
        native = control.LocalizedNative(self.session, lambda _: BrokenClose())
        with self.assertRaisesRegex(RuntimeError, "injected close"):
            native.close()
        self.assertEqual("failed", self.session.state()["state"])
        self.assertTrue(self.session.state()["cleanupComplete"])

    def test_real_control_socket_seed_pause_stop_and_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            endpoint = Path(temporary) / "localization.sock"
            self.session.open_worker()
            try:
                with control.LocalizationControlServer(endpoint, self.session) as server:
                    server.start()
                    self.assertEqual(0o600, endpoint.stat().st_mode & 0o777)

                    def send(value):
                        with socket.socket(socket.AF_UNIX) as client:
                            client.settimeout(2)
                            client.connect(str(endpoint))
                            client.sendall((json.dumps(value) + "\n").encode())
                            return json.loads(client.makefile("rb").readline())

                    packet = PointPacket(1, 1000.1, len(self.world), self.world, self.pose)
                    self.session.observe(packet, session="2" * 32, input_mode="recorded",
                                         received_at=control.time.monotonic())
                    state = send({"action": "state"})["value"]
                    request = {"action": "seed", "binding": state["binding"],
                               "expectedGeneration": state["generation"],
                               "referenceFrame": 1, "sourceFrame": "lidar_chest_front",
                               "mapFromSensor": self.pose.tolist()}
                    self.assertEqual("lidar_chest_front", state["sourceFrame"])
                    for frame in ("IMU", "base_link", None):
                        mismatched = {**request, "sourceFrame": frame}
                        if frame is None:
                            del mismatched["sourceFrame"]
                        self.assertEqual(400, send(mismatched)["status"])
                        self.assertEqual(state["generation"], send({"action": "state"})["value"]["generation"])
                    self.assertEqual("seed_requested", send(request)["value"]["state"])
                    self.assertEqual(409, send(request)["status"])
                    self.session.tick()
                    for frame in range(2, 5):
                        self.session.observe(replace(packet, native_frame_id=frame,
                            algorithm_stamp=1000. + .1 * frame), session="2" * 32,
                            input_mode="recorded", received_at=control.time.monotonic())
                    state = send({"action": "state"})["value"]
                    self.assertEqual("tracking", state["state"])
                    paused = send({"action": "pause", "binding": state["binding"],
                                   "expectedGeneration": state["generation"]})["value"]
                    self.assertIsNone(paused["mapFromSensor"])
                    self.assertEqual("paused", paused["state"])
                    stopped = send({"action": "stop", "binding": paused["binding"],
                                    "expectedGeneration": paused["generation"]})["value"]
                    self.assertEqual("stopped", stopped["state"])
                    self.assertFalse(stopped["cleanupComplete"])
            finally:
                self.session.close_worker()
            self.assertFalse(endpoint.exists())
            self.assertTrue(self.session.state()["cleanupComplete"])

    def test_invalid_bounded_socket_requests_do_not_kill_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            endpoint = Path(temporary) / "localization.sock"
            with control.LocalizationControlServer(endpoint, self.session) as server:
                server.start()
                for payload in (b'{"action":"state","action":"stop"}\n',
                                b'{"action":NaN}\n', b'{"action":Infinity}\n',
                                b"[]" + b" " * 4096 + b"\n", b"\xff\n"):
                    with self.subTest(payload=payload[:40]), socket.socket(socket.AF_UNIX) as client:
                        client.settimeout(2)
                        client.connect(str(endpoint))
                        client.sendall(payload)
                        result = json.loads(client.makefile("rb").readline())
                        self.assertEqual(400, result["status"])
                with socket.socket(socket.AF_UNIX) as slow:
                    slow.connect(str(endpoint))
                    slow.sendall(b"{")
                    self.assertEqual(503, json.loads(slow.makefile("rb").readline())["status"])
                self.assertTrue(server.thread.is_alive())

    def test_bind_failure_preserves_other_socket_and_close_preserves_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            endpoint = Path(temporary) / "localization.sock"
            with patch.object(control.LocalizationControlServer, "server_bind",
                              side_effect=OSError("injected bind")):
                with self.assertRaises(OSError):
                    control.LocalizationControlServer(endpoint, self.session)
            self.assertFalse(endpoint.exists())
            self.setUp()
            server = control.LocalizationControlServer(endpoint, self.session)
            endpoint.unlink()
            endpoint.write_bytes(b"replacement")
            server.server_close()
            self.assertEqual(b"replacement", endpoint.read_bytes())

    def test_unreviewed_platform_live_or_missing_point_output_is_rejected(self):
        for options in ({"platform": "arm64-humble", "inputMode": "recorded", "pointAdapter": "/x"},
                        {"inputMode": "live", "pointAdapter": "/x"}, {"inputMode": "recorded"}):
            with patch.object(runtime, "configuration", return_value=(options, {})):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    runtime.verified_configuration(Path("/unused"))

    def test_recorded_platform_must_match_host_and_humble_python(self):
        for machine, configured, python, accepted in (
                ("x86_64", None, (3, 12), True),
                ("aarch64", "arm64-humble", (3, 10), True),
                ("x86_64", "arm64-humble", (3, 10), False),
                ("aarch64", None, (3, 10), False),
                ("aarch64", "arm64-humble", (3, 12), False),
                ("armv7l", "arm64-humble", (3, 10), False)):
            config = {"inputMode": "recorded", "pointAdapter": "/verified"}
            if configured:
                config["platform"] = configured
            with self.subTest(machine=machine, configured=configured, python=python), patch.object(
                    runtime, "configuration", return_value=(config, {"frozen": "environment"})), patch.object(
                    runtime.host_platform, "machine", return_value=machine), patch.object(
                    runtime.sys, "version_info", python), patch.object(
                    runtime.os, "geteuid", return_value=1000):
                if accepted:
                    self.assertEqual((config, {"frozen": "environment"}),
                                     runtime.verified_configuration(Path("/unused")))
                else:
                    with self.assertRaises(ValueError):
                        runtime.verified_configuration(Path("/unused"))

    def test_live_and_root_remain_rejected_on_matching_arm64(self):
        config = {"platform": "arm64-humble", "inputMode": "recorded", "pointAdapter": "/verified"}
        for mode, uid in (("live", 1000), ("recorded", 0)):
            with self.subTest(mode=mode, uid=uid), patch.object(
                    runtime, "configuration", return_value=({**config, "inputMode": mode}, {})), patch.object(
                    runtime.host_platform, "machine", return_value="aarch64"), patch.object(
                    runtime.sys, "version_info", (3, 10)), patch.object(
                    runtime.os, "geteuid", return_value=uid):
                with self.assertRaises(ValueError):
                    runtime.verified_configuration(Path("/unused"))

    def test_live_requires_explicit_opt_in_matching_arm64_humble(self):
        config = {"platform": "arm64-humble", "inputMode": "live", "pointAdapter": "/verified"}
        for machine, python, uid, accepted in (
                ("aarch64", (3, 10), 1000, True), ("x86_64", (3, 10), 1000, False),
                ("aarch64", (3, 12), 1000, False), ("aarch64", (3, 10), 0, False)):
            with self.subTest(machine=machine, python=python, uid=uid), patch.object(
                    runtime, "configuration", return_value=(config, {})), patch.object(
                    runtime.host_platform, "machine", return_value=machine), patch.object(
                    runtime.sys, "version_info", python), patch.object(
                    runtime.os, "geteuid", return_value=uid):
                if accepted:
                    self.assertEqual((config, {}), runtime.verified_configuration(
                        Path("/unused"), allow_live=True))
                else:
                    with self.assertRaises(ValueError):
                        runtime.verified_configuration(Path("/unused"), allow_live=True)

    def test_loaded_inventory_preserves_platform_specific_library_hashes(self):
        arm = SimpleNamespace(arm64=True, odometry_library=Path("/frozen/odom.so"),
            point_library=Path("/frozen/points.so"), frozen={
                "coreLibrariesSha256": {"libglim.so": "a" * 64},
                "odometryLibrarySha256": "b" * 64, "pointLibrarySha256": "c" * 64})
        self.assertEqual({"libglim.so": "a" * 64, "odom.so": "b" * 64, "points.so": "c" * 64},
                         runtime.native_library_inventory(arm))
        x64 = SimpleNamespace(arm64=False, point_library=Path("/frozen/points.so"), frozen={
            "nativeLibrariesSha256": {"libglim.so": "d" * 64},
            "pointExtensionLibrarySha256": "e" * 64})
        self.assertEqual({"libglim.so": "d" * 64, "points.so": "e" * 64},
                         runtime.native_library_inventory(x64))


@unittest.skipUnless(AVAILABLE, "requires isolated Linux localization dependencies")
class RuntimeCompletion(unittest.TestCase):
    def setUp(self):
        self.binding = LocalizationBinding(
            "2" * 32, "recorded", "slam-" + "1" * 32, "3" * 64, "4" * 64, "5" * 64)
        self.frozen = {"journal": {"records": 10}, "journalSha256": "6" * 64}
        self.sources, self.config_hash = {"source.py": "7" * 64}, "8" * 64
        self.child = {
            "result": runtime.replay.CHILD_RESULT, "session": self.binding.session, "replayRate": 1.,
            "inputIntegrity": {
                **deepcopy(self.frozen), "localizationBinding": asdict(self.binding),
                "localizationSourceSha256": self.sources, "localizationConfigSha256": self.config_hash,
            },
        }
        self.final = {
            "state": {
                "binding": asdict(self.binding), "state": "stopped", "mapFromSensor": None,
                "referenceFrame": None, "cleanupComplete": True, "workerClosed": True,
                "workerReady": False, "requestPending": False,
                "localizationReady": False, "navigationReady": False,
            },
            "robotContacted": False, "motionCommandsSent": False,
            "mapProfileAssociationVerified": False,
        }

    def verify(self, child=None, final=None):
        return runtime.verify_completion(
            self.child if child is None else child, self.final if final is None else final,
            frozen=self.frozen, binding=self.binding, sources=self.sources,
            config_hash=self.config_hash, rate=1.)

    def test_complete_accounting_is_required_before_identity_and_cleanup_acceptance(self):
        with patch.object(runtime.replay, "complete_child") as accounting:
            self.verify()
            accounting.assert_called_once_with(self.child, self.frozen["journal"])
        with patch.object(runtime.replay, "complete_child", side_effect=ValueError("incomplete")):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                self.verify()

    def test_all_bound_input_and_runtime_identities_are_required(self):
        cases = (
            ("result", "failed"), ("session", "9" * 32), ("replayRate", .5),
            ("journalSha256", "9" * 64), ("journal", {"records": 9}),
            ("localizationBinding", asdict(replace(self.binding, map_id="slam-" + "9" * 32))),
            ("localizationSourceSha256", {}), ("localizationConfigSha256", "9" * 64),
        )
        with patch.object(runtime.replay, "complete_child"):
            for key, value in cases:
                report = deepcopy(self.child)
                target = report if key in ("result", "session", "replayRate") else report["inputIntegrity"]
                target[key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    self.verify(child=report)

    def test_cleanup_cannot_publish_pose_reference_readiness_or_pending_work(self):
        cases = (
            ("binding", {}), ("state", "tracking"), ("mapFromSensor", np.eye(4).tolist()),
            ("referenceFrame", 1), ("cleanupComplete", False), ("cleanupComplete", 1),
            ("workerClosed", False), ("workerReady", True), ("requestPending", True),
            ("localizationReady", True), ("navigationReady", True),
            ("robotContacted", True), ("motionCommandsSent", True),
            ("mapProfileAssociationVerified", True),
        )
        with patch.object(runtime.replay, "complete_child"):
            for key, value in cases:
                final = deepcopy(self.final)
                target = final if key in (
                    "robotContacted", "motionCommandsSent", "mapProfileAssociationVerified") else final["state"]
                target[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.verify(final=final)

    def run_fixture(self, *, failure=None, terminal_changes=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config_file = root / "config.json"
        config_file.write_bytes(b'{"synthetic":true}')
        args = SimpleNamespace(
            config=config_file, saved_map=root / "map", output=root / "output", seconds=30.,
            map_id=self.binding.map_id, receipt_sha256=self.binding.receipt_sha256,
            profile_sha256=self.binding.profile_sha256)
        self.config_hash = control.digest(config_file.read_bytes())
        self.child["inputIntegrity"]["localizationConfigSha256"] = self.config_hash
        terminal = {
            "state": "stopped", "exitCode": 0, "forcedTermination": False, "cleanupComplete": True,
            "processAlive": False, "monitorAlive": False, **(terminal_changes or {}),
        }
        owner = Mock(session=self.binding.session)
        owner.stop.return_value = terminal
        platform = Mock(frozen=self.frozen, profile=root / "private-profile.json")
        platform.verify.return_value = self.frozen
        if failure == "input_changed":
            platform.verify.return_value = {}

        def coordinate(*_):
            if failure == "process_failed":
                raise RuntimeError("injected child failure")
            final = deepcopy(self.final)
            if failure == "uncleared_pose":
                final["state"]["mapFromSensor"] = np.eye(4).tolist()
            for name, value in (("child-report.json", self.child), ("localization-final.json", final)):
                (args.output / name).write_text(json.dumps(value))
            if failure == "duplicate_json":
                file = args.output / "localization-final.json"
                file.write_text('{"state":{},' + file.read_text()[1:])
            return terminal

        with patch.object(runtime, "verified_configuration", return_value=({"rate": 1.}, {})), patch.object(
                runtime, "MappingPlatform", return_value=platform), patch.object(
                runtime, "source_hashes", return_value=self.sources), patch.object(
                runtime, "ProcessSupervisor", return_value=owner), patch.object(
                runtime, "load_map", return_value=(self.binding, None)), patch.object(
                runtime.replay, "coordinate", side_effect=coordinate), patch.object(
                runtime.replay, "complete_child"):
            result = runtime.run(args)
        owner.stop.assert_called_once()
        report = json.loads((args.output / "localization-runtime-report.json").read_text())
        self.assertEqual(0o600, (args.output / "localization-runtime-report.json").stat().st_mode & 0o777)
        self.assertFalse(report["localizationReady"])
        self.assertFalse(report["navigationReady"])
        return result, report

    def test_parent_accepts_closed_result_and_writes_private_report(self):
        result, report = self.run_fixture()
        self.assertEqual(0, result)
        self.assertEqual("recorded_localization_runtime_closed", report["result"])
        self.assertEqual({"child-report.json", "localization-final.json"}, set(report["filesSha256"]))

    def test_parent_failure_always_stops_owner_and_records_failure(self):
        for failure in ("process_failed", "uncleared_pose", "input_changed", "duplicate_json"):
            with self.subTest(failure=failure):
                result, report = self.run_fixture(failure=failure)
                self.assertEqual(1, result)
                self.assertEqual("failed", report["result"])
                self.assertIn("errorType", report)

    def test_unclean_process_cannot_be_overridden_by_successful_reports(self):
        for key, value in (
                ("state", "failed"), ("exitCode", 1), ("forcedTermination", True),
                ("cleanupComplete", False), ("processAlive", True), ("monitorAlive", True)):
            with self.subTest(key=key):
                result, report = self.run_fixture(terminal_changes={key: value})
                self.assertEqual(1, result)
                self.assertEqual("failed", report["result"])


if __name__ == "__main__":
    unittest.main()
