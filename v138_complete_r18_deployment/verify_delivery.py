"""Independently verify the final r18 archive and reproduce both r17 defects."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from build_complete import BASE_NAME, BASE_SHA, DOCS, NAME, NEAR, NEW, OLD, VERSION, ROOT

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("r17_verifier", ROOT / "v138_complete_r17_deployment/verify_delivery.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
run, sha, extract, checksums, component = (previous.run, previous.sha, previous.extract,
                                         previous.checksums, previous.component)


def negative(args, **kwargs):
    result = subprocess.run(list(map(str, args)), capture_output=True, encoding="utf-8",
                            errors="replace", timeout=40, **kwargs)
    text = result.stdout + result.stderr
    assert result.returncode != 0, "r17 unexpectedly passed the independent reproducer"
    return text


def lifecycle(bundle, new, work):
    secrets = work / "test.env"
    secrets.write_text("LINGXI_APP_ID=test\nLINGXI_APP_KEY=test\nLINGXI_APP_SECRET=test\nDOUBAO_API_KEY=test\nSOC2_HOST=10.0.1.42\n")
    common = os.environ | {"SPARKROBOT_INSTALL_TEST_MODE": "true", "SPARKROBOT_ALLOW_NON_ARM64": "true"}
    fresh = work / "fresh"
    fresh.mkdir()
    run(["bash", new / "install.sh", "--secrets", secrets], env=common | {"SPARKROBOT_INSTALL_ROOT": str(fresh)})
    assert (fresh / "opt/sparkrobot/lingxi-nav/current").resolve().name == NEW
    script = work / "outer-upgrade.sh"
    script.write_text(r'''set -Eeuo pipefail
source "$BUNDLE/install_all.sh" --help >/dev/null
need_run() { :; }
need_arm64() { :; }
assert_work_root() { :; }
verify_bundle() { :; }
verify_aimdk_runtime() { :; }
extract_components() { NAV_ROOT="$NEW_NAV"; }
curl() { :; }
wait_http() { :; }
native_tour_setup() { echo native >>"$QA_ROOT/native-events"; }
install() {
  local args=() arg
  for arg in "$@"; do
    case "$arg" in /home/run/*) arg="$QA_ROOT$arg" ;; esac
    args+=("$arg")
  done
  command install "${args[@]}"
}
sudo() {
  if [[ "$1" == python3 && "$2" == "$BUNDLE/scripts/check_java_service.py" ]]; then return; fi
  if [[ "$1" == readlink ]]; then
    if [[ "${QA_REJECT:-false}" == true ]]; then echo /opt/sparkrobot/lingxi-nav/releases/unknown; return; fi
    local value
    value="$(readlink -f "$QA_ROOT/opt/sparkrobot/lingxi-nav/current")"
    printf '%s\n' "${value#"$QA_ROOT"}"
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
upgrade_product
''')
    results = {"fresh_component_install": "passed"}
    for suffix in ("r11", "r12", "r13", "r14", "r15", "r16", "r17"):
        date = "20261017" if int(suffix[1:]) <= 15 else "20261019"
        version = "1.0.19-" + date + "-" + suffix
        archive = ROOT / f"v138_complete_{suffix}_deployment/dist/lingxi-v1.3.8-complete-{date}-{suffix}-deployment.tar.gz"
        with tempfile.TemporaryDirectory(prefix="lifecycle-", dir=work) as directory:
            case = Path(directory)
            baseline = extract(component(archive.read_bytes(), version), case / "baseline")
            root = case / "robot"
            root.mkdir()
            env = common | {"SPARKROBOT_INSTALL_ROOT": str(root)}
            run(["bash", baseline / "install.sh", "--secrets", secrets], env=env)
            current = root / "opt/sparkrobot/lingxi-nav/current"
            properties = root / "etc/sparkrobot/lingxi-nav/lingxi-nav.properties"
            runtime = root / "etc/sparkrobot/lingxi-nav/runtime.env"
            properties.write_text(properties.read_text() + "customer.keep=yes\n")
            config = root / "var/lib/sparkrobot/lingxi-nav/unified_nav_config.json"
            data = json.loads(config.read_text())
            data["navigation"]["default_max_forward_speed"] = .37
            data["navigation"]["arrival_policy"]["yaw_tolerance_rad"] = None
            config.write_text(json.dumps(data))
            customer = config.parent / "maps/customer.json"
            customer.write_text('{"tour_tasks":[{"id":"keep","start_commands":["custom"],"steps":[{"point_id":"p1","wait_sec":9}]}],"points":[{"id":"p1"}]}')
            saved = [p.read_bytes() for p in (properties, runtime, customer)]
            outer_env = env | {"BUNDLE": str(bundle), "NEW_NAV": str(new), "QA_ROOT": str(root)}
            run(["bash", script], env=outer_env)
            assert current.resolve().name == NEW
            assert [p.read_bytes() for p in (properties, runtime, customer)] == saved
            assert json.loads(config.read_text())["navigation"] == data["navigation"]
            assert (root / "native-events").read_text().strip() == "native"
            assert (current / "scripts/setup_soc2_access.sh").read_bytes() == (new / "scripts/setup_soc2_access.sh").read_bytes()
            internal = root / "usr/local/sbin/sparkrobot-lingxi-internal"
            run(["bash", internal, "verify"], env=env)
            duplicate = subprocess.run(["bash", str(new / "install.sh")], env=env, capture_output=True)
            assert duplicate.returncode != 0
            run(["bash", script], env=outer_env)
            refused = subprocess.run(["bash", str(script)], env=outer_env | {"QA_REJECT": "true"}, capture_output=True)
            assert refused.returncode != 0 and current.resolve().name == NEW
            run(["bash", internal, "rollback", version], env=env)
            shutil.copyfile(bundle / f"scripts/control-{suffix}.sh", root / "usr/local/sbin/sparkrobot-lingxi-complete")
            run(["bash", internal, "verify"], env=env)
            assert current.resolve().name == version
            assert [p.read_bytes() for p in (properties, runtime, customer)] == saved
            results[suffix + "_upgrade_reinstall_rollback_preservation"] = "passed"
            print("Lifecycle " + suffix + " passed", flush=True)
    return results


def verify(args):
    raw = args.archive.read_bytes()
    assert args.archive.with_name(args.archive.name + ".sha256").read_bytes() == (
        sha(raw) + "  " + args.archive.name + "\n").encode("ascii")
    baseline = (ROOT / "v138_complete_r17_deployment/dist" / (BASE_NAME + ".tar.gz")).read_bytes()
    assert sha(baseline) == BASE_SHA
    report = dict(status="passed", version=VERSION, tested_archive_sha256=sha(raw),
                  physical_robot="not_run", cloud_auth="not_run", acoustic_hardware="not_run",
                  native_tour_mapping_field_acceptance="pending")
    with tempfile.TemporaryDirectory(prefix="lingxi-r18-qa-") as directory:
        work = Path(directory)
        bundle = extract(raw, work / "bundle")
        old_bundle = extract(baseline, work / "old-bundle")
        new = extract(component(raw, NEW), work / "new")
        old = extract(component(baseline, OLD), work / "old")
        checksums(bundle, "SHA256SUMS.txt")
        checksums(new, "SHA256SUMS")
        repair = bundle / "scripts/native-tour-quiet"
        checksums(repair, "SHA256SUMS")
        meta = json.loads((bundle / "bundle-manifest.json").read_text())
        assert meta["navigation_release"] == NEW and meta["version"] == VERSION
        assert meta["source_archive_sha256"] == BASE_SHA and meta["excluded_failed_runtime"] == "1.0.9"
        for name, expected in meta["components"].items():
            assert sha((bundle / "components" / name).read_bytes()) == expected
        for name in ("aimdk-aarch64-1bde262f-artifacts.7z", "sparkrobot-lingxi-kb-1.0.2-20260902.zip"):
            assert (bundle / "components" / name).read_bytes() == (old_bundle / "components" / name).read_bytes()
        manifest = json.loads((new / "payload-manifest.json").read_text())
        paths = {item["path"] for item in manifest["files"]}
        assert paths == {p.relative_to(new / "payload").as_posix() for p in (new / "payload").rglob("*") if p.is_file()}
        changes = []
        for item in manifest["files"]:
            data = (new / "payload" / item["path"]).read_bytes()
            assert sha(data) == item["sha256"] and len(data) == item["size"]
            if data != (old / "payload" / item["path"]).read_bytes():
                changes.append(item["path"])
        assert changes == [NEAR], changes
        for name in ("install.sh", "control.sh"):
            assert (new / name).read_bytes() == (old / name).read_bytes()
        for path in (old / "scripts").rglob("*"):
            if path.is_file() and path.name != "setup_soc2_access.sh":
                assert path.read_bytes() == (new / "scripts" / path.relative_to(old / "scripts")).read_bytes()
        assert (new / "scripts/setup_soc2_access.sh").read_bytes() == (bundle / "scripts/setup-soc2-access.sh").read_bytes()
        merged = json.loads((bundle / "merged-increments.json").read_text())
        assert (new / "merged-increments.json").read_bytes() == (bundle / "merged-increments.json").read_bytes()
        assert len(merged["all_post_r15_increments"]) == 10 and merged["excluded_failed_runtime"] == "1.0.9"
        assert merged["java_runtime_identical_to_shipped_1_0_8"] is True
        repair_meta = json.loads((repair / "release.json").read_text())
        assert NEW in repair_meta["allowed_releases"]
        for name, expected in repair_meta["payload_sha256"].items():
            assert sha((new / "payload" / name).read_bytes()) == expected
            assert (new / "payload" / name).read_bytes() == (repair / "payload" / name).read_bytes()
        for path in (new / "payload/navigation/robot_local_navigation_v3").glob("*.py"):
            assert path.read_bytes() == (new / "source/navigation/robot_local_navigation_v3" / path.name).read_bytes()
        assert (new / "payload" / NEAR).read_bytes() == (bundle / "tests/navigation/robot_local_navigation_v3/near_goal_arrival.py").read_bytes()
        for name in DOCS:
            assert (bundle / "docs" / name).read_bytes() == (new / "docs" / name).read_bytes()
            assert (bundle / "docs" / name).read_bytes() == (HERE / "dist" / name).read_bytes()
        for suffix in ("r11", "r12", "r13", "r14", "r15", "r16"):
            assert (bundle / f"scripts/control-{suffix}.sh").read_bytes() == (old_bundle / f"scripts/control-{suffix}.sh").read_bytes()
        assert (bundle / "scripts/control-r17.sh").read_bytes() == (old_bundle / "control_all.sh").read_bytes()
        assert (bundle / "VERSION").read_text().strip() == "LINGXI_COMPLETE_VERSION=" + VERSION
        assert json.loads((new / "release-info.json").read_text())["version"] == NEW
        for name in ("node", "java/bin/java"):
            header = (new / "payload/runtime" / name).read_bytes()[:20]
            assert header[:4] == b"\x7fELF" and int.from_bytes(header[18:20], "little") == 183
        env = os.environ | {"PYTHONPATH": str(new / "payload/navigation") + os.pathsep + str(new / "source/navigation"),
                            "R14_SCRIPTS": str(new / "scripts"), "R15_SCRIPTS": str(new / "scripts"),
                            "R15_BUNDLE": str(bundle), "SDK_CONTROLLER": str(new / "payload/navigation/robot_local_navigation_v3/sdk_motion_controller.py")}
        report["java_launch_policy"] = run([sys.executable, bundle / "tests/test_java_service.py"],
                                            env=env | {"PYTHONPATH": str(bundle / "scripts")}).strip()
        for name in ("test_motion", "test_motion_confirmation", "test_functional_runtime"):
            report[name] = run([sys.executable, bundle / ("tests/" + name + ".py")], env=env).strip()
        for path in [*new.rglob("*.py"), *repair.rglob("*.py")]:
            compile(path.read_bytes(), str(path), "exec")
        if args.linux:
            bootstrap = bundle / "tests/test_soc2_bootstrap.py"
            old_log = negative([sys.executable, bootstrap],
                               env=env | {"SOC2_SETUP_SCRIPT": str(old / "scripts/setup_soc2_access.sh")})
            assert "failures=4" in old_log
            report["r17_ssh_negative_control"] = "4 of 5 failed as expected"
            report["soc2_bootstrap"] = run([sys.executable, bootstrap],
                env=env | {"SOC2_SETUP_SCRIPT": str(new / "scripts/setup_soc2_access.sh")}).strip()
            assert "Ran 5 tests" in report["soc2_bootstrap"]
            for name in ("test_storage_permissions", "test_voice_runtime", "test_aimdk_install"):
                report[name] = run([sys.executable, bundle / ("tests/" + name + ".py")], env=env).strip()
            log = run([sys.executable, "-m", "unittest", "discover", "-s", repair, "-p", "test_*.py"], cwd=work)
            report["native_and_full_adapter_tests"] = int(re.search(r"Ran (\d+) tests", log).group(1))
            assert "\nOK" in log
            report["outer_orchestration"] = previous.outer_orchestration(bundle, work)
            report["lifecycle"] = lifecycle(bundle, new, work)
            scripts = [*bundle.rglob("*.sh"), *new.rglob("*.sh")]
            for path in scripts:
                assert b"\r" not in path.read_bytes()
                run(["bash", "-n", path])
            count = 0
            for guide in (bundle / "docs").glob("*.md"):
                for text in re.findall(r"```bash\n(.*?)```", guide.read_text(encoding="utf-8"), re.S):
                    target = work / "guide.sh"
                    target.write_text(text)
                    run(["bash", "-n", target])
                    count += 1
            report.update(bash_scripts=len(scripts), guide_blocks=count)
        else:
            test = new / "source/navigation/test/test_optional_arrival_heading.py"
            old_env = env | {"PYTHONPATH": str(old / "payload/navigation") + os.pathsep + str(old / "source/navigation")}
            # Pytest prepends a packaged test's source root ahead of PYTHONPATH.
            # Keep the negative probe outside either version's source tree.
            probe = work / "test_heading_r17_probe.py"
            shutil.copyfile(test, probe)
            old_log = negative([sys.executable, "-m", "pytest", "-q", probe], env=old_env, cwd=work)
            assert "2 failed, 6 passed" in old_log
            report["r17_heading_negative_control"] = "2 of 8 failed as expected"
            report["python_regression"] = run([sys.executable, "-m", "pytest", "-q", new / "source/navigation/test"], env=env, cwd=work).strip()
            jar, sdk = new / "payload/java/robot-local-navigation-java-all.jar", new / "payload/java/linksoul-agentsdk-1.4.0.jar"
            cp = os.pathsep.join(map(str, (jar, sdk)))
            java = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
            classes, tests = work / "classes", work / "tests"
            classes.mkdir()
            tests.mkdir()
            run([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp", cp,
                 "-d", classes, *sorted((new / "source/java/src/main/java").rglob("*.java"))])
            compiled = {p.relative_to(classes).as_posix(): p.read_bytes() for p in classes.rglob("*.class")}
            with zipfile.ZipFile(jar) as archive:
                assert archive.testzip() is None
                assert compiled == {name: archive.read(name) for name in archive.namelist()
                                    if name.startswith("com/agibot/aiem/nav/") and name.endswith(".class")}
            report["java_source_runtime_classes"] = len(compiled)
            run([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp", cp,
                 "-d", tests, *sorted((new / "source/java/src/test/java").rglob("*.java"))])
            results = {}
            for name in previous.JAVA_TESTS:
                output = run([java / "java.exe", "-cp", str(tests) + os.pathsep + cp, "com.agibot.aiem.nav." + name])
                results[name] = [line for line in output.splitlines() if "passed" in line.lower()]
                assert results[name]
                print(name + " passed", flush=True)
            report["java_regression"] = results
            server = new / "source/server"
            deps = ROOT / "server/node_modules"
            junction = server / "node_modules"
            run(["cmd", "/c", "mklink", "/J", junction, deps])
            try:
                node = shutil.which("node")
                run([node, deps / "typescript/bin/tsc", "-p", "tsconfig.json"], cwd=server)
                run([node, server / "scripts/copy-public.cjs"], cwd=server)
                generated = {p.relative_to(server / "dist").as_posix(): p.read_bytes() for p in (server / "dist").rglob("*") if p.is_file()}
                packaged = {p.relative_to(new / "payload/console/dist").as_posix(): p.read_bytes() for p in (new / "payload/console/dist").rglob("*") if p.is_file()}
                assert generated == packaged
                report["task_http_persistence"] = run([sys.executable, bundle / "tests/test_task_http_persistence.py"],
                    env=env | {"TASK_CONSOLE_DIST": str(server / "dist")}, cwd=work).strip()
                result = work / "server-report.json"
                run([node, deps / "vitest/vitest.mjs", "run", "--reporter=json", "--outputFile=" + str(result)], cwd=server)
                value = json.loads(result.read_text())
                assert value["success"] and value["numFailedTests"] == 0
                report["server_regression"] = {k: value[k] for k in ("numTotalTests", "numPassedTests", "numFailedTests")}
            finally:
                os.rmdir(junction)
        report.update(runtime_changes=changes, runtime_files=len(paths), integrity="passed",
                      java_and_console_identical_to_r17=True, source_to_runtime="passed")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=HERE / "dist" / (NAME + ".tar.gz"))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--linux", action="store_true")
    verify(parser.parse_args())
