"""Verify the exact r14 archive, compiled artifacts and isolated Linux lifecycle."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OLD = "1.0.19-20261017-r13"
NEW = "1.0.19-20261017-r14"
VERSION = "1.3.8-complete-20261017-r14"
BASE_SHA = "c283bb8ca867e533e098443281aba93c6d5e8a8bac45b7c67081cf3d8cc5e7ed"
JAVA_TESTS = ("VoiceGateTest", "WakeSessionGateTest", "Soc2WakeEvidenceProviderTest", "AgentSdkInterruptWireTest",
              "VoiceRegression", "ModelRecoveryTest", "FunctionalControlTest", "AgentConnectionStateTest")


def sha(data): return hashlib.sha256(data).hexdigest()


def run(args, env=None, cwd=None, timeout=180):
    result = subprocess.run(list(map(str, args)), env=env, cwd=cwd, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{args[0]} exited {result.returncode}\n{result.stdout[-7000:]}\n{result.stderr[-7000:]}")
    return result.stdout + result.stderr


def extract(raw, destination):
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        roots = {Path(member.name).parts[0] for member in archive}
        assert len(roots) == 1
        archive.extractall(destination, filter="data")
    return destination / roots.pop()


def checksums(root, filename):
    raw = (root / filename).read_bytes()
    assert b"\r" not in raw
    covered = set()
    for line in raw.decode("utf-8").splitlines():
        expected, name = line.split("  ", 1)
        assert name not in covered and sha((root / name).read_bytes()) == expected, name
        covered.add(name)
    assert covered == {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} - {filename}


def component(raw, version):
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        member = next(m for m in archive if m.name.endswith("/components/sparkrobot-lingxi-nav-internal-" + version + ".tar.gz"))
        return archive.extractfile(member).read()


def linux_lifecycle(bundle, new, old, work):
    assert os.geteuid() == 0
    secrets = work / "test.env"
    secrets.write_text("LINGXI_APP_ID=test\nLINGXI_APP_KEY=test\nLINGXI_APP_SECRET=test\nDOUBAO_API_KEY=test\nSOC2_HOST=10.0.1.42\n")
    test_env = os.environ | {"SPARKROBOT_INSTALL_TEST_MODE": "true", "SPARKROBOT_ALLOW_NON_ARM64": "true"}
    fresh = work / "fresh"
    fresh.mkdir()
    run(["bash", new / "install.sh", "--secrets", secrets], env=test_env | {"SPARKROBOT_INSTALL_ROOT": str(fresh)})
    assert (fresh / "opt/sparkrobot/lingxi-nav/current").resolve().name == NEW
    script = work / "outer-upgrade.sh"
    script.write_text('''set -Eeuo pipefail
source "$BUNDLE/install_all.sh" --help >/dev/null
need_run() { :; }
need_arm64() { :; }
assert_work_root() { :; }
verify_aimdk_runtime() { :; }
extract_components() { NAV_ROOT="$NEW_NAV"; }
curl() { return 0; }
wait_http() { return 0; }
install() {
  local args=() arg
  for arg in "$@"; do
    case "$arg" in /home/run/*) arg="$QA_ROOT$arg" ;; esac
    args+=("$arg")
  done
  command install "${args[@]}"
}
sudo() {
  if [[ "$1" == python3 && "$2" == "$BUNDLE/scripts/check_java_service.py" ]]; then return 0; fi
  if [[ "$1" == readlink ]]; then
    local value
    value="$(readlink -f "$QA_ROOT/opt/sparkrobot/lingxi-nav/current")"
    printf '%s\\n' "${value#"$QA_ROOT"}"
    return
  fi
  if [[ "$1" == sparkrobot-lingxi-internal ]]; then
    shift; bash "$QA_ROOT/usr/local/sbin/sparkrobot-lingxi-internal" "$@"; return
  fi
  local args=() arg
  for arg in "$@"; do
    case "$arg" in /etc/*|/opt/*|/usr/local/*) arg="$QA_ROOT$arg" ;; esac
    args+=("$arg")
  done
  "${args[@]}"
}
mkdir -p "$QA_ROOT/home/run"
if [[ "${QA_REJECT:-false}" == true ]]; then
  sudo() { [[ "$1" == readlink ]] && { echo /opt/sparkrobot/lingxi-nav/releases/1.0.19-20260921; return; }; return 98; }
fi
upgrade_product
''')
    results = {"fresh_install": "passed"}
    baselines = [(OLD, old)]
    for version in ("r11", "r12"):
        base_file = ROOT / f"v138_complete_{version}_deployment/dist/lingxi-v1.3.8-complete-20261017-{version}-deployment.tar.gz"
        if base_file.exists():
            release = "1.0.19-20261017-" + version
            baselines.append((release, extract(component(base_file.read_bytes(), release), work / version)))
    for version, baseline in baselines:
        root = work / ("install-" + version)
        root.mkdir()
        env = test_env | {"SPARKROBOT_INSTALL_ROOT": str(root)}
        run(["bash", baseline / "install.sh", "--secrets", secrets], env=env)
        current = root / "opt/sparkrobot/lingxi-nav/current"
        properties = root / "etc/sparkrobot/lingxi-nav/lingxi-nav.properties"
        runtime = root / "etc/sparkrobot/lingxi-nav/runtime.env"
        state = root / "var/lib/sparkrobot/lingxi-nav"
        properties.write_text(properties.read_text() + "custom.preserve=yes\n")
        config = state / "unified_nav_config.json"
        data = json.loads(config.read_text())
        data["navigation"]["default_max_forward_speed"] = 0.37
        config.write_text(json.dumps(data))
        customer = state / "maps/customer.json"
        customer.write_text('{"tour_tasks":[{"id":"keep","steps":[{"point_id":"p1"}]}],"points":[{"id":"p1"}]}')
        before = [p.read_bytes() for p in (properties, runtime, customer)]
        outer_env = env | {"BUNDLE": str(bundle), "NEW_NAV": str(new), "QA_ROOT": str(root)}
        run(["bash", script], env=outer_env)
        assert current.resolve().name == NEW
        assert [p.read_bytes() for p in (properties, runtime, customer)] == before
        assert json.loads(config.read_text())["navigation"] == data["navigation"]
        internal = root / "usr/local/sbin/sparkrobot-lingxi-internal"
        run(["bash", internal, "verify"], env=env)
        assert (current / "scripts/agent_connection_status.py").is_file()
        duplicate = subprocess.run(["bash", str(new / "install.sh")], env=env, capture_output=True)
        assert duplicate.returncode != 0
        run(["bash", script], env=outer_env)
        rejected = subprocess.run(["bash", str(script)], env=outer_env | {"QA_REJECT": "true"}, capture_output=True)
        assert rejected.returncode != 0 and "r11、r12、r13 或当前 r14" in rejected.stderr.decode()
        assert current.resolve().name == NEW
        run(["bash", internal, "rollback", version], env=env)
        shutil.copyfile(bundle / ("scripts/control-" + version.rsplit("-", 1)[1] + ".sh"), root / "usr/local/sbin/sparkrobot-lingxi-complete")
        assert current.resolve().name == version
        run(["bash", internal, "verify"], env=env)
        assert [p.read_bytes() for p in (properties, runtime, customer)] == before
        results[version + "_upgrade_reinstall_rollback_preservation"] = "passed"
    return results


def verify(args):
    raw = args.archive.read_bytes()
    sidecar = args.archive.with_name(args.archive.name + ".sha256").read_bytes()
    assert b"\r" not in sidecar and sidecar.decode().strip() == sha(raw) + "  " + args.archive.name
    baseline = (ROOT / "v138_complete_r13_deployment/dist/lingxi-v1.3.8-complete-20261017-r13-deployment.tar.gz").read_bytes()
    assert sha(baseline) == BASE_SHA
    report = {"status": "passed", "version": VERSION, "tested_archive_sha256": sha(raw),
              "physical_robot": "not_run", "cloud_auth": "not_run", "acoustic_hardware": "not_run"}
    with tempfile.TemporaryDirectory(prefix="lingxi-r14-qa-") as directory:
        work = Path(directory)
        bundle = extract(raw, work / "bundle")
        checksums(bundle, "SHA256SUMS.txt")
        old_bundle = extract(baseline, work / "old-bundle")
        new = extract(component(raw, NEW), work / "new")
        old = extract(component(baseline, OLD), work / "old")
        checksums(new, "SHA256SUMS")
        meta = json.loads((bundle / "bundle-manifest.json").read_text(encoding="utf-8"))
        assert meta["version"] == VERSION and meta["navigation_release"] == NEW and meta["source_archive_sha256"] == BASE_SHA
        for name, expected in meta["components"].items(): assert sha((bundle / "components" / name).read_bytes()) == expected
        for name in ("aimdk-aarch64-1bde262f-artifacts.7z", "sparkrobot-lingxi-kb-1.0.2-20260902.zip"):
            assert (bundle / "components" / name).read_bytes() == (old_bundle / "components" / name).read_bytes()
        manifest = json.loads((new / "payload-manifest.json").read_text())
        files = {entry["path"] for entry in manifest["files"]}
        assert files == {p.relative_to(new / "payload").as_posix() for p in (new / "payload").rglob("*") if p.is_file()}
        changes = []
        for entry in manifest["files"]:
            data = (new / "payload" / entry["path"]).read_bytes()
            assert sha(data) == entry["sha256"] and len(data) == entry["size"]
            previous = old / "payload" / entry["path"]
            if not previous.exists() or previous.read_bytes() != data: changes.append(entry["path"])
        assert "java/robot-local-navigation-java-all.jar" in changes and "console/dist/robotClient.js" in changes
        for path in (new / "source/navigation/robot_local_navigation_v3").glob("*.py"):
            assert path.read_bytes() == (new / "payload/navigation/robot_local_navigation_v3" / path.name).read_bytes()
        assert (new / "scripts/agent_connection_status.py").is_file()
        assert (bundle / "tests/navigation/robot_local_navigation_v3/sdk_motion_controller.py").read_bytes() == (new / "payload/navigation/robot_local_navigation_v3/sdk_motion_controller.py").read_bytes()
        for name in ("r11", "r12", "r13"):
            source = old_bundle / ("control_all.sh" if name == "r13" else "scripts/control-" + name + ".sh")
            assert (bundle / ("scripts/control-" + name + ".sh")).read_bytes() == source.read_bytes()
        assert NEW in (bundle / "install_all.sh").read_text(encoding="utf-8")
        assert (bundle / "VERSION").read_text().strip() == "LINGXI_COMPLETE_VERSION=" + VERSION
        assert json.loads((new / "release-info.json").read_text())["version"] == NEW
        for path in (bundle / "docs").glob("*.md"):
            assert path.read_bytes() == (new / "docs" / path.name).read_bytes()
        for name in ("node", "java/bin/java"):
            header = (new / "payload/runtime" / name).read_bytes()[:20]
            assert header[:4] == b"\x7fELF" and int.from_bytes(header[18:20], "little") == 183
        for path in (new / "payload/navigation").rglob("*.py"): compile(path.read_bytes(), str(path), "exec")
        jar = new / "payload/java/robot-local-navigation-java-all.jar"
        with zipfile.ZipFile(jar) as source:
            assert source.testzip() is None
            assert "com/agibot/aiem/nav/LingxiNavAgentApp.class" in source.namelist()
            assert "com/agibot/aiem/nav/AgentConnectionState.class" in source.namelist()
        env = os.environ | {"PYTHONPATH": str(new / "payload/navigation") + os.pathsep + str(new / "source/navigation"),
                            "R14_SCRIPTS": str(new / "scripts"),
                            "SDK_CONTROLLER": str(new / "payload/navigation/robot_local_navigation_v3/sdk_motion_controller.py")}
        service_env = env | {"PYTHONPATH": str(bundle / "scripts")}
        report["java_launch_policy"] = run([sys.executable, bundle / "tests/test_java_service.py"], env=service_env).strip()
        report["motion_receipts"] = run([sys.executable, bundle / "tests/test_motion.py"], env=env).strip()
        report["motion_confirmation"] = run([sys.executable, bundle / "tests/test_motion_confirmation.py"], env=env).strip()
        report["connection_and_process_orchestration"] = run([sys.executable, bundle / "tests/test_functional_runtime.py"], env=env).strip()
        if args.linux_lifecycle:
            scripts = [*bundle.rglob("*.sh"), *new.rglob("*.sh")]
            for path in scripts:
                assert b"\r" not in path.read_bytes(); run(["bash", "-n", path])
            blocks = 0
            for guide in (bundle / "docs").glob("*.md"):
                for text in re.findall(r"```bash\n(.*?)```", guide.read_text(encoding="utf-8"), re.S):
                    block = work / "guide-block.sh"; block.write_text(text); run(["bash", "-n", block]); blocks += 1
            report["bash_syntax"] = len(scripts)
            report["guide_bash_syntax_blocks"] = blocks
            report["storage_permissions"] = run([sys.executable, bundle / "tests/test_storage_permissions.py"], env=env).strip()
            report["lifecycle"] = linux_lifecycle(bundle, new, old, work)
        else:
            report["python_regression"] = run([sys.executable, "-m", "pytest", "-q", new / "source/navigation/test"], env=env, cwd=work).strip()
            sdk = new / "payload/java/linksoul-agentsdk-1.4.0.jar"
            cp = os.pathsep.join(map(str, (jar, sdk)))
            java_home = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
            javac = java_home / "javac.exe" if os.name == "nt" else Path(shutil.which("javac"))
            classes = work / "java-classes"; classes.mkdir()
            run([javac, "-encoding", "UTF-8", "--release", "17", "-cp", cp, "-d", classes, *sorted((new / "source/java/src/main/java").rglob("*.java"))])
            with zipfile.ZipFile(jar) as archive:
                compiled = {p.relative_to(classes).as_posix(): p.read_bytes() for p in classes.rglob("*.class")}
                packaged = {n: archive.read(n) for n in archive.namelist() if n.startswith("com/agibot/aiem/nav/") and n.endswith(".class")}
                assert compiled == packaged, "Java source does not reproduce packaged classes"
            tests = work / "java-tests"; tests.mkdir()
            run([javac, "-encoding", "UTF-8", "--release", "17", "-cp", cp, "-d", tests, *sorted((new / "source/java/src/test/java").rglob("*.java"))])
            results = {}
            for name in JAVA_TESTS:
                output = run([javac.with_name("java.exe" if os.name == "nt" else "java"), "-cp", str(tests) + os.pathsep + cp, "com.agibot.aiem.nav." + name])
                results[name] = [line for line in output.splitlines() if "passed" in line.lower() or "assertions" in line.lower()][-2:]
            report["java_regression"] = results
            server = new / "source/server"
            deps = ROOT / "server/node_modules"
            junction = server / "node_modules"
            if os.name == "nt":
                run(["cmd", "/c", "mklink", "/J", junction, deps])
            else:
                junction.symlink_to(deps, target_is_directory=True)
            try:
                node = shutil.which("node")
                run([node, deps / "typescript/bin/tsc", "-p", "tsconfig.json"], cwd=server)
                run([node, server / "scripts/copy-public.cjs"], cwd=server)
                compiled = {p.relative_to(server / "dist").as_posix(): p.read_bytes() for p in (server / "dist").rglob("*") if p.is_file()}
                packaged = {p.relative_to(new / "payload/console/dist").as_posix(): p.read_bytes() for p in (new / "payload/console/dist").rglob("*") if p.is_file()}
                assert compiled == packaged, "Console source does not reproduce final dist"
                result_file = work / "server-report.json"
                run([node, deps / "vitest/vitest.mjs", "run", "--reporter=json", "--outputFile=" + str(result_file)], cwd=server)
                test_report = json.loads(result_file.read_text(encoding="utf-8"))
                assert test_report["success"] and test_report["numFailedTests"] == 0
                report["server_regression"] = {k: test_report[k] for k in ("numTotalTests", "numPassedTests", "numFailedTests")}
            finally:
                if os.name == "nt": os.rmdir(junction)
                else: junction.unlink()
        report.update(integrity="passed", source_to_runtime="passed", runtime_files=len(files), runtime_changes=sorted(changes),
                      unchanged_aimdk_and_kb="passed", arm64_runtime="passed")
    args.report.parent.mkdir(exist_ok=True, parents=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--linux-lifecycle", action="store_true")
    verify(parser.parse_args())
