#!/usr/bin/env python3
"""Run real deploy scripts in a private mount namespace and disposable chroot.

Requires Linux root and mount namespaces. systemd, architecture and health are
stubbed; file operations, certificates, checksums, accounts and backups are real.
No host service or robot is contacted. ARM64 runtime execution is not tested.
"""
from __future__ import annotations

import hashlib
import fcntl
import json
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
case "${1:-}" in
  cat) test -f "/etc/systemd/system/${2:?}" ;;
  is-active) test -f "/test-state/active-${3:?}" ;;
  is-enabled) test -f "/test-state/enabled-${3:?}" ;;
  stop)
    test ! -f /test-state/stop-fail || exit 1
    rm -f "/test-state/active-${2:?}" ;;
  enable)
    touch "/test-state/enabled-${3:?}" "/test-state/active-${3:?}"
    if test -f /test-state/terminate-enable; then kill -TERM "$PPID"; fi
    test ! -f /test-state/enable-fail ;;
  disable) rm -f "/test-state/enabled-${2:?}" ;;
  start) touch "/test-state/active-${2:?}" ;;
esac
""")
    script(root / "test-bin/curl", """
printf '%s\\n' "$*" >> /test-state/health.log
test ! -e /test-state/health-fail
if test -f /test-state/health-fail-release; then
  current=$(cat /opt/lingxi-next/current/VERSION)
  if test "$current" = "$(cat /test-state/health-fail-release)"; then
    printf 'failed-new-state\\n' > /var/lib/lingxi-next/state.json
    exit 1
  fi
