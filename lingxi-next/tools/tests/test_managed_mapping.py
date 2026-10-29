"""Offline launcher checks; no native library, sensor, network or robot access."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-runtime/python"))
from managed_mapping import main, validate_output
from mapping_owner import MappingOwnerLimits


class ManagedConfiguration(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.frozen = self.root / "frozen"
        self.frozen.mkdir()

    def test_new_sibling_output_preserves_frozen_input_directory(self):
        validate_output(self.root / "run", {"pointAdapter": str(self.frozen)},
                        (self.frozen / "config.json",))
        self.assertEqual([], list(self.frozen.iterdir()))
        self.assertFalse((self.root / "run").exists())

    def test_relative_existing_and_overlapping_output_is_rejected(self):
        for output in (Path("relative"), self.frozen, self.frozen / "new",
                       self.root / "missing" / ".." / "run"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                validate_output(output, {"pointAdapter": str(self.frozen)}, ())
        parent = self.root / "new"
        for name in ("root", "coreRoot", "mappingRoot", "pointAdapter", "reference",
                     "work", "adapter", "mappingAdapter", "bundle"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_output(parent, {name: str(parent / "frozen")}, ())
        with self.assertRaises(ValueError):
            validate_output(parent, {}, (parent / "api.mjs",))

    def test_cleanup_limits_are_finite_positive_and_allow_polling(self):
        self.assertEqual(110., MappingOwnerLimits().service_stop_seconds)
        for value in (0, -1, True, float("nan"), float("inf"), "2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MappingOwnerLimits(service_stop_seconds=value)
        with self.assertRaises(ValueError):
            MappingOwnerLimits(tick_seconds=5.)

    def launcher(self, environment, *, node_digest=None):
        node = Path(sys.executable).resolve()
        api = self.frozen / "api.mjs"
        api.write_text("// Synthetic launcher input, never executed.\n", encoding="utf-8")
        arguments = ["managed_mapping.py", "--config", str(self.frozen / "config.json"),
                     "--output", str(self.root / "run"), "--node", str(node),
                     "--node-sha256", node_digest or hashlib.sha256(node.read_bytes()).hexdigest(),
                     "--api-bundle", str(api),
                     "--api-sha256", hashlib.sha256(api.read_bytes()).hexdigest(),
                     "--seconds", "60"]
        with patch("managed_mapping.configuration", return_value=(
                {"pointAdapter": str(self.frozen)}, {})), patch.dict(os.environ, environment, clear=True), \
                patch.object(sys, "argv", arguments), patch("managed_mapping.signal.signal"), \
                patch("managed_mapping.ManagedMappingOwner") as owner:
            owner.return_value.run.return_value = {"result": "stopped"}
            result = main()
            return result, owner

    def test_production_launcher_passes_only_reviewed_api_environment(self):
        expected = {
            "LINGXI_TLS_KEY": "/synthetic/key", "LINGXI_TLS_CERT": "/synthetic/cert",
            "LINGXI_PAIRING_CODE_SHA256": "a" * 64, "LINGXI_HOST": "127.0.0.1",
            "LINGXI_PORT": "3456", "LINGXI_WEB_ROOT": "/synthetic/web",
        }
        result, owner = self.launcher({
            **expected, "LINGXI_DEMO_MODE": "1", "NODE_OPTIONS": "--inspect",
            "LINGXI_MAPPING_SOCKET": "/unreviewed/socket", "LINGXI_SLAM_POINT_FD": "8",
            "LINGXI_AGENT_INTERNAL_TOKEN": "synthetic-unused", "LD_PRELOAD": "/unreviewed/library",
            "PYTHONPATH": "/unreviewed/modules",
        })
        self.assertEqual(0, result)
        options = owner.call_args.kwargs
        self.assertEqual({"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", **expected},
                         options["api_environment"])
        self.assertEqual({"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                          "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
                         options["service_environment"])
        self.assertEqual(60, owner.return_value.run.call_args.kwargs["seconds"])
        self.assertFalse((self.root / "run").exists())

    def test_production_launcher_rejects_missing_tls_pairing_or_changed_node(self):
        required = {"LINGXI_TLS_KEY": "/synthetic/key", "LINGXI_TLS_CERT": "/synthetic/cert",
                    "LINGXI_PAIRING_CODE_SHA256": "a" * 64}
        for name in required:
            with self.subTest(missing=name), self.assertRaisesRegex(
                    ValueError, "explicit_production_tls_and_pairing_required"):
                self.launcher({key: value for key, value in required.items() if key != name})
        with self.assertRaises(ValueError):
            self.launcher(required, node_digest="0" * 64)
        self.assertFalse((self.root / "run").exists())


if __name__ == "__main__":
    unittest.main()
