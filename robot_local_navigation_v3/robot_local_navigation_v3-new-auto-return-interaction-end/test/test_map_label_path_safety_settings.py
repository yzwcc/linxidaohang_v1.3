from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from robot_local_navigation_v3.config_store import UnifiedConfigStore
from robot_local_navigation_v3.map_label_server_v3 import (
    BridgeServiceManager,
    V3LabelApp,
    path_safety_matches,
)
from robot_local_navigation_v3.models import UnifiedNavConfig


class MapLabelPathSafetySettingsTests(unittest.TestCase):
    def test_saves_global_path_safety_and_reports_bridge_application(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UnifiedConfigStore(Path(temporary) / "unified_nav_config.json")
            store.save(UnifiedNavConfig())
            bridge = Mock()
            bridge.set_nav_mode.return_value = {
                "running": True,
                "applied": True,
                "path_safety": {
                    "robot_radius_m": 0.253,
                    "extra_clearance_m": 0.18,
                },
            }
            app = V3LabelApp(store, Mock(), bridge, Mock())

            result = app.save_settings({
                "path_safety": {
                    "robot_radius_m": 0.253,
                    "extra_clearance_m": 0.18,
                }
            })

            persisted = store.load().navigation.path_safety
            self.assertEqual(persisted.robot_radius_m, 0.253)
            self.assertEqual(persisted.extra_clearance_m, 0.18)
            self.assertTrue(result["path_safety_saved"])
            self.assertTrue(result["path_safety_applied"])
            self.assertEqual(result["applied_path_safety"]["robot_radius_m"], 0.253)
            self.assertEqual(result["applied_path_safety"]["extra_clearance_m"], 0.18)
            bridge.set_nav_mode.assert_called_once_with(
                "dry-run",
                path_safety=persisted.__dict__,
            )

    def test_keeps_saved_values_but_reports_when_bridge_is_not_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UnifiedConfigStore(Path(temporary) / "unified_nav_config.json")
            store.save(UnifiedNavConfig())
            bridge = Mock()
            bridge.set_nav_mode.return_value = {"running": False, "applied": False}
            app = V3LabelApp(store, Mock(), bridge, Mock())

            result = app.save_settings({
                "path_safety": {
                    "robot_radius_m": 0.253,
                    "extra_clearance_m": 0.1,
                }
            })

            self.assertEqual(store.load().navigation.path_safety.robot_radius_m, 0.253)
            self.assertTrue(result["path_safety_saved"])
            self.assertFalse(result["path_safety_applied"])
            self.assertFalse(result["bridge_running"])

    def test_rejects_invalid_radius_or_clearance_without_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UnifiedConfigStore(Path(temporary) / "unified_nav_config.json")
            store.save(UnifiedNavConfig())
            bridge = Mock()
            app = V3LabelApp(store, Mock(), bridge, Mock())

            for radius, clearance in ((0, 0.1), (2.01, 0.1), (0.253, -0.01), (0.253, 2.01)):
                with self.subTest(radius=radius, clearance=clearance):
                    with self.assertRaises(ValueError):
                        app.save_settings({
                            "path_safety": {
                                "robot_radius_m": radius,
                                "extra_clearance_m": clearance,
                            }
                        })

            self.assertIsNone(store.load().navigation.path_safety.robot_radius_m)
            bridge.set_nav_mode.assert_not_called()

    def test_get_settings_reports_persisted_and_runtime_values_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UnifiedConfigStore(Path(temporary) / "unified_nav_config.json")
            config = UnifiedNavConfig()
            config.navigation.path_safety.robot_radius_m = 0.253
            config.navigation.path_safety.extra_clearance_m = 0.18
            store.save(config)
            bridge = Mock()
            bridge.status.return_value = {
                "bridge": {
                    "ok": True,
                    "path_safety": {
                        "robot_radius_m": 0.253,
                        "extra_clearance_m": 0.18,
                        "occupied_threshold": 205,
                        "max_odom_age_sec": 1.5,
                        "max_start_drift_m": 0.1,
                        "max_reference_points": 256,
                    },
                }
            }
            app = V3LabelApp(store, Mock(), bridge, Mock())

            settings = app.get_settings()

            self.assertTrue(settings["path_safety_applied"])
            self.assertEqual(settings["path_safety"]["robot_radius_m"], 0.253)
            self.assertEqual(settings["applied_path_safety"]["extra_clearance_m"], 0.18)

            bridge.status.return_value["bridge"]["path_safety"]["extra_clearance_m"] = 0.1
            settings = app.get_settings()
            self.assertFalse(settings["path_safety_applied"])

    def test_bridge_application_requires_exact_runtime_readback(self) -> None:
        expected = {
            "robot_radius_m": 0.253,
            "extra_clearance_m": 0.18,
            "occupied_threshold": 205,
            "max_odom_age_sec": 1.5,
            "max_start_drift_m": 0.1,
            "max_reference_points": 256,
        }
        with tempfile.TemporaryDirectory() as temporary:
            manager = BridgeServiceManager(
                config_path=Path(temporary) / "config.json",
                app_home=Path(temporary),
                bridge_host="0.0.0.0",
                bridge_port=18082,
            )
            manager._get_remote_status = Mock(
                side_effect=[
                    {"ok": True},
                    {"ok": True, "path_safety": expected},
                    {"ok": True},
                    {
                        "ok": True,
                        "path_safety": {**expected, "extra_clearance_m": 0.1},
                    },
                ]
            )
            manager._post_json = Mock(return_value={"ok": True, "path_safety": expected})

            applied = manager.set_nav_mode("execute", path_safety=expected)
            self.assertTrue(applied["applied"])
            self.assertEqual(applied["path_safety"], expected)
            self.assertEqual(manager._get_remote_status.call_count, 2)

            manager._post_json.return_value = {
                "ok": True,
                "path_safety": {**expected, "extra_clearance_m": 0.1},
            }
            mismatched = manager.set_nav_mode("execute", path_safety=expected)
            self.assertFalse(mismatched["applied"])
            self.assertEqual(manager._get_remote_status.call_count, 4)

            manager._get_remote_status.side_effect = None
            manager._get_remote_status.return_value = {}
            offline = manager.set_nav_mode("execute", path_safety=expected)
            self.assertFalse(offline["running"])
            self.assertFalse(offline["applied"])

    def test_path_safety_value_comparison_rejects_missing_or_invalid_values(self) -> None:
        expected = {
            "robot_radius_m": 0.253,
            "extra_clearance_m": 0.18,
            "occupied_threshold": 205,
            "max_odom_age_sec": 1.5,
            "max_start_drift_m": 0.1,
            "max_reference_points": 256,
        }
        self.assertTrue(path_safety_matches(expected, dict(expected)))
        self.assertFalse(path_safety_matches(expected, None))
        self.assertFalse(path_safety_matches(expected, {**expected, "robot_radius_m": "bad"}))
        self.assertFalse(path_safety_matches(expected, {**expected, "occupied_threshold": 206}))


if __name__ == "__main__":
    unittest.main()