fi
""")
    for version in (f"test-{index}" for index in range(10)):
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
        with self.subTest("first activation failure leaves no current release or enabled API"):
            Path("/test-state/health-fail").touch()
            result = self.command("test-0", success=False)
            self.assertFalse(Path("/opt/lingxi-next/current").is_symlink())
            self.assertFalse(Path("/etc/systemd/system/lingxi-next-api.service").exists())
            self.assertFalse(Path("/test-state/enabled-lingxi-next-api.service").exists())
            self.assertIn("Prior deployment restored", result.stderr)
            self.assertNotIn("Installed commissioning", result.stdout)
            self.assertTrue(Path("/etc/lingxi-next/pairing-code.txt").is_file())
            Path("/test-state/health-fail").unlink()
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
            self.assertEqual("/opt/lingxi-next/releases/test-1", os.readlink("/opt/lingxi-next/current"))
            self.assertEqual("/opt/lingxi-next/releases/test-2", os.readlink("/opt/lingxi-next/previous"))
            self.assertIn("Automatic recovery incomplete", result.stderr)
            self.assertFalse(Path("/test-state/active-lingxi-next-api.service").exists())
            Path("/test-state/health-fail").unlink()
            run("bash", "/package-test-1/deploy/rollback.sh")
            self.assertEqual("/opt/lingxi-next/releases/test-2", os.readlink("/opt/lingxi-next/current"))
            run("bash", "/package-test-1/deploy/rollback.sh")
            self.assertEqual("/opt/lingxi-next/releases/test-1", os.readlink("/opt/lingxi-next/current"))
            self.assertEqual(4, len(list(Path("/opt/lingxi-next/backups").glob("pre-rollback-*/data-and-config.tar.gz"))))
        with self.subTest("failed upgrade restores original pointers, data and units; restarts only prior API"):
            units = {file.name: file.read_bytes() for file in Path("/etc/systemd/system").glob("lingxi-next-*.service")}
            Path("/test-state/health-fail-release").write_text("test-4\n")
            log_offset = len(Path("/test-state/systemctl.log").read_text())
            result = self.command("test-4", success=False)
            self.assertIn("Prior deployment restored", result.stderr)
            self.assertEqual("/opt/lingxi-next/releases/test-1", os.readlink("/opt/lingxi-next/current"))
            self.assertEqual("/opt/lingxi-next/releases/test-2", os.readlink("/opt/lingxi-next/previous"))
            self.assertEqual('{"preserved":true}\n', Path("/var/lib/lingxi-next/state.json").read_text())
            self.assertEqual(config, Path("/etc/lingxi-next/robot.env").read_bytes())
            self.assertEqual(units, {file.name: file.read_bytes() for file in Path("/etc/systemd/system").glob("lingxi-next-*.service")})
            self.assertTrue(Path("/test-state/active-lingxi-next-api.service").exists())
            recovery_log = Path("/test-state/systemctl.log").read_text()[log_offset:]
            self.assertIn("start lingxi-next-api.service", recovery_log)
            self.assertNotIn("start lingxi-next-aimdk.service", recovery_log)
            self.assertNotIn("start lingxi-next-agent.service", recovery_log)
            Path("/test-state/health-fail-release").unlink()
        with self.subTest("enable failure and termination recover without activating autonomous services"):
            for version, marker in (("test-5", "enable-fail"), ("test-6", "terminate-enable")):
                Path("/test-state/" + marker).touch()
                result = self.command(version, success=False)
                self.assertIn("Prior deployment restored", result.stderr)
                self.assertEqual("/opt/lingxi-next/releases/test-1", os.readlink("/opt/lingxi-next/current"))
                Path("/test-state/" + marker).unlink()
        with self.subTest("uncertain stop blocks activation and automatic service restart"):
            Path("/test-state/stop-fail").touch()
            log_offset = len(Path("/test-state/systemctl.log").read_text())
            result = self.command("test-7", success=False)
            self.assertIn("Automatic recovery incomplete", result.stderr)
            self.assertEqual("/opt/lingxi-next/releases/test-1", os.readlink("/opt/lingxi-next/current"))
            self.assertNotIn("start ", Path("/test-state/systemctl.log").read_text()[log_offset:])
            Path("/test-state/stop-fail").unlink()
        with self.subTest("inactive disabled previous API is not restarted on upgrade failure"):
            Path("/test-state/active-lingxi-next-api.service").unlink()
            Path("/test-state/enabled-lingxi-next-api.service").unlink()
            Path("/test-state/enable-fail").touch()
            result = self.command("test-8", success=False)
            self.assertIn("Prior deployment restored", result.stderr)
            self.assertFalse(Path("/test-state/active-lingxi-next-api.service").exists())
            self.assertFalse(Path("/test-state/enabled-lingxi-next-api.service").exists())
            Path("/test-state/enable-fail").unlink()
        with self.subTest("concurrent install rejected before service changes"):
            log = Path("/test-state/systemctl.log").read_bytes()
            with Path("/opt/lingxi-next/.deployment.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = self.command("test-9", success=False)
                self.assertIn("Another install or rollback", result.stderr)
            self.assertEqual(log, Path("/test-state/systemctl.log").read_bytes())
            self.assertFalse(Path("/opt/lingxi-next/releases/test-9").exists())
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

    def test_z_independent_installed_entrypoints(self):
        """Execute installed Bash/Python with synthetic owners, never ROS/hardware."""
        current = Path("/opt/lingxi-next/current")
        independent = current / "independent"
        owners = independent / "source/robot-runtime/python"
        owners.mkdir(parents=True)
        (independent / "source/robot-slam").mkdir()
        stub = """import json, os, pathlib, sys
