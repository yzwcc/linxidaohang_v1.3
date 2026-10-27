#!/usr/bin/env python3
"""Run real deploy scripts in a private mount namespace and disposable chroot.

Requires Linux root and mount namespaces. systemd, architecture and health are
stubbed; file operations, certificates, checksums, accounts and backups are real.
No host service or robot is contacted. ARM64 runtime execution is not tested.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


def run(*args, **options):
    return subprocess.run(args, check=True, text=True, capture_output=True, **options)


def script(path: Path, body: str):
    path.write_text("#!/bin/sh\nset -eu\n" + body + "\n")
    path.chmod(0o755)


def prepare(root: Path, deploy: Path):
    for name in ("etc/systemd/system", "etc/ssl", "opt", "var/lib", "var/log", "var/mail",
                 "tmp", "proc", "dev", "test-bin", "test-state", "agibot"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "tmp").chmod(0o1777)
    (root / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
    (root / "etc/group").write_text("root:x:0:\n")
    (root / "etc/shadow").write_text("root:!:20000:0:99999:7:::\n")
    (root / "etc/gshadow").write_text("root:!::\n")
    (root / "etc/nsswitch.conf").write_text("passwd: files\ngroup: files\nshadow: files\n")
    shutil.copy("/etc/ssl/openssl.cnf", root / "etc/ssl/openssl.cnf")
    (root / "agibot/vendor-marker").write_text("vendor-unchanged")
    script(root / "test-bin/uname", 'printf "aarch64\\n"')
    script(root / "test-bin/systemctl", """
printf '%s\\n' "$*" >> /test-state/systemctl.log
if [ "${1:-}" = cat ]; then test -f "/etc/systemd/system/${2:?}"; fi
""")
    script(root / "test-bin/curl", """
printf '%s\\n' "$*" >> /test-state/health.log
test ! -e /test-state/health-fail
""")
    for version in ("test-1", "test-2", "test-3"):
        package = root / f"package-{version}"
        shutil.copytree(deploy, package / "deploy")
        for runtime in ("node/bin", "java/bin"):
            (package / "runtime" / runtime).mkdir(parents=True)
        script(package / "runtime/node/bin/node", """
