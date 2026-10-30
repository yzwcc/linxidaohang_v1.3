"""Actual deployment filesystem transactions; synthetic packages and service-manager stub."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_slam_bundle as fixtures
import live_bundle as bundle
import slam_deploy as deploy


@unittest.skipUnless(sys.platform == "linux" and os.geteuid() != 0, "requires ordinary-user Linux filesystem semantics")
class SlamDeployment(unittest.TestCase):
    def setUp(self):
        fixtures.SlamBundleContracts.setUp(self)
        self.calls = []
        self.fail_start = False
        self.fail_cleanup = False
        self.addCleanup(patch.stopall)
        patch.object(deploy, "target").start()
        patch.object(deploy.subprocess, "run", side_effect=self.execute).start()
        for name in ("tls.key", "tls.crt", "config.json"):
            (self.root / name).write_text("synthetic external deployment input")
        self.install_root = self.root / "installed"

    prepared = fixtures.SlamBundleContracts.prepared

    def execute(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs))
        if arguments[:3] == ["/usr/bin/systemctl", "--user", "start"] and self.fail_start:
            raise subprocess.CalledProcessError(1, arguments)
        if (arguments[:3] == ["/usr/bin/systemctl", "--user", "stop"] and self.fail_cleanup
                and any(args[:3] == ["/usr/bin/systemctl", "--user", "start"] for args, _ in self.calls)):
            raise subprocess.CalledProcessError(1, arguments)
        return subprocess.CompletedProcess(arguments, 0)

    def arguments(self, name="first"):
        prepared, report = self.prepared(name)
        return argparse.Namespace(root=self.install_root, bundle=prepared.output / "bundle",
            manifest_sha256=report["manifestSha256"], config=self.root / "config.json", python=Path(sys.executable),
            tls_key=self.root / "tls.key", tls_cert=self.root / "tls.crt", port=3443, host="127.0.0.1", start=False)

    def second(self):
        file = self.assets / "web/assets/main.js"
        file.write_bytes(b"// second explicitly synthetic web revision\n")
        self.build["files"]["web/assets/main.js"] = {"sha256": bundle.sha256(file), "bytes": file.stat().st_size}
        self.build_digest = fixtures.save(self.assets / "build-manifest.json", self.build)
        return self.arguments("second")

    def test_install_upgrade_rollback_keep_maps_pairing_and_release_sources(self):
        first = self.arguments()
        result = deploy.install(first)
        self.assertEqual("installed_stopped", result["result"])
        self.assertFalse(any(args[2] == "start" for args, _ in self.calls if args[0] == "/usr/bin/systemctl"))
        maps = self.install_root / "maps"
        (maps / "preserved-map").write_text("synthetic retained map receipt")
        pairing = (self.install_root / "pairing-code.txt").read_bytes()
        second = self.second()
        second.start = True
        self.assertEqual("service_ready", deploy.install(second)["result"])
        self.assertEqual(second.manifest_sha256, deploy.pointer(self.install_root, "current"))
        self.assertEqual(first.manifest_sha256, deploy.pointer(self.install_root, "previous"))
        result = deploy.rollback(argparse.Namespace(root=self.install_root, start=False))
        self.assertEqual(first.manifest_sha256, result["release"])
        self.assertEqual(second.manifest_sha256, result["previous"])
        self.assertEqual(pairing, (self.install_root / "pairing-code.txt").read_bytes())
        self.assertEqual("synthetic retained map receipt", (maps / "preserved-map").read_text())
        self.assertIn("Type=notify", (self.install_root / deploy.SERVICE).read_text())
        self.assertIn("Restart=no", deploy.unit(self.install_root))
        self.assertEqual(0o700, maps.stat().st_mode & 0o777)
        self.assertEqual(0o600, (self.install_root / "pairing-code.txt").stat().st_mode & 0o777)

    def test_failed_activation_stops_new_process_and_restores_old_pointer_without_auto_restart(self):
        first = self.arguments()
        deploy.install(first)
        second = self.second()
        second.start, self.fail_start = True, True
        with self.assertRaises(subprocess.CalledProcessError):
            deploy.install(second)
        self.assertEqual(first.manifest_sha256, deploy.pointer(self.install_root, "current"))
        self.assertIsNone(deploy.pointer(self.install_root, "previous"))
        self.assertEqual(["/usr/bin/systemctl", "--user", "stop", deploy.SERVICE], self.calls[-1][0])
        self.assertTrue((self.install_root / "releases" / second.manifest_sha256).is_dir())

    def test_changed_external_configuration_stops_rollback_before_service_changes(self):
        first = self.arguments()
        deploy.install(first)
        second = self.second()
        alternate = self.root / "alternate.json"
        alternate.write_text("synthetic independent current config")
        second.config = alternate
        deploy.install(second)
        first.config.write_text("explicitly changed old config")
        before = len(self.calls)
        with self.assertRaisesRegex(ValueError, "unchanged_external"):
            deploy.rollback(argparse.Namespace(root=self.install_root, start=False))
        self.assertEqual(before, len(self.calls))
        self.assertEqual(second.manifest_sha256, deploy.pointer(self.install_root, "current"))

    def test_failed_cleanup_still_restores_pointer_and_preserves_map_catalog(self):
        first = self.arguments()
        deploy.install(first)
        marker = self.install_root / "maps" / "retained"
        marker.write_text("synthetic preserved map")
        second = self.second()
        second.start, self.fail_start, self.fail_cleanup = True, True, True
        with self.assertRaises(subprocess.CalledProcessError):
            deploy.install(second)
        self.assertEqual(first.manifest_sha256, deploy.pointer(self.install_root, "current"))
        self.assertEqual("synthetic preserved map", marker.read_text())

    def test_first_activation_failure_removes_pointer_even_if_cleanup_fails(self):
        first = self.arguments()
        first.start, self.fail_start, self.fail_cleanup = True, True, True
        with self.assertRaises(subprocess.CalledProcessError):
            deploy.install(first)
        self.assertIsNone(deploy.pointer(self.install_root, "current"))
        self.assertTrue((self.install_root / "releases" / first.manifest_sha256).is_dir())

    def test_long_root_rejected_for_nested_localization_socket_before_creation(self):
        root = Path("/tmp/" + "a" * 30)
        self.assertLess(len(os.fsencode(root / "maps" / ("a" * 32 + ".points.sock"))), 100)
        with self.assertRaisesRegex(ValueError, "short_direct"):
            with deploy.locked(root):
                self.fail("long root admitted")
        self.assertFalse(root.exists())

    def test_service_stop_budgets_cover_existing_owner_cleanup_before_systemctl_deadline(self):
        from mapping_owner import MappingOwnerLimits
        limits = MappingOwnerLimits()
        maximum = 2 * limits.service_stop_seconds + limits.api_stop_seconds + 7 * limits.signal_seconds
        values = dict(line.split("=", 1) for line in deploy.unit(self.install_root).splitlines() if "=" in line)
        self.assertGreater(int(values["TimeoutStopSec"]), maximum)
        deploy.systemctl("stop", deploy.SERVICE)
        self.assertGreater(self.calls[-1][1]["timeout"], int(values["TimeoutStopSec"]))

    def test_service_runner_uses_current_bundle_and_only_reviewed_environment(self):
        first = self.arguments()
        deploy.install(first)
        with patch.dict(os.environ, {"NOTIFY_SOCKET": "/synthetic-notify", "NODE_OPTIONS": "unused"}, clear=True), \
                patch.object(deploy.os, "execve") as execute:
            deploy.run(argparse.Namespace(root=self.install_root))
        executable, arguments, environment = execute.call_args.args
        self.assertEqual(str(first.python), executable)
        self.assertIn("--maps", arguments)
        self.assertIn(str(self.install_root / "maps"), arguments)
        self.assertNotIn("--seconds", arguments)
        output = Path(arguments[arguments.index("--output") + 1])
        self.assertEqual(deploy.RUN_ID_LENGTH, len(output.name))
        self.assertLess(len(os.fsencode(output / "r" / ("a" * 32) / "localization.sock")), 100)
        self.assertEqual("/synthetic-notify", environment["NOTIFY_SOCKET"])
        self.assertNotIn("NODE_OPTIONS", environment)
        self.assertFalse(any((self.install_root / "runs").iterdir()))


if __name__ == "__main__":
    unittest.main()
