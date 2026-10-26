from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from robot_local_navigation_v3.config_store import UnifiedConfigStore
from robot_local_navigation_v3.map_label_server_v3 import V3LabelApp
from robot_local_navigation_v3.map_point_store import MapPointStore
from robot_local_navigation_v3.models import UnifiedNavConfig


class MappingDiscoveryTests(unittest.TestCase):
    def test_imported_aimdk_map_is_discoverable_and_loadable_for_point_editing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            map_dir = root / "123"
            map_dir.mkdir()
            (map_dir / "occupancy_map.png").write_bytes(b"png")
            info = {
                "resolution_pixels_per_meter": 20.0,
                "origin_u": 20.0,
                "origin_v": -38.0,
                "width": 3,
                "height": 2,
            }
            (map_dir / "lingxi_map_import.json").write_text(json.dumps({
                "map_id": 123,
                "map_name": "123",
                "source_map_name": "inspection_room",
                "map_info": info,
            }), encoding="utf-8")
            store = UnifiedConfigStore(root / "unified_nav_config.json")
            store.save(UnifiedNavConfig())
            app = V3LabelApp(store, MapPointStore(root / "maps"), Mock(), Mock())

            maps = app.discover_maps(str(root))
            payload = app.get_map_payload(str(root), "123")
            image, content_type = app.get_map_image(str(root), "123")

            self.assertEqual(len(maps), 1)
            self.assertEqual(maps[0]["map_id"], 123)
            self.assertEqual(maps[0]["origin_u"], 20.0)
            self.assertEqual(payload["map"]["map_name"], "123")
            self.assertEqual(image, b"png")
            self.assertEqual(content_type, "image/png")

    def test_import_metadata_must_match_numeric_map_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            map_dir = root / "123"
            map_dir.mkdir()
            (map_dir / "occupancy_map.png").write_bytes(b"png")
            (map_dir / "lingxi_map_import.json").write_text(json.dumps({
                "map_id": 999,
                "map_name": "999",
                "map_info": {"width": 1, "height": 1},
            }), encoding="utf-8")
            store = UnifiedConfigStore(root / "unified_nav_config.json")
            store.save(UnifiedNavConfig())
            app = V3LabelApp(store, Mock(), Mock(), Mock())
            self.assertEqual(app.discover_maps(str(root)), [])

    def test_import_metadata_without_map_image_is_not_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            map_dir = root / "123"
            map_dir.mkdir()
            (map_dir / "lingxi_map_import.json").write_text(json.dumps({
                "map_id": 123,
                "map_name": "123",
                "map_info": {"width": 1, "height": 1},
            }), encoding="utf-8")
            store = UnifiedConfigStore(root / "unified_nav_config.json")
            store.save(UnifiedNavConfig())
            app = V3LabelApp(store, Mock(), Mock(), Mock())
            self.assertEqual(app.discover_maps(str(root)), [])


if __name__ == "__main__":
    unittest.main()
