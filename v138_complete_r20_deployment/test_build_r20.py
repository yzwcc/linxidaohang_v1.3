"""Regression tests for the r20 complete bundle integration."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
NAME = "lingxi-v1.3.8-complete-20261021-r20-deployment"
ARCHIVE = DIST / (NAME + ".tar.gz")
RUNTIME = "1.0.20-20261021-r20"
JAR_SHA = "61055b65c24e086d2fe9f4459e3050d893dee4ed2c8a20abf54b8a0f6511fdf2"
BRIDGE_SHA = "ded2c941c4f033b22d688f9266407ea8fc7578bc0a45f50d39a7d7391182115c"
JAR = "java/robot-local-navigation-java-all.jar"
BRIDGE = "navigation/robot_local_navigation_v3/lingxi_nav_bridge.py"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_outer():
    with tarfile.open(ARCHIVE, "r:gz") as archive:
        prefix = NAME + "/"
        result = {}
        for item in archive.getmembers():
            if not item.isfile() or not item.name.startswith(prefix):
                continue
            name = item.name[len(prefix) :]
            if name in result:
                raise AssertionError("duplicate outer path: " + name)
            result[name] = archive.extractfile(item).read()
        return result


def read_component(outer):
    name = next(
        path
        for path in outer
        if path.startswith("components/sparkrobot-lingxi-nav-internal-")
        and path.endswith(RUNTIME + ".tar.gz")
    )
    raw = outer[name]
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        prefix = "sparkrobot-lingxi-nav-internal-" + RUNTIME + "/"
        result = {
            item.name[len(prefix) :]: archive.extractfile(item).read()
            for item in archive.getmembers()
            if item.isfile() and item.name.startswith(prefix)
        }
    return raw, result


def read_sums(files, name):
    lines = files[name].decode("utf-8").splitlines()
    expected = {}
    for line in lines:
        digest, path = line.split("  ", 1)
        if path in expected:
            raise AssertionError("duplicate checksum entry: " + path)
        expected[path] = digest
    if set(expected) != set(files) - {name}:
        raise AssertionError("checksum manifest coverage mismatch: " + name)
    for path, digest in expected.items():
        if sha256(files[path]) != digest:
            raise AssertionError("checksum mismatch: " + path)


class R20BundleTests(unittest.TestCase):
    def test_sidecar_and_nested_manifests_cover_every_file(self):
        raw = ARCHIVE.read_bytes()
        sidecar = ARCHIVE.with_name(ARCHIVE.name + ".sha256").read_text("ascii")
        self.assertEqual(sidecar, f"{sha256(raw)}  {ARCHIVE.name}\n")
        self.assertNotIn("\r", sidecar)
        outer = read_outer()
        read_sums(outer, "SHA256SUMS.txt")
        _, component = read_component(outer)
        read_sums(component, "SHA256SUMS")
        helper = {
            name[len("scripts/native-tour-quiet/") :]: data
            for name, data in outer.items()
            if name.startswith("scripts/native-tour-quiet/")
        }
        read_sums(helper, "SHA256SUMS")

    def test_r20_runtime_contains_exact_cumulative_increment_payload(self):
        outer = read_outer()
        _, component = read_component(outer)
        release = json.loads(component["release-info.json"])
        payload_manifest = json.loads(component["payload-manifest.json"])
        self.assertEqual(release["version"], RUNTIME)
        self.assertEqual(release["merged_voice_control_silent_fix"], "1.0.10-20261021")
        payload_entries = {entry["path"]: entry for entry in payload_manifest["files"]}
        self.assertEqual(sha256(component["payload/" + JAR]), JAR_SHA)
        self.assertEqual(sha256(component["payload/" + BRIDGE]), BRIDGE_SHA)
        self.assertEqual(payload_entries[JAR]["sha256"], JAR_SHA)
        self.assertEqual(payload_entries[BRIDGE]["sha256"], BRIDGE_SHA)
        self.assertIn("payload/navigation/robot_local_navigation_v3/near_goal_arrival.py",
                      component)
        merged = json.loads(component["merged-increments.json"])
        self.assertEqual(merged["runtime_release"], RUNTIME)
        self.assertEqual(merged["effective_jar_sha256"], JAR_SHA)

    def test_native_tour_helper_cannot_downgrade_runtime(self):
        outer = read_outer()
        _, component = read_component(outer)
        prefix = "scripts/native-tour-quiet/"
        helper_meta = json.loads(outer[prefix + "release.json"])
        self.assertEqual(helper_meta["allowed_releases"], [RUNTIME])
        self.assertEqual(helper_meta["patched_jar_sha256"], JAR_SHA)
        self.assertEqual(sha256(outer[prefix + "payload/" + JAR]), JAR_SHA)
        self.assertEqual(sha256(outer[prefix + "payload/" + BRIDGE]), BRIDGE_SHA)
        setup = outer[prefix + "full_setup.py"].decode("utf-8")
        self.assertIn(f'EXPECTED_RELEASE = "{RUNTIME}"', setup)
        arrival = outer[prefix + "arrival_runtime.py"].decode("utf-8")
        self.assertIn("control_reply_fix_version", arrival)
        self.assertIn("arrival_tts_fix_version", arrival)
        self.assertEqual(
            outer[prefix + "source/java/main/com/agibot/aiem/nav/LingxiNavAgentApp.java"],
            component["source/java/src/main/java/com/agibot/aiem/nav/LingxiNavAgentApp.java"],
        )
        self.assertEqual(
            outer[prefix + "source/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py"],
            component["source/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py"],
        )
        self.assertEqual(
            sha256(component["payload/navigation/robot_local_navigation_v3/near_goal_arrival.py"]),
            helper_meta["payload_sha256"][
                "navigation/robot_local_navigation_v3/near_goal_arrival.py"
            ],
        )

    def test_installer_runtime_route_upgrade_allowlist_and_r18_rollback_control(self):
        outer = read_outer()
        installer = outer["install_all.sh"].decode("utf-8")
        self.assertIn(f'NAV_VERSION="{RUNTIME}"', installer)
        self.assertIn('EXPECTED_RELEASE="/opt/sparkrobot/lingxi-nav/releases/${NAV_VERSION}"',
                      installer)
        self.assertIn("/opt/sparkrobot/lingxi-nav/releases/1.0.19-20261019-r18", installer)
        self.assertIn("upgrade_product", installer)
        control = outer["control_all.sh"].decode("utf-8")
        self.assertIn(f'NAV_VERSION="{RUNTIME}"', control)
        r18 = outer["scripts/control-r18.sh"].decode("utf-8")
        self.assertIn('NAV_VERSION="1.0.19-20261019-r18"', r18)
        self.assertNotIn(f'NAV_VERSION="{RUNTIME}"', r18)
        verify = outer["scripts/03-verify-after-web-start.sh"].decode("utf-8")
        self.assertIn(RUNTIME, verify)

    def test_guides_are_present_and_match_install_directory(self):
        outer = read_outer()
        standard = outer["docs/deployment-guide.md"].decode("utf-8")
        precompiled = outer["docs/precompiled-aimdk-guide.md"].decode("utf-8")
        for document in (standard, precompiled):
            self.assertIn(NAME, document)
            self.assertIn(RUNTIME, document)
        self.assertIn("voice-control-silent-fix-1.0.10-20261021", standard)
        self.assertIn("voice-control-silent-fix-1.0.10-20261021", precompiled)
        self.assertIn("bash ./install_all.sh upgrade", standard)
        self.assertIn("不要运行 `/home/agi/01-replace-aimdk.sh`", precompiled)
        readme = outer["README.md"].decode("utf-8")
        self.assertIn("docs/deployment-guide.md", readme)
        self.assertIn("docs/precompiled-aimdk-guide.md", readme)

    @unittest.skipIf(os.name == "nt", "bash syntax validation requires POSIX")
    def test_all_packaged_shell_scripts_parse(self):
        outer = read_outer()
        _, component = read_component(outer)
        shell_scripts = {
            **{name: data for name, data in outer.items() if name.endswith(".sh")},
            **{
                "component/" + name: data
                for name, data in component.items()
                if name.endswith(".sh")
            },
        }
        with tempfile.TemporaryDirectory(prefix="lingxi-r20-helper-") as directory:
            for index, (name, data) in enumerate(sorted(shell_scripts.items())):
                target = Path(directory) / f"script-{index}.sh"
                target.write_bytes(data)
                result = subprocess.run(
                    ["bash", "-n", str(target)],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(result.returncode, 0, name + "\n" + result.stderr)

    def test_native_tour_voice_validator_regression_suite(self):
        outer = read_outer()
        prefix = "scripts/native-tour-quiet/"
        with tempfile.TemporaryDirectory(prefix="lingxi-r20-validator-") as directory:
            helper_root = Path(directory) / "native-tour-quiet"
            for name, data in outer.items():
                if not name.startswith(prefix):
                    continue
                target = helper_root / name[len(prefix) :]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            environment = dict(os.environ, LINGXI_TEST_HELPER=str(helper_root))
            result = subprocess.run(
                [sys.executable, str(helper_root / "test_voice_validator.py")],
                capture_output=True,
                text=True,
                timeout=30,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
