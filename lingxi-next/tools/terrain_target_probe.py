"""Source-bound native ground/clearance trial; synthetic input only, without ROS."""
import argparse
import ctypes
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import sys
import time
import unittest


MANIFEST = "terrain-trial-manifest.json"
RESULT = "arm64_terrain_synthetic_contracts_passed_not_physical_acceptance"
LIBRARIES = ("liblingxi_ground.so", "liblingxi_octomap.so")
DEPENDENCIES = ("liboctomap.so.1.9.7", "liboctomath.so.1.9.7")
SOURCES = ("robot-navigation/lingxi_navigation/__init__.py",
    "robot-navigation/lingxi_navigation/navfn.py", "robot-navigation/lingxi_navigation/occupancy.py",
    "robot-navigation/lingxi_navigation/ground.py", "tools/tests/test_ground.py",
    "tools/terrain_target_probe.py")
NOTICES = ("THIRD_PARTY_NOTICES.md", "PATCHWORKPP_LICENSE", "OCTOMAP_DISTRIBUTION_COPYRIGHT")
CHECKS = dict.fromkeys(("bundleIntegrity", "isolatedLibraries", "all21Contracts", "inputsUnchanged"), True)
BOUNDARIES = dict.fromkeys(("sensorSubscriptionsStarted", "motionCommandsSent", "servicesSwitched",
    "systemPackagesInstalled", "realDataProcessed", "liveThroughputValidated", "physicalScaleValidated",
    "groundSupportValidated", "navigationReady", "deploymentApproved"), False)


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            value.update(chunk)
    return value.hexdigest()


def digest(value):
    return type(value) is str and re.fullmatch("[a-f0-9]{64}", value) is not None


def aliases():
    return {"native/lib/" + name.removesuffix(".7"): name for name in DEPENDENCIES}


def contract_ids(suite):
    return sorted(item.id() for group in suite for item in
        (group if isinstance(group, unittest.TestSuite) else [group]))


def load_contracts(root):
    sys.path.insert(0, str(root / "robot-navigation"))
    spec = importlib.util.spec_from_file_location("terrain_native_contracts", root / "tools/tests/test_ground.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return unittest.defaultTestLoader.loadTestsFromModule(module)


def verify_bundle(root):
    root = Path(root)
    if not root.is_absolute() or root.resolve(strict=True) != root or not root.is_dir():
        raise ValueError("direct_absolute_terrain_trial_required")
    manifest = root / MANIFEST
    if manifest.is_symlink() or not manifest.is_file() or not 0 < manifest.stat().st_size <= 131072:
        raise ValueError("bounded_direct_terrain_trial_manifest_required")
    value = json.loads(manifest.read_bytes())
    fields = {"schemaVersion", "purpose", "platform", "inputClassification", "buildReportSha256",
        "sourceSha256", "contracts", "files", "symlinks", "boundaries"}
    if (type(value) is not dict or set(value) != fields or type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != 1 or value["purpose"] != "synthetic_arm64_terrain_trial"
            or value["platform"] != "linux-aarch64" or value["inputClassification"] != "synthetic_fixture"
            or not digest(value["buildReportSha256"]) or value["boundaries"] != BOUNDARIES
            or any(item is not False for item in value["boundaries"].values())
            or value["symlinks"] != aliases()):
        raise ValueError("complete_synthetic_terrain_trial_required")
    sources, files, contracts = value["sourceSha256"], value["files"], value["contracts"]
    if (type(sources) is not dict or not set(SOURCES) <= set(sources)
            or any(type(name) is not str or not name or "\\" in name
                or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
                or PurePosixPath(name).as_posix() != name or not digest(identity)
                for name, identity in sources.items())
            or type(contracts) is not list or len(contracts) != 21
            or any(type(name) is not str or not name.startswith("terrain_native_contracts.") for name in contracts)
            or contracts != sorted(set(contracts))):
        raise ValueError("current_sources_and_exact_terrain_contracts_required")
    expected = set(SOURCES) | {"native/" + name for name in LIBRARIES + NOTICES}
    expected.update("native/lib/" + name for name in DEPENDENCIES)
    if type(files) is not dict or set(files) != expected:
        raise ValueError("exact_terrain_trial_inventory_required")
    folders = {str(parent) for name in expected for parent in PurePosixPath(name).parents if str(parent) != "."}
    actual = set()
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            if name not in aliases() or os.readlink(path) != aliases()[name]:
                raise ValueError("only_declared_relative_terrain_soname_aliases_allowed")
            if path.resolve(strict=True) != root / "native/lib" / aliases()[name]:
                raise ValueError("contained_terrain_dependency_alias_required")
        elif name in aliases():
            raise ValueError("only_declared_relative_terrain_soname_aliases_allowed")
        elif path.is_dir():
            if name not in folders:
                raise ValueError("undeclared_terrain_trial_directory")
            continue
        actual.add(name)
    if actual != expected | set(aliases()) | {MANIFEST}:
        raise ValueError("undeclared_terrain_trial_member")
    for name, entry in files.items():
        path = root / name
        if (type(entry) is not dict or set(entry) != {"bytes", "sha256"}
                or type(entry["bytes"]) is not int or not 0 <= entry["bytes"] <= 16777216
                or not digest(entry["sha256"]) or not path.is_file() or path.is_symlink()
                or path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]
                or name in SOURCES and entry["sha256"] != sources[name]):
            raise ValueError("unchanged_terrain_trial_members_required")
        if name.startswith("native/") and ".so" in name:
            with path.open("rb") as stream:
                header = stream.read(20)
            if header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\xb7\x00":
                raise ValueError("aarch64_terrain_trial_libraries_required")
    return value


