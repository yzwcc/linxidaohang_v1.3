"""Cross-build plan and artifact checks, not target runtime acceptance."""
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec.loader.exec_module(result)
    return result


build = module("build_slam_arm64_trial")
download = module("prepare_slam_arm64_cache")


class Arm64Plans(unittest.TestCase):
    def test_apt_always_uses_download_only_and_private_state(self):
        for architecture in ("amd64", "arm64"):
            root = Path("/private-cache") / architecture
            update, fetch = download.apt_commands(root, architecture)
            self.assertEqual("update", update[-1])
            self.assertIn("--download-only", fetch)
            self.assertNotIn("sudo", fetch)
            self.assertNotIn("--allow-unauthenticated", fetch)
            self.assertIn("Dir::State::status=" + str(root / "status"), fetch)
            self.assertIn("Dir::Etc::sourceparts=-", fetch)
            self.assertIn("APT::Architecture=" + architecture, fetch)

    def test_download_origins_are_signed_official_jammy_archives(self):
        for architecture in ("amd64", "arm64"):
            text = download.sources(architecture)
            self.assertIn("https://", text)
            self.assertIn("signed-by=" + str(download.KEYRING), text)
            self.assertNotIn("trusted=yes", text)
            for line in text.splitlines():
                self.assertIn("jammy", line)
                self.assertNotIn("noble", line)
        self.assertIn("archive.ubuntu.com", download.sources("amd64"))
        self.assertIn("ports.ubuntu.com", download.sources("arm64"))

    def test_unknown_architecture_is_not_guessed(self):
        for function in (lambda: download.apt_commands(Path("unused"), "riscv64"),
                         lambda: download.sources("riscv64")):
            with self.assertRaisesRegex(ValueError, "unsupported_cache_architecture"):
                function()

    def test_non_linux_host_never_downloads_or_builds(self):
        with patch.object(build.platform, "system", return_value="Windows"):
            with self.assertRaisesRegex(ValueError, "linux_x86_64"):
                build.run("unused", "unused", "unused", "unused", 1)
        with patch.object(download.platform, "system", return_value="Windows"):
            with self.assertRaisesRegex(ValueError, "linux_x86_64"):
                download.run("unused")

    def test_existing_download_cache_is_never_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "keep"
            marker.write_text("preserve")
            with patch.object(download.platform, "system", return_value="Linux"), \
                    patch.object(download.platform, "machine", return_value="x86_64"), \
                    patch.object(download.os, "geteuid", return_value=1000, create=True):
                with self.assertRaisesRegex(ValueError, "existing_cache"):
                    download.run(root)
            self.assertEqual("preserve", marker.read_text())
            self.assertEqual([marker], list(root.iterdir()))

    def test_existing_build_results_are_never_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "keep"
            marker.write_text("preserve")
            with patch.object(build.platform, "system", return_value="Linux"), \
                    patch.object(build.platform, "machine", return_value="x86_64"), \
                    patch.object(build.os, "geteuid", return_value=1000, create=True):
                with self.assertRaisesRegex(ValueError, "existing_work"):
                    build.run(root / "sources", root / "cache", root / "native", root, 1)
            self.assertEqual("preserve", marker.read_text())
            self.assertEqual([marker], list(root.iterdir()))

    def test_failed_update_remains_failed_with_no_install_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keyring = root / "synthetic-keyring"
            keyring.write_bytes(b"SYNTHETIC NOT A REAL KEYRING")
            output = root / "new-cache"
            with patch.object(download.platform, "system", return_value="Linux"), \
                    patch.object(download.platform, "machine", return_value="x86_64"), \
                    patch.object(download.os, "geteuid", return_value=1000, create=True), \
                    patch.object(download, "KEYRING", keyring), \
                    patch.object(download.subprocess, "run",
                                 side_effect=download.subprocess.CalledProcessError(100, ["apt-get"])), \
                    patch("builtins.print"):
                with self.assertRaises(download.subprocess.CalledProcessError):
                    download.run(output)
                env = download.subprocess.run.call_args.kwargs["env"]
                self.assertEqual(str(output / "apt-bootstrap.conf"), env["APT_CONFIG"])
            report = json.loads((output / "download-report.json").read_text())
            self.assertEqual("failed_or_interrupted", report["result"])
            self.assertFalse(report["systemPackagesInstalled"])
            bootstrap = (output / "apt-bootstrap.conf").read_text()
            self.assertIn('Dir::Etc::parts "-"', bootstrap)

    def test_toolchain_finds_only_target_libraries_and_uses_jammy_compiler(self):
        text = build.toolchain_text("/trial")
        self.assertIn('set(CMAKE_SYSTEM_PROCESSOR "aarch64")', text)
        self.assertIn('set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM "NEVER")', text)
        for kind in ("LIBRARY", "INCLUDE", "PACKAGE"):
            self.assertIn('set(CMAKE_FIND_ROOT_PATH_MODE_' + kind + ' "ONLY")', text)
        self.assertIn("aarch64-linux-gnu-g++-11", text)
        self.assertIn("sysroot", text)
        for kind in ("EXE", "SHARED", "MODULE"):
            self.assertIn("CMAKE_" + kind + "_LINKER_FLAGS_INIT", text)
        self.assertIn("-L/trial/sysroot/usr/lib/aarch64-linux-gnu", text)

    def test_toolchain_rejects_cmake_interpolation_in_paths(self):
        for path in ('/bad"path', "/bad$path", "/bad\npath", "/bad;path"):
            with self.assertRaisesRegex(ValueError, "unsupported_cmake_path"):
                build.toolchain_text(path)

    def test_all_core_configures_use_the_same_cross_toolchain_without_running_targets(self):
        plan = build.cross_commands(Path("/sources"), Path("/trial"), 2)
        self.assertEqual(9, len(plan))
        for name, command in plan:
            if name.endswith("_configure"):
                self.assertIn(
                    "-DCMAKE_TOOLCHAIN_FILE=" + str(Path("/trial") / "arm64-toolchain.cmake"),
                    command,
                )
            self.assertNotIn("ctest", command)
            self.assertNotIn("qemu-aarch64", command)
            self.assertNotIn("ros2", command)

    def test_host_library_paths_include_cross_binutils_but_not_arm64_sysroot(self):
        with patch.dict(build.os.environ, {
            "ROS_DISTRO": "fixture", "CMAKE_PREFIX_PATH": "/fixture-native",
            "CPATH": "/fixture-include", "LD_LIBRARY_PATH": "/fixture-lib",
            "PKG_CONFIG_PATH": "/fixture-pkgconfig",
        }):
            env = build.cross_environment(Path("/trial"), Path("/native"))
        for key in ("ROS_DISTRO", "CMAKE_PREFIX_PATH", "CPATH", "PKG_CONFIG_PATH"):
            self.assertNotIn(key, env)
        libraries = env["LD_LIBRARY_PATH"].split(build.os.pathsep)
        self.assertIn(str(Path("/native") / "sysroot/usr/lib/x86_64-linux-gnu"), libraries)
        self.assertIn(str(Path("/trial") / "host-linker-libs"), libraries)
        self.assertNotIn(str(Path("/trial") / "toolchain/usr/lib/x86_64-linux-gnu"), libraries)
        self.assertNotIn(str(Path("/trial") / "sysroot/usr/lib/aarch64-linux-gnu"), libraries)
        self.assertNotIn("/fixture-lib", libraries)


