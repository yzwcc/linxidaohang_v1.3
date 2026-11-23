import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_reference_native as native


SUFFIX = ".cpython-310-aarch64-linux-gnu.so"


def emit_native_output(root, machine="aarch64"):
    for rel in (*native.EXECUTABLES, *native.LIBRARIES, *native.REQUIRED_ASSETS,
                f"grid/grid_export_native{SUFFIX}", f"grid/nav_ground_native{SUFFIX}"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel in native.REQUIRED_ASSETS:
            path.write_bytes(b"resource\n")
            continue
        header = bytearray(64)
        header[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<HH", header, 16, 3, native.MACHINES[machine])
        path.write_bytes(header)
        if rel in native.EXECUTABLES:
            path.chmod(0o755)


@unittest.skipUnless(os.name == "posix", "native outputs require POSIX modes")
class BuildReferenceNativeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.source = self.root / "reference"
        self.source.mkdir()
        self.output = self.root / "output"

    def mock_build_host(self):
        for mock in (
            patch.object(native.platform, "machine", return_value="aarch64"),
            patch.object(native.sys, "platform", "linux"),
            patch.object(native.sys, "version_info", (3, 10)),
            patch.object(native.sysconfig, "get_config_var", return_value=SUFFIX),
            patch.dict(native.os.environ, {"ROS_DISTRO": "humble", "AMENT_PREFIX_PATH": "/ros/humble",
                                            "COLCON_DEFAULTS_FILE": "/do-not-use"}),
            patch.object(native.shutil, "which", return_value="/usr/bin/tool"),
            patch.object(native, "checked_source", return_value=self.source),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def test_plan_selects_only_required_packages_and_records_full_output_set(self):
        self.mock_build_host()

        def run(args, **kwargs):
            self.assertEqual(kwargs["cwd"], self.output)
            self.assertEqual(kwargs["env"]["CMAKE_BUILD_PARALLEL_LEVEL"], "2")
            self.assertNotIn("COLCON_DEFAULTS_FILE", kwargs["env"])
            if args[:2] == ("cmake", "--install"):
                emit_native_output(self.output)

        def git(_, command, *args):
            if command == "ls-tree":
                return b"LICENSE\0integrations/grid_export/vendor/lib/LICENSE\0integrations/fast_lio/ignored.priv\0"
            self.assertEqual(command, "show")
            return b"Tracked license\n"

        with patch.object(native.subprocess, "run", side_effect=run) as commands, patch.object(native, "git", side_effect=git):
            manifest = json.loads(native.build(self.source, self.output, 2).read_text())
        colcon = commands.call_args_list[0].args[0]
        self.assertEqual(colcon[colcon.index("--packages-select") + 1:colcon.index("--build-base")], native.PACKAGES)
        self.assertIn("--merge-install", colcon)
        self.assertEqual(commands.call_count, 4)
        self.assertEqual(manifest["referenceRevision"], native.REVISION)
        self.assertEqual(manifest["machine"], "aarch64")
        self.assertFalse(manifest["targetAccepted"])
        self.assertFalse(manifest["dependencyClosureVerified"])
        self.assertIn("notices/integrations/grid_export/vendor/lib/LICENSE", manifest["files"])
        self.assertNotIn("notices/integrations/fast_lio/ignored.priv", manifest["files"])
        self.assertEqual(manifest["files"][native.EXECUTABLES[0]], native.sha256(self.output / native.EXECUTABLES[0]))
        sums = (self.output / "SHA256SUMS.native").read_text().splitlines()
        self.assertEqual(sums, [f"{digest}  {name}" for name, digest in sorted(manifest["files"].items())])

    def test_partial_build_has_no_manifest_and_source_paths_are_refused(self):
        self.mock_build_host()
        with patch.object(native.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "colcon")) as commands:
            with self.assertRaisesRegex(ValueError, "outside of the reference checkout"):
                native.build(self.source, self.source / "output", 2)
            commands.assert_not_called()
            with self.assertRaises(subprocess.CalledProcessError):
                native.build(self.source, self.output, 2)
            self.assertFalse((self.output / "native-build.json").exists())
            with self.assertRaises(FileExistsError):
                native.build(self.source, self.output, 2)

    def test_inspection_rejects_wrong_architecture_unexecutable_or_linked_outputs(self):
        emit_native_output(self.output, "x86_64")
        with self.assertRaisesRegex(ValueError, "architecture mismatch"):
            native.inspect_install(self.output, "aarch64", SUFFIX)
        emit_native_output(self.output)
        target = self.output / native.EXECUTABLES[0]
        target.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "not executable"):
            native.inspect_install(self.output, "aarch64", SUFFIX)
        target.rename(self.root / "outside")
        target.symlink_to(self.root / "outside")
        with self.assertRaisesRegex(ValueError, "linked native output"):
            native.inspect_install(self.output, "aarch64", SUFFIX)

    def test_wrong_revision_or_dirty_source_never_reaches_build(self):
        root = os.fsencode(str(self.source))
        with patch.object(native, "git", side_effect=[root, b"wrong"]):
            with self.assertRaisesRegex(ValueError, "pinned reference checkout"):
                native.checked_source(self.source)
        with patch.object(native, "git", side_effect=[root, native.REVISION.encode(), b" M CMakeLists.txt"]):
            with self.assertRaisesRegex(ValueError, "clean reference checkout"):
                native.checked_source(self.source)

    def test_sparse_checkout_is_rejected_before_compilation(self):
        for name in (*native.INTEGRATIONS, "deploy", "robot_nav_tasks"):
            (self.source / name).mkdir(parents=True)
        with patch.object(native, "git", side_effect=[os.fsencode(str(self.source)), native.REVISION.encode(), b""]):
            with self.assertRaisesRegex(ValueError, "missing or linked reference build input"):
                native.checked_source(self.source)


if __name__ == "__main__":
    unittest.main()