def mapped_libraries(root, manifest, maps):
    expected = {Path(name).name: entry["sha256"] for name, entry in manifest["files"].items()
                if name.startswith("native/") and ".so" in name}
    found = {}
    for line in maps.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6 or not fields[5].startswith("/"):
            continue
        path = Path(fields[5])
        key = next((name for name in expected if name.partition(".so")[0] == path.name.partition(".so")[0]), None)
        if key is None:
            continue
        wanted = root / "native" / ("lib" if key in DEPENDENCIES else "") / key
        if path.resolve(strict=True) != wanted or sha256(wanted) != expected[key]:
            raise ValueError("terrain_library_outside_isolated_trial")
        found[key] = expected[key]
    if found != expected:
        raise ValueError("all_terrain_libraries_must_be_observed")
    return found


def run_contracts(root, contracts):
    suite = load_contracts(root)
    if len(contracts) != 21 or contract_ids(suite) != contracts:
        raise ValueError("exact_current_terrain_contract_inventory_required")

    class Result(unittest.TextTestResult):
        def __init__(self, *args):
            super().__init__(*args)
            self.passed = []

        def addSuccess(self, test):
            super().addSuccess(test)
            self.passed.append(test.id())

    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, resultclass=Result).run(suite)
    if not result.wasSuccessful() or result.skipped or sorted(result.passed) != contracts:
        raise RuntimeError("terrain_native_contract_failed:\n" + output.getvalue())
    return sorted(result.passed)


def probe(root):
    if (platform.system() != "Linux" or platform.machine() != "aarch64" or os.geteuid() == 0
            or os.environ.get("LD_LIBRARY_PATH") != str(root / "native/lib")
            or any(name in os.environ for name in ("LD_PRELOAD", "PYTHONPATH", "ROS_DISTRO", "AMENT_PREFIX_PATH"))):
        raise ValueError("ordinary_user_clean_arm64_terrain_process_required")
    manifest = verify_bundle(root)
    identity, started = sha256(root / MANIFEST), time.monotonic()
    libraries = [ctypes.CDLL(str(root / "native" / name), mode=os.RTLD_NOW | os.RTLD_LOCAL) for name in LIBRARIES]
    observed = mapped_libraries(root, manifest, Path("/proc/self/maps").read_text())
    os.environ.update(LINGXI_TEST_GROUND_LIBRARY=str(root / "native" / LIBRARIES[0]),
        LINGXI_TEST_GROUND_SHA256=manifest["files"]["native/" + LIBRARIES[0]]["sha256"],
        LINGXI_TEST_OCTOMAP_LIBRARY=str(root / "native" / LIBRARIES[1]),
        LINGXI_TEST_OCTOMAP_SHA256=manifest["files"]["native/" + LIBRARIES[1]]["sha256"],
        LINGXI_TEST_OCTOMAP_DEPENDENCIES=json.dumps({name: manifest["files"]["native/lib/" + name]["sha256"] for name in DEPENDENCIES}))
    passed = run_contracts(root, manifest["contracts"])
    if (verify_bundle(root) != manifest or sha256(root / MANIFEST) != identity or len(libraries) != 2
            or mapped_libraries(root, manifest, Path("/proc/self/maps").read_text()) != observed):
        raise ValueError("terrain_trial_changed_during_execution")
    return dict(result=RESULT, inputClassification="synthetic_fixture",
        target=dict(machine=platform.machine(), python=platform.python_version(), libc=list(platform.libc_ver())),
        trialManifestSha256=identity, buildReportSha256=manifest["buildReportSha256"],
        mappedLibrariesSha256=observed, executedContracts=passed,
        probeSeconds=time.monotonic() - started, checks=dict(CHECKS), boundaries=dict(BOUNDARIES))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    print(json.dumps(probe(parser.parse_args().root), allow_nan=False))