test "${1:-}" = --version || exit 64
printf 'v22-fixture\\n'
""")
        script(package / "runtime/java/bin/java", "printf 'java fixture\\n'")
        (package / "VERSION").write_text(version + "\n")
        manifest = "".join(
            f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.relative_to(package).as_posix()}\n"
            for file in sorted(package.rglob("*")) if file.is_file()
        )
        (package / "SHA256SUMS").write_text(manifest)


def isolate(root: Path):
    run("mount", "--make-rprivate", "/")
    for name in ("usr", "bin", "sbin", "lib", "lib64"):
        source = Path("/") / name
        if source.exists():
            target = root / name
            target.mkdir()
            run("mount", "--bind", str(source), str(target))
            run("mount", "-o", "remount,bind,ro", str(target))
    for name in ("null", "urandom", "random"):
        target = root / "dev" / name
        target.touch()
        run("mount", "--bind", f"/dev/{name}", str(target))
    run("mount", "-t", "proc", "-o", "ro,nosuid,nodev,noexec", "proc", str(root / "proc"))
    os.chroot(root)
    os.chdir("/")
    os.environ["PATH"] = "/test-bin:/usr/sbin:/usr/bin:/sbin:/bin"


class DeploymentLifecycle(unittest.TestCase):
    def command(self, version="test-1", success=True, ip="192.0.2.10"):
        result = subprocess.run(["bash", f"/package-{version}/deploy/install.sh",
                                 "--commissioning", "--robot-ip", ip],
                                text=True, capture_output=True)
        if success and result.returncode:
            self.fail(result.stdout + result.stderr)
        if not success:
            self.assertNotEqual(0, result.returncode, result.stdout)
        return result

    def test_install_upgrade_rollback_and_failure_barriers(self):
        with self.subTest("initial install, permissions and certificate"):
            self.command()
            self.assertEqual("/opt/lingxi-next/releases/test-1", os.readlink("/opt/lingxi-next/current"))
            self.assertEqual(0o600, Path("/etc/lingxi-next/robot.env").stat().st_mode & 0o777)
            self.assertEqual(0o600, Path("/etc/lingxi-next/pairing-code.txt").stat().st_mode & 0o777)
            self.assertEqual(0o640, Path("/etc/lingxi-next/tls/robot.key").stat().st_mode & 0o777)
            self.assertEqual(0o700, Path("/var/lib/lingxi-next").stat().st_mode & 0o777)
            certificate = run("openssl", "x509", "-in", "/etc/lingxi-next/tls/robot.crt",
                              "-noout", "-ext", "subjectAltName").stdout
            self.assertIn("IP Address:192.0.2.10", certificate)
            self.assertIn("IP Address:127.0.0.1", certificate)
            self.assertIn("DNS:localhost", certificate)
            health = Path("/test-state/health.log").read_text()
            self.assertIn("--cacert /etc/lingxi-next/tls/ca.crt", health)
            self.assertNotIn("--insecure", health)
        config = Path("/etc/lingxi-next/robot.env").read_bytes()
        ca = Path("/etc/lingxi-next/tls/ca.crt").read_bytes()
        Path("/var/lib/lingxi-next/state.json").write_text('{"preserved":true}\n')
        with self.subTest("upgrade preserves data, credentials and previous release"):
            self.command("test-2")
            self.assertEqual("/opt/lingxi-next/releases/test-2", os.readlink("/opt/lingxi-next/current"))
            self.assertEqual("/opt/lingxi-next/releases/test-1", os.readlink("/opt/lingxi-next/previous"))
            self.assertEqual(config, Path("/etc/lingxi-next/robot.env").read_bytes())
            self.assertEqual(ca, Path("/etc/lingxi-next/tls/ca.crt").read_bytes())
            self.assertIn("preserved", Path("/var/lib/lingxi-next/state.json").read_text())
            backups = list(Path("/opt/lingxi-next/backups").glob("pre-test-2-*/data-and-config.tar.gz"))
            self.assertEqual(1, len(backups))
            self.assertIn("var/lib/lingxi-next/state.json", run("tar", "-tzf", str(backups[0])).stdout)
        with self.subTest("rollback restores code and preserves data"):
            run("bash", "/package-test-2/deploy/rollback.sh")
            self.assertEqual("/opt/lingxi-next/releases/test-1", os.readlink("/opt/lingxi-next/current"))
            self.assertEqual(config, Path("/etc/lingxi-next/robot.env").read_bytes())
            self.assertIn("preserved", Path("/var/lib/lingxi-next/state.json").read_text())
        with self.subTest("duplicate version cannot overwrite a release"):
            self.command("test-2", success=False)
            self.assertEqual("/opt/lingxi-next/releases/test-1", os.readlink("/opt/lingxi-next/current"))
        with self.subTest("failed rollback health does not report success; recovery retains unique backups"):
            Path("/test-state/health-fail").touch()
            result = subprocess.run(["bash", "/package-test-1/deploy/rollback.sh"], text=True, capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertNotIn("Code rollback complete", result.stdout)
            Path("/test-state/health-fail").unlink()
            run("bash", "/package-test-1/deploy/rollback.sh")
            self.assertEqual("/opt/lingxi-next/releases/test-1", os.readlink("/opt/lingxi-next/current"))
            self.assertEqual(3, len(list(Path("/opt/lingxi-next/backups").glob("pre-rollback-*/data-and-config.tar.gz"))))
        with self.subTest("certificate address mismatch fails before release switch"):
            self.command("test-3", success=False, ip="192.0.2.11")
            self.assertEqual("/opt/lingxi-next/releases/test-1", os.readlink("/opt/lingxi-next/current"))
        with self.subTest("checksum mismatch cannot install"):
            Path("/package-test-3/VERSION").write_text("tampered\n")
            self.command("test-3", success=False)
            self.assertFalse(Path("/opt/lingxi-next/releases/tampered").exists())
        with self.subTest("rollback rejects a corrupted release before stopping services"):
            log = Path("/test-state/systemctl.log").read_bytes()
            Path("/opt/lingxi-next/releases/test-2/VERSION").write_text("tampered\n")
            result = subprocess.run(["bash", "/package-test-1/deploy/rollback.sh"], capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(log, Path("/test-state/systemctl.log").read_bytes())
        with self.subTest("no vendor files or old services changed"):
            self.assertEqual("vendor-unchanged", Path("/agibot/vendor-marker").read_text())
            lines = Path("/test-state/systemctl.log").read_text().splitlines()
            self.assertTrue(all("lingxi-next-" in line or line == "daemon-reload" for line in lines))
        with self.subTest("diagnostics honor configured port without exposing pairing credentials"):
            config_path = Path("/etc/lingxi-next/robot.env")
            config_path.write_text(config_path.read_text().replace("LINGXI_PORT=3000", "LINGXI_PORT=3443"))
            diagnostic = run("bash", "/package-test-1/deploy/diagnose.sh").stdout
            health_requests = Path("/test-state/health.log").read_text()
            self.assertIn("https://127.0.0.1:3443/health", health_requests)
            self.assertNotIn(Path("/etc/lingxi-next/pairing-code.txt").read_text().strip(), diagnostic)


def main():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SystemExit("Run on isolated Linux as root; see docs/VALIDATION.md")
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        isolate(Path(sys.argv[2]))
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DeploymentLifecycle))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    deploy = Path(__file__).resolve().parents[1] / "deploy"
    with tempfile.TemporaryDirectory(prefix="lingxi-next-install-") as temporary:
        root = Path(temporary)
        prepare(root, deploy)
        result = subprocess.run(["unshare", "--mount", "--fork", sys.executable,
                                 str(Path(__file__).resolve()), "--child", str(root)])
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
