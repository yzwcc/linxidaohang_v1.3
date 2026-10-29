"""Isolated synthetic ARM64 point-extension contracts; no ROS, sensors or motion."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys

from slam_target_probe import sha256, verify_bundle as verify_core


PURPOSE = "isolated_arm64_corrected_point_loading"
RESULT = "arm64_point_extension_loaded_and_synthetic_contracts_closed_not_recorded_or_live_acceptance"
LIBRARY = "liblingxi_glim_point_output.so"
SCENARIOS = ("copy", "overflow", "invalid_frame", "wrong_thread", "wrong_api_thread",
             "invalid_stamp", "invalid_points", "invalid_pose", "missing_points", "skipped_id", "pending_close")
MEMBERS = ("lib/" + LIBRARY, "bin/point_output_contract",
           "tools/glim_point_target_probe.py", "tools/slam_target_probe.py")
CORE_LIBRARIES = ("libodometry_estimation_cpu.so", "libcephes-gtsam.so.1", "libmetis-gtsam.so",
                  "libgtsam.so.4", "libgtsam_points.so.1", "libglim.so")
PINS = {
    "gtsam": "3ad4b4c3cb28394c9597f48fa02dad361c8450e3",
    "gtsam_points": "9d32e7dbecf6015560d84b4901d6b0a6f483ec46",
    "glim": "1a197c999e4865f5cab7c9cd25474128413ebbca",
}
BOUNDARIES = ("rosStarted", "sensorsSubscribed", "motionCommandsSent", "servicesChanged",
              "recordedDataProcessed", "livePointChannelValidated", "mappingReady", "localizationReady",
              "navigationReady", "runtimePackageBuilt", "deploymentApproved", "rawDataPublished")


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def arm64_elf(path):
    with path.open("rb") as stream:
        header = stream.read(20)
    if (len(header) != 20 or header[:6] != b"\x7fELF\x02\x01"
            or int.from_bytes(header[18:20], "little") != 183):
        raise ValueError("elf64_little_endian_aarch64_artifact_required")


def verify_bundle(root, expected_manifest):
    root = root.resolve(strict=True)
    record = root / "point-manifest.json"
    if (record.is_symlink() or not record.is_file() or not digest(expected_manifest)
            or sha256(record) != expected_manifest):
        raise ValueError("explicit_matching_point_manifest_hash_required")
    manifest = json.loads((root / "point-manifest.json").read_text(encoding="utf-8"))
    if (type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"] != 1
            or manifest["purpose"] != PURPOSE or manifest["inputClassification"] != "isolated_synthetic_contracts"
            or manifest["sourceCommits"] != PINS or manifest["scenarios"] != list(SCENARIOS)
            or manifest["targetExecutionValidated"] is not False
            or manifest["navigationValidated"] is not False or manifest["deploymentApproved"] is not False
            or not digest(manifest["crossBuildReportSha256"])
            or not digest(manifest["crossBuildEvidenceSha256"])
            or set(manifest["files"]) != set(MEMBERS)
            or set(manifest["expectedCoreLibrariesSha256"]) != set(CORE_LIBRARIES)
            or any(not digest(value) for value in manifest["expectedCoreLibrariesSha256"].values())):
        raise ValueError("explicit_unexecuted_synthetic_point_bundle_required")
    for name in MEMBERS:
        entry, path = manifest["files"][name], root / name
        if (set(entry) != {"sha256", "bytes", "executable"} or not digest(entry["sha256"])
                or type(entry["bytes"]) is not int or not 0 < entry["bytes"] <= 8 * 1024 * 1024
                or entry["executable"] is not (name.startswith("bin/"))
                or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root)
                or path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]):
            raise ValueError("identical_direct_point_bundle_member_required")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() or path.is_symlink()}
    if actual != set(MEMBERS) | {"point-manifest.json"}:
        raise ValueError("exact_point_bundle_members_required")
    for name in MEMBERS[:2]:
        arm64_elf(root / name)
    return manifest


def environment(bundle, core):
    return {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
        "LD_LIBRARY_PATH": str(bundle / "lib") + ":" + str(core / "lib"),
        "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
    }


def mapped_libraries(bundle, core, manifest, text):
    expected = {**manifest["expectedCoreLibrariesSha256"], LIBRARY: manifest["files"]["lib/" + LIBRARY]["sha256"]}
    found = {}
    for line in text.splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6:
            continue
        path = Path(fields[5])
        if path.name not in expected:
            continue
        directory = (bundle if path.name == LIBRARY else core) / "lib"
        if path.parent.resolve() != directory.resolve() or sha256(path) != expected[path.name]:
            raise ValueError("point_probe_library_loaded_from_wrong_path_or_bytes")
        found[path.name] = expected[path.name]
    if set(found) != set(expected):
        raise ValueError("all_point_probe_native_libraries_must_be_observed")
    return found


def empty_abi(library):
    pointer = ctypes.POINTER(ctypes.c_double)
    library.lingxi_points_error.argtypes, library.lingxi_points_error.restype = [], ctypes.c_char_p
    library.lingxi_points_open.argtypes = [ctypes.c_int]
    library.lingxi_points_take.argtypes = [ctypes.c_int, pointer, pointer, pointer]
    library.lingxi_points_counts.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
    library.lingxi_points_close.argtypes = []
    for name in ("open", "take", "counts", "close"):
        getattr(library, "lingxi_points_" + name).restype = ctypes.c_int
    opened = False
    try:
        if library.lingxi_points_open(2) != 0:
            raise ValueError("point_probe_empty_open_failed")
        opened = True
        values = (ctypes.c_uint64 * 5)()
        xyz, pose, metadata = (ctypes.c_double * 6)(), (ctypes.c_double * 16)(), (ctypes.c_double * 4)()
        if (library.lingxi_points_take(2, xyz, pose, metadata) != 0
                or library.lingxi_points_counts(values) != 0 or any(values)):
            raise ValueError("point_probe_empty_state_required")
    finally:
        if opened and library.lingxi_points_close() != 0:
            raise ValueError("point_probe_empty_close_failed")
    if library.lingxi_points_counts(values) != 0 or any(values):
        raise ValueError("point_probe_closed_empty_state_required")
    return {"open": 1, "close": 1, "callbacks": 0, "copiedPackets": 0}


def stop_owned(process):
    forced = process.poll() is None
    if forced:
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1.)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.poll() is None:
            raise RuntimeError("point_contract_owned_group_did_not_close")
    return {"exitCode": process.returncode, "forcedTermination": forced, "processAlive": process.poll() is None}


def contract(bundle, core, output, scenario, *, timeout_seconds=8.):
    if scenario not in SCENARIOS:
        raise ValueError("allowlisted_native_point_contract_required")
    command = ["/usr/bin/nice", "-n", "10", str(bundle / "bin/point_output_contract"), scenario]
    process, timed_out = None, False
    try:
        with (output / ("private-contract-" + scenario + ".log")).open("xb") as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       cwd=output, env=environment(bundle, core), start_new_session=True)
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        closed = stop_owned(process) if process is not None else None
    if closed is None:
        raise RuntimeError("native_point_contract_not_started")
    return {"scenario": scenario, **closed, "timedOut": timed_out}


def run(args):
    if getattr(args, "operator_confirmed", False) is not True:
        raise ValueError("fresh_stationary_supervised_estop_confirmation_required")
    if (sys.platform != "linux" or platform.machine() != "aarch64" or os.geteuid() == 0
            or sys.version_info[:2] != (3, 10)):
        raise ValueError("ordinary_user_arm64_python310_required")
    bundle, core, output = (getattr(args, name).resolve() for name in ("bundle", "core_root", "output"))
    expected_env = environment(bundle, core)
    allowed_env = {**expected_env, **({"LC_CTYPE": "C.UTF-8"} if "LC_CTYPE" in os.environ else {})}
    libc, version = platform.libc_ver()
    if (dict(os.environ) != allowed_env or libc != "glibc"
            or not re.fullmatch(r"[0-9]+\.[0-9]+", version)
            or tuple(map(int, version.split("."))) < (2, 34)):
        raise ValueError("clean_explicit_arm64_environment_and_glibc234_required")
    manifest = verify_bundle(bundle, args.manifest_sha256)
    core_manifest = verify_core(core)
    if any(core_manifest["files"].get("lib/" + name, {}).get("sha256") != value
           for name, value in manifest["expectedCoreLibrariesSha256"].items()):
        raise ValueError("same_previously_verified_arm64_core_required")
    cache = (Path.home() / ".cache/lingxi-next").resolve()
    if (output.exists() or output == cache or not output.is_relative_to(cache)
            or any(output == path or output in path.parents or path in output.parents for path in (bundle, core))):
        raise ValueError("new_private_point_probe_output_outside_frozen_inputs_required")
    output.mkdir(mode=0o700, parents=True)
    report = {
        "schemaVersion": 1, "result": "running", "inputClassification": "isolated_synthetic_contracts",
        "bundleManifestSha256": args.manifest_sha256, "coreManifestSha256": sha256(core / "bundle-manifest.json"),
        "target": {"machine": platform.machine(), "python": platform.python_version(), "libc": [libc, version]},
        "stages": [], "nativeContractExecutionsOnTarget": 0, "boundaries": dict.fromkeys(BOUNDARIES, False),
    }
    try:
        # The odometry module is loaded, but no native odometry engine is created.
        cpu = ctypes.CDLL(str(core / "lib/libodometry_estimation_cpu.so"), mode=os.RTLD_NOW | os.RTLD_LOCAL)
        extension = ctypes.CDLL(str(bundle / "lib" / LIBRARY), mode=os.RTLD_NOW | os.RTLD_LOCAL)
        report["actualLoadedLibrariesSha256"] = mapped_libraries(
            bundle, core, manifest, Path("/proc/self/maps").read_text())
        report["emptyAbi"] = empty_abi(extension)
        for scenario in SCENARIOS:
            stage = contract(bundle, core, output, scenario)
            report["stages"].append(stage)
            report["nativeContractExecutionsOnTarget"] = len(report["stages"])
            if stage != {"scenario": scenario, "exitCode": 0, "forcedTermination": False,
                         "processAlive": False, "timedOut": False}:
                raise RuntimeError("native_point_contract_failed_see_private_stage")
        if (verify_bundle(bundle, args.manifest_sha256) != manifest or verify_core(core) != core_manifest
                or sha256(core / "bundle-manifest.json") != report["coreManifestSha256"]):
            raise ValueError("point_probe_inputs_changed_during_execution")
        if cpu is None or extension is None:
            raise ValueError("point_probe_libraries_must_remain_loaded")
        report.update(result=RESULT, nativeContractExecutionsOnTarget=len(SCENARIOS))
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        with (output / "report.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "core-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--operator-confirmed", action="store_true")
    previous = os.umask(0o077)
    try:
        print(json.dumps({"result": run(parser.parse_args())["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