class Arm64Artifacts(unittest.TestCase):
    def test_public_results_keep_cross_link_distinct_from_target_execution(self):
        evidence = json.loads(
            (ROOT / "docs/slam-arm64-build-evidence.json").read_text(encoding="utf-8")
        )
        self.assertEqual("arm64_cross_built_and_linked_not_executed", evidence["result"])
        self.assertEqual(build.PINS, evidence["sourceCommits"])
        expected = [name for name, _ in build.cross_commands(Path("/sources"), Path("/trial"), 2)]
        self.assertEqual(expected, [stage["name"] for stage in evidence["coreStages"]])
        for stage in evidence["coreStages"] + evidence["syntheticLinkStages"]:
            self.assertEqual(0, stage["exitCode"])
        self.assertEqual(8, len(evidence["artifacts"]))
        for artifact in evidence["artifacts"]:
            self.assertEqual("AArch64", artifact["machine"])
            self.assertEqual("ELF64", artifact["class"])
            self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")
        for digest in evidence["sha256"].values():
            self.assertIsNotNone(re.fullmatch(r"[0-9a-f]{64}", digest))
        self.assertTrue(all(value is False or value == 0 for value in evidence["boundaries"].values()))

    def fixture(self, path, machine=183):
        header = bytearray(20)
        header[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", header, 18, machine)
        path.write_bytes(header)

    def test_correct_machine_and_jammy_abi_are_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "explicit-synthetic-elf-header"
            self.fixture(path)
            with patch.object(build.subprocess, "check_output",
                              return_value="GLIBC_2.17\nGLIBC_2.35\nGLIBCXX_3.4.29\nCXXABI_1.3.13"):
                result = build.inspect_elf(path, Path("readelf"), {})
            self.assertEqual("AArch64", result["machine"])
            self.assertEqual("2.35", result["maxGlibcRequirement"])
            self.assertEqual("3.4.29", result["maxGLIBCXXRequirement"])
            self.assertEqual("1.3.13", result["maxCXXABIRequirement"])
            self.assertEqual(64, len(result["sha256"]))

    def test_native_x86_binary_cannot_be_reported_as_arm64(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "explicit-synthetic-elf-header"
            self.fixture(path, machine=62)
            with self.assertRaisesRegex(ValueError, "expected_aarch64"):
                build.inspect_elf(path, Path("readelf"), {})

    def test_empty_or_non_elf_file_is_not_an_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "explicit-synthetic-non-elf"
            path.write_bytes(b"fixture")
            with self.assertRaisesRegex(ValueError, "expected_little_endian"):
                build.inspect_elf(path, Path("readelf"), {})

    def test_newer_glibc_requirement_is_not_jammy_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "explicit-synthetic-elf-header"
            self.fixture(path)
            with patch.object(build.subprocess, "check_output", return_value="Name: GLIBC_2.38"):
                with self.assertRaisesRegex(ValueError, "newer_than_jammy"):
                    build.inspect_elf(path, Path("readelf"), {})


if __name__ == "__main__":
    unittest.main()
