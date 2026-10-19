"""Verify the final full archive, its actual runtime and isolated installation."""
import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
from build_complete import BASE_NAME, BASE_SHA, DOCS, FIX_NAME, FIX_SHA, NAME, NEW, OLD, VERSION

spec = importlib.util.spec_from_file_location("r16_verifier", ROOT / "v138_complete_r16_deployment/verify_delivery.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
run, sha, extract, checksums, component = (previous.run, previous.sha, previous.extract,
                                         previous.checksums, previous.component)
JAVA_TESTS = (*previous.JAVA_TESTS, "NavigationTourRoutingTest", "TtsAcknowledgementTest", "SilentStartControlTest")


def outer_orchestration(bundle, work):
    script = work / "orchestration.sh"
    script.write_text(r'''set -Eeuo pipefail
source "$BUNDLE/install_all.sh" --help >/dev/null
need_run() { :; }
need_arm64() { :; }
assert_work_root() { :; }
verify_bundle() { :; }
verify_aimdk_runtime() { :; }
extract_components() { NAV_ROOT="$FIXTURES"; KB_ROOT="$FIXTURES"; }
wait_http() { :; }
curl() { :; }
install() { :; }
native_tour_setup() {
  echo "native $*" >>"$EVENTS"
  [[ "${QA_NATIVE_FAIL:-false}" != true ]]
}
sudo() {
  echo "sudo $*" >>"$EVENTS"
  case "$1" in
    readlink) echo "$EXPECTED_RELEASE"; return ;;
    test)
      [[ "$2" != -d ]]
      return
      ;;
    bash) return ;;
    systemctl) return ;;
    python3) return ;;
    sparkrobot-lingxi-internal) return ;;
    sparkrobot-lingxi-kb) return ;;
    install) return ;;
  esac
  return 91
}
case "$QA_STAGE" in
  install) install_product --agent-code AI_AGENT_test ;;
  upgrade) upgrade_product ;;
  preflight) preflight ;;
  doctor) doctor ;;
esac
''', encoding="utf-8")
    fixtures = work / "fixtures"
    fixtures.mkdir()
    configure = fixtures / "configure.sh"
    configure.write_text('#!/bin/bash\nprintf "test\\n" >"$1"\n')
    configure.chmod(0o755)
    results = {}
    for stage in ("install", "upgrade", "preflight", "doctor"):
        for failed in (False, True):
            events = work / ("events-" + stage + str(failed))
            env = os.environ | {"BUNDLE": str(bundle), "FIXTURES": str(fixtures),
                                "EVENTS": str(events), "QA_STAGE": stage,
                                "QA_NATIVE_FAIL": "true" if failed else "false"}
            proc = subprocess.run(["bash", str(script)], env=env, capture_output=True, timeout=30)
            output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
            assert (proc.returncode != 0) == failed, output
            calls = events.read_text()
            assert ("native --check" if stage in {"preflight", "doctor"} else "native ") in calls
            if failed:
                assert "完整产品已安装" not in output and "升级完成" not in output
                assert "安装预检通过" not in output and "完整套件验收通过" not in output
            results[stage + ("_native_failure_propagated" if failed else "_native_stage_called")] = "passed"
    return results


def linux_lifecycle(bundle, new, old, work):
    assert os.geteuid() == 0
    secrets = work / "test.env"
    secrets.write_text("LINGXI_APP_ID=test\nLINGXI_APP_KEY=test\nLINGXI_APP_SECRET=test\nDOUBAO_API_KEY=test\nSOC2_HOST=10.0.1.42\n")
    common = os.environ | {"SPARKROBOT_INSTALL_TEST_MODE": "true", "SPARKROBOT_ALLOW_NON_ARM64": "true"}
    fresh = work / "fresh"
    fresh.mkdir()
    run(["bash", new / "install.sh", "--secrets", secrets], env=common | {"SPARKROBOT_INSTALL_ROOT": str(fresh)})
    assert (fresh / "opt/sparkrobot/lingxi-nav/current").resolve().name == NEW
    results = {"fresh_component_install": "passed"}
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
  if [[ "$1" == python3 && "$2" == "$BUNDLE/scripts/check_java_service.py" ]]; then return 0; fi
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
''', encoding="utf-8")
    baselines = [(OLD, old, "r16"), (OLD, old, "r16-with-1.0.10")]
    for suffix in ("r11", "r12", "r13", "r14", "r15"):
        base_file = ROOT / f"v138_complete_{suffix}_deployment/dist/lingxi-v1.3.8-complete-20261017-{suffix}-deployment.tar.gz"
        assert base_file.is_file(), str(base_file)
        version = "1.0.19-20261017-" + suffix
        baselines.append((version, base_file, suffix))
    for version, baseline, scenario in baselines:
        with tempfile.TemporaryDirectory(prefix="lifecycle-", dir=work) as directory:
            case = Path(directory)
            if baseline.is_file():
                baseline = extract(component(baseline.read_bytes(), version), case / "baseline")
            root = case / "robot"
            root.mkdir()
            env = common | {"SPARKROBOT_INSTALL_ROOT": str(root)}
            run(["bash", baseline / "install.sh", "--secrets", secrets], env=env)
            current = root / "opt/sparkrobot/lingxi-nav/current"
            if scenario == "r16-with-1.0.10":
                manifest_path = current / "payload-manifest.json"
                manifest = json.loads(manifest_path.read_text())
                entries = {item["path"]: item for item in manifest["files"]}
                fix_meta = json.loads((bundle / "scripts/native-tour-quiet/release.json").read_text())
                for name, expected in fix_meta["payload_sha256"].items():
                    source = new / "payload" / name
                    shutil.copyfile(source, current / name)
                    entries[name] = dict(path=name, sha256=expected, size=source.stat().st_size)
                manifest["files"] = sorted(entries.values(), key=lambda item: item["path"])
                manifest_path.write_text(json.dumps(manifest) + "\n")
            props = root / "etc/sparkrobot/lingxi-nav/lingxi-nav.properties"
            runtime = root / "etc/sparkrobot/lingxi-nav/runtime.env"
            state = root / "var/lib/sparkrobot/lingxi-nav"
            props.write_text(props.read_text() + "custom.preserve=yes\n")
            config = state / "unified_nav_config.json"
            document = json.loads(config.read_text())
            document["navigation"]["default_max_forward_speed"] = 0.37
            config.write_text(json.dumps(document))
            customer = state / "maps/customer.json"
            customer.write_text('{"tour_tasks":[{"id":"keep","steps":[{"point_id":"p1"}]}],"points":[{"id":"p1"}]}')
            saved = [path.read_bytes() for path in (props, runtime, customer)]
            outer_env = env | {"BUNDLE": str(bundle), "NEW_NAV": str(new), "QA_ROOT": str(root)}
            run(["bash", script], env=outer_env)
            assert current.resolve().name == NEW
            assert [p.read_bytes() for p in (props, runtime, customer)] == saved
            assert json.loads(config.read_text())["navigation"] == document["navigation"]
            assert (root / "native-events").read_text().strip() == "native"
            internal = root / "usr/local/sbin/sparkrobot-lingxi-internal"
            run(["bash", internal, "verify"], env=env)
            duplicate = subprocess.run(["bash", str(new / "install.sh")], env=env, capture_output=True)
            assert duplicate.returncode != 0
            run(["bash", script], env=outer_env)
            denied = subprocess.run(["bash", str(script)], env=outer_env | {"QA_REJECT": "true"}, capture_output=True)
            assert denied.returncode != 0 and b"upgrade" in denied.stderr
            assert current.resolve().name == NEW
            run(["bash", internal, "rollback", version], env=env)
            suffix = version.rsplit("-", 1)[1]
            shutil.copyfile(bundle / f"scripts/control-{suffix}.sh", root / "usr/local/sbin/sparkrobot-lingxi-complete")
            assert current.resolve().name == version
            run(["bash", internal, "verify"], env=env)
            assert [p.read_bytes() for p in (props, runtime, customer)] == saved
            results[scenario + "_upgrade_reinstall_rollback_preservation"] = "passed"
    return results


def verify(args):
    raw = args.archive.read_bytes()
    assert args.archive.with_name(args.archive.name + ".sha256").read_bytes() == (
        sha(raw) + "  " + args.archive.name + "\n").encode("ascii")
    baseline = (ROOT / "v138_complete_r16_deployment/dist" / (BASE_NAME + ".tar.gz")).read_bytes()
    assert sha(baseline) == BASE_SHA
    report = dict(status="passed", version=VERSION, tested_archive_sha256=sha(raw),
                  bytes=len(raw), physical_robot="not_run", cloud_auth="not_run",
                  acoustic_hardware="not_run", native_tour_mapping_field_acceptance="pending")
    with tempfile.TemporaryDirectory(prefix="lingxi-r17-qa-") as directory:
        work = Path(directory)
        bundle = extract(raw, work / "bundle")
        checksums(bundle, "SHA256SUMS.txt")
        new = extract(component(raw, NEW), work / "new")
        old = extract(component(baseline, OLD), work / "old")
        checksums(new, "SHA256SUMS")
        repair = bundle / "scripts/native-tour-quiet"
        checksums(repair, "SHA256SUMS")
        meta = json.loads((bundle / "bundle-manifest.json").read_text(encoding="utf-8"))
        assert meta["version"] == VERSION and meta["navigation_release"] == NEW
        assert meta["source_archive_sha256"] == BASE_SHA and meta["cumulative_patch_archive_sha256"] == FIX_SHA
        for name, expected in meta["components"].items():
            assert sha((bundle / "components" / name).read_bytes()) == expected
        with previous.tarfile.open(fileobj=io.BytesIO(baseline)) as base_archive:
            for name in ("aimdk-aarch64-1bde262f-artifacts.7z", "sparkrobot-lingxi-kb-1.0.2-20260902.zip"):
                prior_data = base_archive.extractfile(BASE_NAME + "/components/" + name).read()
                assert (bundle / "components" / name).read_bytes() == prior_data
        fix_raw = (ROOT / "v138_native_tour_quiet_deployment/dist" / (FIX_NAME + ".tar.gz")).read_bytes()
        assert sha(fix_raw) == FIX_SHA
        fix = extract(fix_raw, work / "fix")
        checksums(fix, "SHA256SUMS")
        fix_meta = json.loads((fix / "release.json").read_text())
        manifest = json.loads((new / "payload-manifest.json").read_text())
        paths = {entry["path"] for entry in manifest["files"]}
        assert paths == {p.relative_to(new / "payload").as_posix()
                         for p in (new / "payload").rglob("*") if p.is_file()}
        changed = []
        for entry in manifest["files"]:
            data = (new / "payload" / entry["path"]).read_bytes()
            assert sha(data) == entry["sha256"] and len(data) == entry["size"]
            before = old / "payload" / entry["path"]
            if not before.is_file() or before.read_bytes() != data:
                changed.append(entry["path"])
        assert sorted(changed) == sorted(fix_meta["payload_sha256"])
        for name, expected in fix_meta["payload_sha256"].items():
            assert (new / "payload" / name).read_bytes() == (fix / "payload" / name).read_bytes()
            assert sha((new / "payload" / name).read_bytes()) == expected
        assert not (new / "source/java/src/test/java/com/agibot/aiem/nav/TourStartTakeoverTest.java").exists()
        for group, target in (("main", "main/java"), ("test", "test/java")):
            sources = {p.relative_to(fix / ("source/java/" + group)).as_posix(): p.read_bytes()
                       for p in (fix / ("source/java/" + group)).rglob("*.java")}
            packaged = {p.relative_to(new / ("source/java/src/" + target)).as_posix(): p.read_bytes()
                        for p in (new / ("source/java/src/" + target)).rglob("*.java")}
            assert sources == packaged
        for path in (fix / "source/navigation").rglob("*.py"):
            name = path.relative_to(fix / "source/navigation")
            assert path.read_bytes() == (new / "source/navigation" / name).read_bytes()
            assert path.read_bytes() == (bundle / "tests/navigation" / name).read_bytes()
        for path in (new / "payload/navigation/robot_local_navigation_v3").glob("*.py"):
            assert path.read_bytes() == (new / "source/navigation/robot_local_navigation_v3" / path.name).read_bytes()
        for name in ("install.py", "native_tour_quiet.py", "java_runtime.py", "arrival_runtime.py"):
            assert (repair / name).read_bytes() == (fix / name).read_bytes()
        merged = json.loads((bundle / "merged-increments.json").read_text(encoding="utf-8"))
        assert (bundle / "merged-increments.json").read_bytes() == (new / "merged-increments.json").read_bytes()
        assert merged["effective_increment"] == FIX_NAME and merged["excluded_failed_runtime"] == "1.0.9"
        assert merged["separate_increment_install_required"] is False
        assert len(merged["all_post_r15_increments"]) == 10
        assert merged["all_post_r15_increments"] == meta["merged_post_r15_increments"]
        assert all("1.0.9-" not in item["package"] for item in merged["all_post_r15_increments"])
        assert NEW in json.loads((repair / "release.json").read_text())["allowed_releases"]
        for name in DOCS:
            assert (bundle / "docs" / name).read_bytes() == (new / "docs" / name).read_bytes()
        assert (bundle / "VERSION").read_text().strip() == "LINGXI_COMPLETE_VERSION=" + VERSION
        assert json.loads((new / "release-info.json").read_text())["version"] == NEW
        for name in ("node", "java/bin/java"):
            header = (new / "payload/runtime" / name).read_bytes()[:20]
            assert header[:4] == b"\x7fELF" and int.from_bytes(header[18:20], "little") == 183
        for path in [*new.rglob("*.py"), *repair.rglob("*.py")]:
            compile(path.read_bytes(), str(path), "exec")
        env = os.environ | {"PYTHONPATH": str(new / "payload/navigation") + os.pathsep + str(new / "source/navigation"),
                            "R14_SCRIPTS": str(new / "scripts"), "R15_SCRIPTS": str(new / "scripts"),
                            "R15_BUNDLE": str(bundle),
                            "SDK_CONTROLLER": str(new / "payload/navigation/robot_local_navigation_v3/sdk_motion_controller.py")}
        report["java_launch_policy"] = run([sys.executable, bundle / "tests/test_java_service.py"],
                                            env=env | {"PYTHONPATH": str(bundle / "scripts")}).strip()
        for name in ("test_motion", "test_motion_confirmation", "test_functional_runtime"):
            report[name] = run([sys.executable, bundle / ("tests/" + name + ".py")], env=env).strip()
        if args.linux:
            scripts = [*bundle.rglob("*.sh"), *new.rglob("*.sh")]
            for path in scripts:
                assert b"\r" not in path.read_bytes()
                run(["bash", "-n", path])
            count = 0
            for guide in (bundle / "docs").glob("*.md"):
                for block in re.findall(r"```bash\n(.*?)```", guide.read_text(encoding="utf-8"), re.S):
                    target = work / "guide.sh"
                    target.write_text(block)
                    run(["bash", "-n", target])
                    count += 1
            report["bash_scripts"] = len(scripts)
            report["guide_blocks"] = count
            for name in ("test_storage_permissions", "test_voice_runtime", "test_aimdk_install"):
                report[name] = run([sys.executable, bundle / ("tests/" + name + ".py")], env=env).strip()
            native_log = run([sys.executable, "-m", "unittest", "discover", "-s", repair, "-p", "test_*.py"],
                             cwd=work, timeout=120)
            assert "\nOK" in native_log
            report["native_and_full_adapter_tests"] = int(re.search(r"Ran (\d+) tests", native_log).group(1))
            report["native_test_log_tail"] = native_log[-1800:]
            report["outer_orchestration"] = outer_orchestration(bundle, work)
            report["lifecycle"] = linux_lifecycle(bundle, new, old, work)
        else:
            report["python_regression"] = run([sys.executable, "-m", "pytest", "-q", new / "source/navigation/test"],
                                              env=env, cwd=work).strip()
            jar, sdk = (new / "payload/java/robot-local-navigation-java-all.jar",
                        new / "payload/java/linksoul-agentsdk-1.4.0.jar")
            cp = os.pathsep.join(map(str, (jar, sdk)))
            java = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
            classes, tests = work / "classes", work / "java-tests"
            classes.mkdir()
            tests.mkdir()
            run([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp", cp, "-d", classes,
                 *sorted((new / "source/java/src/main/java").rglob("*.java"))])
            compiled = {p.relative_to(classes).as_posix(): p.read_bytes() for p in classes.rglob("*.class")}
            with zipfile.ZipFile(jar) as archive:
                assert archive.testzip() is None
                assert compiled == {name: archive.read(name) for name in archive.namelist()
                                    if name.startswith("com/agibot/aiem/nav/") and name.endswith(".class")}
            run([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp", cp, "-d", tests,
                 *sorted((new / "source/java/src/test/java").rglob("*.java"))])
            results = {}
            for name in JAVA_TESTS:
                output = run([java / "java.exe", "-cp", str(tests) + os.pathsep + cp,
                              "com.agibot.aiem.nav." + name])
                results[name] = [line for line in output.splitlines() if "passed" in line.lower()]
                assert results[name], name
                print(name + " passed", flush=True)
            report["java_regression"] = results
            report["java_source_runtime_classes"] = len(compiled)
            server = new / "source/server"
            deps = ROOT / "server/node_modules"
            junction = server / "node_modules"
            run(["cmd", "/c", "mklink", "/J", junction, deps])
            try:
                node = shutil.which("node")
                run([node, deps / "typescript/bin/tsc", "-p", "tsconfig.json"], cwd=server)
                run([node, server / "scripts/copy-public.cjs"], cwd=server)
                generated = {p.relative_to(server / "dist").as_posix(): p.read_bytes()
                             for p in (server / "dist").rglob("*") if p.is_file()}
                packaged = {p.relative_to(new / "payload/console/dist").as_posix(): p.read_bytes()
                            for p in (new / "payload/console/dist").rglob("*") if p.is_file()}
                assert generated == packaged
                report["task_http_persistence"] = run([sys.executable, bundle / "tests/test_task_http_persistence.py"],
                    env=env | {"TASK_CONSOLE_DIST": str(server / "dist")}, cwd=work).strip()
                output = work / "server-report.json"
                run([node, deps / "vitest/vitest.mjs", "run", "--reporter=json", "--outputFile=" + str(output)], cwd=server)
                value = json.loads(output.read_text())
                assert value["success"] and value["numFailedTests"] == 0
                report["server_regression"] = {k: value[k] for k in ("numTotalTests", "numPassedTests", "numFailedTests")}
            finally:
                os.rmdir(junction)
        report.update(runtime_changes=sorted(changed), runtime_files=len(paths),
                      effective_increment_history=merged["all_post_r15_increments"],
                      cumulative_runtime_matches_1_0_10=True, excluded_1_0_9=True,
                      integrity="passed", source_to_runtime="passed", unchanged_aimdk_and_kb=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=HERE / "dist" / (NAME + ".tar.gz"))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--linux", action="store_true")
    verify(parser.parse_args())
