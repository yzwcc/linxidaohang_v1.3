from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "robot-runtime" / "python"))
from reference_service import checked_grid, make_app


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
