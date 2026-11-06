"""DDS relocation uses identical verified bytes; no ROS or robot access."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "deploy"), str(ROOT / "robot-slam"),
                str(ROOT / "robot-runtime/python"), str(ROOT / "tools")]
from lingxi_slam.dds_profile import selected_profile


@unittest.skipUnless(sys.platform == "linux", "POSIX installation ownership and permissions")
class InstalledDdsProfile(unittest.TestCase):
    def setUp(self):
        import provision_dds_profile
        self.provision = provision_dds_profile.provision
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "vendor.xml"
        self.source.write_bytes(b"<profiles>\r\n verified transport bytes \r\n</profiles>\r\n")
        self.source.chmod(0o600)
        self.before = self.source.stat()
        self.expected = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.directory = self.root / "product"
        self.directory.mkdir(mode=0o750)
        self.target = self.directory / "dds.xml"
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()

    def test_copy_is_byte_exact_idempotent_and_leaves_source_metadata_unchanged(self):
        for _ in range(2):
            self.provision(self.source, self.target, self.expected, os.getegid())
        self.assertEqual(self.source.read_bytes(), self.target.read_bytes())
        self.assertEqual(0o640, self.target.stat().st_mode & 0o777)
        self.assertEqual((self.before.st_mode, self.before.st_uid, self.before.st_gid, self.before.st_mtime_ns),
                         tuple(getattr(self.source.stat(), name) for name in
                               ("st_mode", "st_uid", "st_gid", "st_mtime_ns")))
        with patch.dict(os.environ, {"LINGXI_DDS_PROFILE": str(self.target)}):
            self.assertEqual(self.target, selected_profile())
            import live_points
            env = live_points.environment(self.root, self.root)
            self.assertEqual(str(self.target), env["FASTRTPS_DEFAULT_PROFILES_FILE"])
            self.assertEqual(str(self.target), env["LINGXI_DDS_PROFILE"])

    def test_changed_source_and_existing_destination_are_rejected_without_overwrite(self):
        with self.assertRaisesRegex(ValueError, "verified_vendor"):
            self.provision(self.source, self.target, "f" * 64, os.getegid())
        self.assertFalse(self.target.exists())
        self.target.write_bytes(b"do not overwrite")
        with self.assertRaises(ValueError):
            self.provision(self.source, self.target, self.expected, os.getegid())
        self.assertEqual(b"do not overwrite", self.target.read_bytes())

    def test_symlinks_writable_profile_and_unsafe_directory_are_rejected(self):
        self.target.symlink_to(self.source)
        with self.assertRaises(OSError):
            self.provision(self.source, self.target, self.expected, os.getegid())
        with patch.dict(os.environ, {"LINGXI_DDS_PROFILE": str(self.target)}):
            with self.assertRaises(ValueError):
                selected_profile()
        self.target.unlink()
        self.directory.chmod(0o777)
        with self.assertRaises(ValueError):
            self.provision(self.source, self.target, self.expected, os.getegid())
        self.directory.chmod(0o750)
        self.provision(self.source, self.target, self.expected, os.getegid())
        self.target.chmod(0o666)
        with patch.dict(os.environ, {"LINGXI_DDS_PROFILE": str(self.target)}):
            with self.assertRaises(ValueError):
                selected_profile()
        for value in ("relative.xml", ""):
            with patch.dict(os.environ, {"LINGXI_DDS_PROFILE": value}), self.assertRaises(ValueError):
                selected_profile()

    def test_child_validates_the_selected_transport_file_against_original_evidence(self):
        from test_live_point_runtime import FrozenInputContract
        case = FrozenInputContract()
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.provision(self.source, self.target, self.expected, os.getegid())
        case.baseline["runs"]["start"]["ddsProfileSha256"] = self.expected
        previous = case.modules["verify_slam_capture"].sha256.side_effect
        case.modules["verify_slam_capture"].sha256.side_effect = lambda path: (
            hashlib.sha256(path.read_bytes()).hexdigest() if path == self.target else previous(path))
        import live_child
        with patch.dict(os.environ, {"LINGXI_DDS_PROFILE": str(self.target)}):
            live_child.verified_inputs(case.args)
            self.target.write_bytes(b"tampered transport")
            with self.assertRaisesRegex(ValueError, "same_verified_live_inputs"):
                live_child.verified_inputs(case.args)

    def test_live_ros_environment_uses_the_same_product_copy(self):
        self.provision(self.source, self.target, self.expected, os.getegid())
        import live_points
        with patch.dict(os.environ, {"LINGXI_DDS_PROFILE": str(self.target)}):
            env = live_points.environment(self.root, self.root)
        self.assertEqual(str(self.target), env["FASTRTPS_DEFAULT_PROFILES_FILE"])
        self.assertEqual(str(self.target), env["LINGXI_DDS_PROFILE"])
        with patch.dict(os.environ, env, clear=True):
            from lingxi_slam.dds_profile import selected_profile
            self.assertEqual(self.target, selected_profile())

    def test_only_product_mapping_services_receive_vendor_read_group(self):
        binding = "BindReadOnlyPaths=/etc/lingxi-next/dds/ros_dds_configuration.xml:/agibot/software/entry/cfg/ros_dds_configuration.xml"
        for name in ("lingxi-next-independent-mapping.service", "lingxi-next-independent-slam.service"):
            with self.subTest(name=name):
                service = (ROOT / "deploy" / name).read_text(encoding="utf-8")
                self.assertIn("SupplementaryGroups=manager", service)
                self.assertIn(binding, service)
                self.assertIn("User=lingxi-next", service)


if __name__ == "__main__":
    unittest.main()
