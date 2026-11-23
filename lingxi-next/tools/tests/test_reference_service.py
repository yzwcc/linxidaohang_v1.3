import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "robot-runtime" / "python"))
from reference_service import (checked_grid, checked_map, make_app, reference_capture_phase,
                               reference_capture_progress, reference_capture_settings)
from reference_calibration import check_target_mounting


def target_calibration(directory, *, online=False):
    import numpy as np
    import yaml

    # Non-identity rotation with a translation catches reversed TF query/composition.
    configured = np.array([[0, -1, 0, .024], [1, 0, 0, -.012], [0, 0, 1, .047], [0, 0, 0, 1]])
    profile = Path(directory).resolve() / "fastlio.yaml"
    profile.write_text(yaml.safe_dump({"/**": {"ros__parameters": {"mapping": {
        "extrinsic_est_en": online, "extrinsic_R": configured[:3, :3].reshape(-1).tolist(),
        "extrinsic_T": configured[:3, 3].tolist(),
    }}}}), encoding="utf-8")
    return profile, np.linalg.inv(configured)


class TargetCalibrationTests(unittest.TestCase):
    def test_validates_direction_mismatch_and_unusable_tf(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, live = target_calibration(directory)
            lookup = Mock(return_value=live)
            self.assertEqual(check_target_mounting(profile, lookup), "matched")
            lookup.assert_called_once_with("lidar_chest_front", "lidar_imu_chest_front", 0)
            live[0, 3] += .012
            self.assertEqual(check_target_mounting(profile, lookup), "mismatch")
            lookup.return_value = None
            self.assertEqual(check_target_mounting(profile, lookup), "missing_live_tf")
            live[2, 2] = -1
            lookup.return_value = live
            self.assertEqual(check_target_mounting(profile, lookup), "invalid_live_tf")

    def test_online_estimation_and_missing_configs_do_not_query_tf(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, _ = target_calibration(directory, online=True)
            lookup = Mock()
            for path in (profile, profile.with_name("missing.yaml"), Path("relative.yaml")):
                self.assertEqual(check_target_mounting(path, lookup), "invalid_profile")
            lookup.assert_not_called()

    def test_status_exposes_mismatch_and_blocks_mapping_activation_and_planning(self):
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as directory:
            profile, live = target_calibration(directory)
            aimdk = SimpleNamespace(service_is_ready=lambda: True)
            ros = SimpleNamespace(source_get=aimdk, source_set=aimdk, _navigation_transform=Mock(return_value=live),
                                  snapshot=lambda: {"sensor_fresh": {"lidar": True, "imu": True},
                                                    "localization_fresh": True, "actions": {"compute_path": True},
                                                    "nav2": {"loaded_map": True}})
            mapping = SimpleNamespace(snapshot=lambda: {"active": None, "items": [], "enabled": True})
            state = SimpleNamespace(settings={"mapping": {"fastlio_config": str(profile)}},
                                    ros=ros, mapping=mapping, active_map_id="fastlio-" + "a" * 32)
            source = SimpleNamespace(app=SimpleNamespace(state=state), lifespan=None,
                                     start_mapping=Mock(), activate_map=Mock())
            client = TestClient(make_app(source, Path(directory)))
            status = client.get("/lingxi/status").json()
            self.assertEqual(status["calibration"], "matched")
            self.assertTrue(status["mappingReady"] and status["planningReady"])
            live[0, 3] += .012
            status = client.get("/lingxi/status").json()
            self.assertEqual(status["calibration"], "mismatch")
            self.assertFalse(status["mappingReady"] or status["planningReady"])
            for operation in ("mapping/start", f"maps/{state.active_map_id}/activate", f"maps/{state.active_map_id}/plan"):
                response = client.post(f"/lingxi/{operation}", json={})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json(), {"detail": "reference_calibration_mismatch"})
            source.start_mapping.assert_not_called()
            source.activate_map.assert_not_called()

            # A static restart cannot replace an unresolved motion task with idle.
            live[0, 3] -= .012
            marker = Path(directory) / "motion-in-progress"
            marker.write_text("prior-session\n", encoding="ascii")
            restarted = TestClient(make_app(source, Path(directory)))
            recovered = restarted.get("/lingxi/status").json()
            self.assertTrue(recovered["motionRecoveryRequired"])
            self.assertFalse(recovered["mappingReady"] or recovered["planningReady"])
            for operation in ("mapping/start", f"maps/{state.active_map_id}/activate", f"maps/{state.active_map_id}/plan"):
                result = restarted.post(f"/lingxi/{operation}", json={})
                self.assertEqual(result.status_code, 409)
                self.assertEqual(result.json(), {"detail": "reference_stop_recovery_required"})
            self.assertEqual(marker.read_text(encoding="ascii"), "prior-session\n")


class ReferenceCaptureTests(unittest.TestCase):
    def test_old_profile_disables_only_live_grid_cache_at_runtime(self):
        mapping = {"grid_cache_enabled": True, "fastlio_config": "/etc/lingxi-next/fastlio-x2.yaml"}
        original = {"mapping": mapping, "motion_enabled": False, "visualization": {"enabled": False}}
        effective = reference_capture_settings(original)
        self.assertEqual(effective, {**original, "mapping": {**mapping, "grid_cache_enabled": False}})
        self.assertIs(original["mapping"]["grid_cache_enabled"], True)

    def test_stationary_hold_follows_confirmed_capture_not_start_request(self):
        self.assertEqual(reference_capture_phase({"state": "starting", "started_at": 1}, 30), "requested")
        for stamp in (None, False, float("nan"), 0, "27", 27.01, 32):
            self.assertEqual(reference_capture_phase({"state": "recording", "recording_started_at": stamp}, 30),
                             "requested")
        self.assertEqual(reference_capture_phase({"state": "recording", "recording_started_at": 27}, 30), "running")
        self.assertEqual(reference_capture_phase({"state": "stopping"}, 30), "saving")

    def test_capture_progress_never_reuses_a_terminal_session_or_invents_counters(self):
        live = {"state": "recording", "stats": {"paired_frames": 42, "recording_bytes": 314159}}
        self.assertEqual(reference_capture_progress(live), {"pairedFrames": 42, "recordingBytes": 314159})
        self.assertIsNone(reference_capture_progress({**live, "state": "failed"}))
        self.assertIsNone(reference_capture_progress(None))
        for count in (True, None, -1, 1 << 53, 2.5):
            self.assertIsNone(reference_capture_progress({**live, "stats": {"paired_frames": count, "recording_bytes": 0}}))

    def test_premature_save_does_not_stop_or_fail_capture(self):
        from fastapi.testclient import TestClient

        identity = "b" * 32
        active = {"id": identity, "state": "recording", "recording_started_at": 98,
                  "stats": {"paired_frames": 7, "recording_bytes": 2048}}
        with tempfile.TemporaryDirectory() as directory:
            profile, live = target_calibration(directory)
            aimdk = SimpleNamespace(service_is_ready=lambda: True)
            ros = SimpleNamespace(source_get=aimdk, source_set=aimdk, _navigation_transform=Mock(return_value=live),
                                  snapshot=lambda: {"sensor_fresh": {"lidar": True, "imu": True},
                                                    "localization_fresh": False})
            mapping = SimpleNamespace(snapshot=lambda: {"active": active, "items": [active], "enabled": True})
            state = SimpleNamespace(mapping=mapping, ros=ros, active_map_id=None,
                                    settings={"mapping": {"fastlio_config": str(profile)}})
            source = SimpleNamespace(app=SimpleNamespace(state=state), lifespan=None, stop_mapping=Mock())
            client = TestClient(make_app(source, Path(directory)))
            with patch("reference_service.time.time", return_value=100):
                result = client.post("/lingxi/mapping/save", json={"session": identity, "name": "展厅"})
                self.assertEqual(result.status_code, 409)
                self.assertEqual(result.json(), {"detail": "reference_capture_initializing"})
                status = client.get("/lingxi/status").json()
                self.assertEqual((status["phase"], status["session"]), ("requested", identity))
                self.assertEqual(status["referenceCaptureProgress"], {"pairedFrames": 7, "recordingBytes": 2048})
            source.stop_mapping.assert_not_called()


class ReferenceGridTests(unittest.TestCase):
    def test_discard_stops_then_deletes_only_the_matching_capture(self):
        from fastapi.testclient import TestClient

        identity = "b" * 32
        selected = {"id": identity, "state": "recording"}
        active = [selected]
        stored = []
        mapping = SimpleNamespace(snapshot=lambda: {"active": next(iter(active), None), "items": [selected]},
                                  is_active=lambda: bool(active))
        state = SimpleNamespace(mapping=mapping, store=SimpleNamespace(maps=lambda: stored))
        def stop(session):
            self.assertEqual(session, identity)
            active.clear()
            selected["state"] = "saved"

        def delete(session, confirmed):
            self.assertEqual((session, confirmed), (identity, True))
            self.assertEqual(selected["state"], "saved")
            return {"id": session, "deleted": True}

        source = SimpleNamespace(app=SimpleNamespace(state=state), lifespan=None,
                                 stop_mapping=Mock(side_effect=stop), delete_mapping=Mock(side_effect=delete))
        client = TestClient(make_app(source, Path.cwd()))
        self.assertEqual(client.post("/lingxi/mapping/discard", json={"session": "a" * 32}).status_code, 409)
        source.stop_mapping.assert_not_called()
        self.assertIs(client.post("/lingxi/mapping/discard", json={"session": identity}).json(), True)
        source.delete_mapping.assert_called_once_with(identity, confirmed=True)
        stored.append({"id": f"fastlio-{identity}"})
        self.assertEqual(client.post("/lingxi/mapping/discard", json={"session": identity}).status_code, 409)
        self.assertEqual(source.delete_mapping.call_count, 1)

    def test_ros_rows_and_yaw_are_preserved(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            grid = root / "map.yaml"
            grid.write_text("image: map.pgm\nmode: trinary\nresolution: 0.05\norigin: [1, 2, 0.5]\n"
                            "occupied_thresh: 0.65\nfree_thresh: 0.196\nnegate: 0\n", encoding="utf-8")
            image = Image.frombytes("L", (2, 2), bytes([0, 205, 254, 0]))
            image.save(root / "map.pgm")
            bundle = {"yaml_path": str(grid), "width": 2, "height": 2, "resolution": 0.05, "origin": [1, 2, 0.5]}
            result = checked_grid(root, bundle)
            self.assertEqual(result["cells"], [0, 100, 100, -1])
            self.assertEqual(result["originYaw"], 0.5)
            with self.assertRaisesRegex(ValueError, "grid_metadata_mismatch"):
                checked_grid(root, {**bundle, "origin": [1, 2, 0]})

    def test_mount_exposes_only_product_routes(self):
        from fastapi.testclient import TestClient

        async def lifecycle(_app):
            raise AssertionError("Upstream runtime is not needed for route inspection")
        source = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), lifespan=lifecycle)
        app = make_app(source, Path.cwd())
        client = TestClient(app)
        self.assertEqual(client.post("/api/tasks", json={}).status_code, 404)
        self.assertEqual(client.get("/docs").status_code, 404)
        self.assertEqual(client.post("/lingxi/mapping/save", json={"name": "展厅", "session": "../other"}).status_code, 422)


class ReferenceExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.session = "b" * 32
        self.identity = f"fastlio-{self.session}"
        self.directory = self.root / self.identity
        self.directory.mkdir()
        self.yaml = self.directory / "map.yaml"
        self.yaml.write_text("image: map.pgm\nmode: trinary\nresolution: 0.05\norigin: [1, 2, 0.5]\n"
                             "occupied_thresh: 0.65\nfree_thresh: 0.196\nnegate: 0\n", encoding="utf-8")
        raster = b"P5\n2 2\n255\n" + bytes([0, 205, 254, 0])
        (self.directory / "map.pgm").write_bytes(raster)
        self.cloud = self.directory / "localization.ply"
        self.header = (b"ply\nformat binary_little_endian 1.0\ncomment Created by Open3D\nelement vertex 200\n"
                       + b"".join(f"property double {key}\n".encode() for key in ("x", "y", "z", "nx", "ny", "nz"))
                       + b"end_header\n")
        self.body = b"".join(struct.pack("<6d", i * .01, 2, 3, 0, 0, 1) for i in range(200))
        transform = [[1, 0, 0, .025], [0, 1, 0, 0], [0, 0, 1, -.025], [0, 0, 0, 1]]
        self.bundle = dict(id=self.identity, revision="c" * 32, name="展厅", schema_version=1, frame_id="map",
                           origin=[1, 2, .5], resolution=.05, width=2, height=2, map_points=200,
                           source_session_id=self.session, navigation_from_source=transform, base_from_imu=transform,
                           imu_from_lidar=transform, free_space={"output_sha256": hashlib.sha256(raster).hexdigest()},
                           localization_map=str(self.cloud), yaml_path=str(self.yaml))
        self.metadata = self.directory / "metadata.json"
        self.metadata.write_text(json.dumps(self.bundle), encoding="utf-8")
        self.write_cloud(self.header + self.body)

    def write_cloud(self, data):
        self.cloud.write_bytes(data)
        (self.directory / "localization.json").write_text(json.dumps({
            "map_points": 200, "normal_orientation": "towards_sensor", "viewpoint_frame": "map",
            "trajectory_frame": "map", "imu_from_lidar": self.bundle["imu_from_lidar"],
            "cache_sha256": hashlib.sha256(data).hexdigest(),
        }), encoding="utf-8")

    def test_reopens_pinned_export_and_rejects_geometry_corruption(self):
        self.assertEqual(checked_map(self.root, self.bundle)["originYaw"], .5)
        for data, reason in (
                (self.header + self.body[:-1], "invalid_reference_ply_layout"),
                (self.header.replace(b"vertex 200", b"vertex 201") + self.body, "invalid_reference_ply_layout"),
                (self.header + struct.pack("<d", float("nan")) + self.body[8:], "invalid_reference_ply_geometry"),
                (self.header + self.body[:24] + bytes(24) + self.body[48:], "invalid_reference_ply_normals")):
            with self.subTest(reason=reason):
                self.write_cloud(data)
                with self.assertRaisesRegex(ValueError, reason):
                    checked_map(self.root, self.bundle)

    def test_refuses_drift_in_sidecar_raster_origin_and_registry(self):
        self.cloud.write_bytes(self.header + struct.pack("<d", 42.0) + self.body[8:])
        with self.assertRaisesRegex(ValueError, "reference_ply_digest_mismatch"):
            checked_map(self.root, self.bundle)
        self.write_cloud(self.header + self.body)
        (self.directory / "map.pgm").write_bytes(b"P5\n2 2\n255\n" + bytes([254, 205, 254, 0]))
        with self.assertRaisesRegex(ValueError, "reference_grid_digest_or_format_mismatch"):
            checked_map(self.root, self.bundle)
        (self.directory / "map.pgm").write_bytes(b"P5\n2 2\n255\n" + bytes([0, 205, 254, 0]))
        base = self.yaml.read_text(encoding="utf-8")
        self.yaml.write_text(base.replace("0.65", "0.6"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reference_grid_policy_mismatch"):
            checked_map(self.root, self.bundle)
        self.yaml.write_text(base.replace("0.5]", "0.0]"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "grid_metadata_mismatch"):
            checked_map(self.root, self.bundle)
        with self.assertRaisesRegex(ValueError, "reference_map_metadata_mismatch"):
            checked_map(self.root, {**self.bundle, "revision": "a" * 32})

    def test_save_load_and_plan_reject_corrupt_export_before_ros_loader(self):
        from fastapi.testclient import TestClient

        profile, live = target_calibration(self.root)
        saved = {"id": self.session, "state": "saved"}
        mapping = SimpleNamespace(snapshot=lambda: {"active": None, "items": [saved]}, is_active=lambda: False)
        ros = SimpleNamespace(_navigation_transform=Mock(return_value=live), activate_map=Mock(), compute_path=Mock())
        state = SimpleNamespace(mapping=mapping, ros=ros, store=SimpleNamespace(map=Mock(return_value=self.bundle)),
                                settings={"mapping": {"fastlio_config": str(profile)}}, active_map_id=self.identity,
                                tasks=SimpleNamespace(require_localization=Mock()))
        source = SimpleNamespace(app=SimpleNamespace(state=state), lifespan=None, activate_map=Mock(),
                                 stop_mapping=Mock(), require_mapping_idle=Mock())
        client = TestClient(make_app(source, self.root))
        payload = {"name": self.bundle["name"], "session": self.session}
        self.assertEqual(client.post("/lingxi/mapping/save", json=payload).json()["id"], self.identity)
        self.assertEqual(client.get(f"/lingxi/maps/{self.identity}/grid").json()["originYaw"], .5)
        self.cloud.write_bytes(self.header + self.body[:-1])
        self.assertEqual(client.post("/lingxi/mapping/save", json=payload).status_code, 409)
        self.assertEqual(client.get(f"/lingxi/maps/{self.identity}/grid").status_code, 409)
        self.assertEqual(client.post(f"/lingxi/maps/{self.identity}/activate", json={}).status_code, 409)
        self.assertEqual(client.post(f"/lingxi/maps/{self.identity}/plan", json={"pose": {"x": 1, "y": 2, "yaw": .5}}).status_code, 409)
        source.activate_map.assert_not_called()
        ros.activate_map.assert_not_called()
        ros.compute_path.assert_not_called()


if __name__ == "__main__":
    unittest.main()
