from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "robot-runtime" / "python"))
from reference_service import checked_grid, make_app
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


if __name__ == "__main__":
    unittest.main()