args = sys.argv[1:]
output = pathlib.Path(args[args.index('--output') + 1])
output.mkdir(mode=0o700)
result = {'args': args, 'web': os.environ['LINGXI_WEB_ROOT'], 'port': os.environ['LINGXI_PORT']}
(output / 'fixture-evidence.json').write_text(json.dumps(result))
print(json.dumps(result))
"""
        for profile in ("mapping", "slam"):
            (owners / ("managed_" + profile + ".py")).write_text(stub)
            assets = independent / "assets" / profile
            (assets / "api").mkdir(parents=True)
            (assets / "web").mkdir()
            (assets / "api/main.mjs").write_text("// synthetic API, not executed\n")
        config = Path("/etc/lingxi-next/independent")
        for name in ("mapping.json", "slam.json", "product selection.json", "acquisition.json"):
            (config / name).write_text('{"fixture":true}\n')
        setup = config / "setup.bash"
        setup.write_text('export FIXTURE_ROS_ONLY=1\n')
        env = {**os.environ, "LINGXI_INDEPENDENT_PRODUCT": "1", "LINGXI_ROS_SETUP": str(setup),
               "LINGXI_MAPPING_CONFIG": str(config / "mapping.json"),
               "LINGXI_SLAM_CONFIG": str(config / "slam.json"),
               "LINGXI_MAP_ROOT": "/var/lib/lingxi-next/maps"}

        def execute(mode, **selection):
            return subprocess.run(["bash", str(current / "deploy/run.sh"), mode],
                                  env={**env, **selection}, text=True, capture_output=True)

        for mode, port in (("independent-mapping", "3443"), ("independent-slam", "3444")):
            with self.subTest(mode=mode):
                result = execute(mode)
                self.assertEqual(0, result.returncode, result.stderr)
                value = json.loads(result.stdout)
                self.assertEqual(port, value['port'])
                self.assertIn('/independent/assets/', value['web'])
                args = value['args']
                self.assertEqual(env['LINGXI_MAP_ROOT'], args[args.index('--maps') + 1])
                self.assertEqual(mode == 'independent-mapping', '--persistent' in args)
                self.assertNotIn('--seconds', args)
                self.assertTrue((Path(args[args.index('--output') + 1]) / 'fixture-evidence.json').is_file())

        product = config / 'product selection.json'
        acquisition = config / 'acquisition.json'
        selections = {'LINGXI_PRODUCT_CONFIG': str(product),
                      'LINGXI_PRODUCT_CONFIG_SHA256': hashlib.sha256(product.read_bytes()).hexdigest(),
                      'LINGXI_ACQUISITION_SETTINGS': str(acquisition),
                      'LINGXI_ACQUISITION_SETTINGS_SHA256': hashlib.sha256(acquisition.read_bytes()).hexdigest()}
        selected = execute('independent-slam', **selections)
        self.assertEqual(0, selected.returncode, selected.stderr)
        args = json.loads(selected.stdout)['args']
        self.assertEqual(str(product), args[args.index('--product-config') + 1])
        self.assertEqual(str(acquisition), args[args.index('--acquisition-settings') + 1])
        for label in ('LINGXI_PRODUCT_CONFIG', 'LINGXI_ACQUISITION_SETTINGS'):
            for change in ({label + '_SHA256': ''}, {label + '_SHA256': '0' * 64}, {label: ''}):
                with self.subTest(label=label, change=change):
                    rejected = execute('independent-slam', **{**selections, **change})
                    self.assertEqual(78, rejected.returncode, rejected.stderr)
                    self.assertEqual('', rejected.stdout)
        for mode in ('independent-mapping', 'independent-slam'):
            self.assertEqual(78, execute(mode, LINGXI_INDEPENDENT_PRODUCT='0').returncode)
        for profile, other in (('mapping', 'slam'), ('slam', 'mapping')):
            unit = Path('/etc/systemd/system/lingxi-next-independent-' + profile + '.service').read_text()
            self.assertIn('RestartPreventExitStatus=78', unit)
            self.assertIn('Conflicts=lingxi-next-independent-' + other + '.service', unit)

        # This fixture checks the installed shell selection only. Java configuration
        # and actual HTTPS callbacks have separate tests using the real JVM.
        script(current / 'runtime/java/bin/java', 'printf "synthetic-java-selected\\n"')
        agent_env = {'LINKSOUL_AGENT_ID': 'synthetic-agent'}
        self.assertEqual(78, execute('agent', **agent_env).returncode)
        core_credentials = config / 'core-ai.json'
        core_credentials.write_text('{}')
        core = execute('agent', **agent_env, LINGXI_CREDENTIALS_FILE=str(core_credentials))
        self.assertEqual(0, core.returncode, core.stderr)
        self.assertEqual('synthetic-java-selected\n', core.stdout)
        product_agent = execute('agent', **agent_env, **selections)
        self.assertEqual(0, product_agent.returncode, product_agent.stderr)
        self.assertEqual('synthetic-java-selected\n', product_agent.stdout)
        for change in ({'LINGXI_PRODUCT_CONFIG_SHA256': ''}, {'LINGXI_PRODUCT_CONFIG': ''},
                       {'LINGXI_PRODUCT_CONFIG_SHA256': '0' * 64}, {'LINGXI_INDEPENDENT_PRODUCT': '0'},
                       {'LINKSOUL_AGENT_ID': ''}):
            rejected = execute('agent', **{**agent_env, **selections, **change})
            self.assertEqual(78, rejected.returncode, rejected.stderr)
            self.assertEqual('', rejected.stdout)
        unit = Path('/etc/systemd/system/lingxi-next-agent.service').read_text()
        self.assertIn('RestartPreventExitStatus=78', unit)


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
