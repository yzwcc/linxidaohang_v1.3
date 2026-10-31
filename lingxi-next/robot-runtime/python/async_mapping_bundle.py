"""Explicit source-bound full-mapper selection; verification never loads native code."""
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "async-mapping-manifest.json"
LIBRARY = "liblingxi_glim_full_mapping.so"
ENGINE = "glim-full-async-v1"
OBSERVED_ENGINE = "glim-full-observed-v1"
SOURCES = (
    "tools/glim-async-mapping/CMakeLists.txt", "tools/glim-async-mapping/async_mapping.cpp",
    "tools/glim-async-mapping/bounded_worker.hpp", "tools/glim-full-mapping/CMakeLists.txt",
    "tools/glim-full-mapping/full_mapping.cpp", "tools/glim-full-mapping/submapping-eof.patch",
    "robot-slam/lingxi_slam/async_mapping.py",
)
OBSERVATION_SOURCES = (
    "tools/glim-observations/CMakeLists.txt", "tools/glim-observations/observation_capture.hpp",
    "tools/glim-observations/observation_capture.cpp", "tools/glim-observations/observation-capture.patch",
    "robot-slam/lingxi_slam/observed_mapping.py", "robot-slam/lingxi_slam/observation_archive.py",
    "robot-slam/lingxi_slam/observation_binding.py", "robot-slam/lingxi_slam/mapping_session.py",
    "robot-runtime/python/mapping_platform.py", "robot-runtime/python/async_mapping_bundle.py",
)
CORE_LIBRARIES = ("libglim.so", "libglobal_mapping.so", "libgtsam.so", "libgtsam_unstable.so",
                  "libgtsam_points.so", "libodometry_estimation_cpu.so")
PLATFORMS = {"linux-aarch64": 183, "linux-x86_64": 62}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def digest(value):
    return type(value) is str and re.fullmatch("[a-f0-9]{64}", value) is not None


def direct(path, *, directory=False):
    path = Path(path)
    if (not path.is_absolute() or path.is_symlink() or path.resolve(strict=True) != path
            or not (path.is_dir() if directory else path.is_file())):
        raise ValueError("direct_full_mapping_bundle_path_required")
    return path


def bounded_text(path):
    direct(path)
    if not 0 < path.stat().st_size <= 2_000_000:
        raise ValueError("bounded_full_mapping_metadata_required")
    return path.read_text(encoding="utf-8")


def read_json(path):
    return json.loads(bounded_text(path))


def read_configuration(path):
    import json5
    return json5.loads(bounded_text(path), allow_duplicate_keys=False)


def source_hashes(*, observed=False):
    names = (*SOURCES, *OBSERVATION_SOURCES) if observed else SOURCES
    return {name: sha256(direct(ROOT / name)) for name in names}


def configuration_hashes(directory):
    direct(directory, directory=True)
    paths = sorted(directory.iterdir())
    if not 1 <= len(paths) <= 32:
        raise ValueError("bounded_full_mapping_configuration_required")
    result = {}
    for path in paths:
        direct(path)
        if path.stat().st_size > 2_000_000:
            raise ValueError("bounded_full_mapping_configuration_file_required")
        result[path.name] = sha256(path)
    return result


def inventory(root):
    direct(root, directory=True)
    files, size = {}, 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("direct_full_mapping_bundle_members_required")
        if path.is_dir():
            continue
        direct(path)
        name = path.relative_to(root).as_posix()
        if name == MANIFEST:
            continue
        length = path.stat().st_size
        size += length
        if (len(files) >= 40 or not 0 < length <= 32_000_000 or size > 64_000_000
                or name not in ("build-report.json", "lib/" + LIBRARY, "config/UPSTREAM_LICENSE.txt")
                and re.fullmatch(r"config/[A-Za-z0-9_.-]+\.json", name) is None):
            raise ValueError("bounded_explicit_full_mapping_bundle_members_required")
        files[name] = {"bytes": length, "sha256": sha256(path)}
    return files


def verify_configuration(root, baseline, *, observed=False):
    before, after = configuration_hashes(baseline / "config"), configuration_hashes(root / "config")
    added = {"config_sub_mapping_cpu.json", "config_global_mapping_cpu.json"}
    allowed = {"config.json", "config_odometry_cpu.json"} if observed else {"config.json"}
    if (set(after) != set(before) | added
            or any(after[name] != value for name, value in before.items() if name not in allowed)):
        raise ValueError("unchanged_full_mapper_sensor_and_odometry_configuration_required")
    first, last = read_configuration(baseline / "config/config.json"), read_configuration(root / "config/config.json")
    first["global"]["config_sub_mapping"] = "config_sub_mapping_cpu.json"
    first["global"]["config_global_mapping"] = "config_global_mapping_cpu.json"
    if first != last:
        raise ValueError("only_explicit_full_mapping_module_selection_may_change")
    if observed:
        odometry = read_configuration(baseline / "config/config_odometry_cpu.json")
        odometry["odometry_estimation"]["save_imu_rate_trajectory"] = True
        if odometry != read_configuration(root / "config/config_odometry_cpu.json"):
            raise ValueError("complete_acquisition_trajectory_configuration_required")
    local = read_configuration(root / "config/config_sub_mapping_cpu.json")["sub_mapping"]
    world = read_configuration(root / "config/config_global_mapping_cpu.json")["global_mapping"]
    if (any(options.get("enable_imu") is not True or options.get("enable_optimization") is not True
            or options.get("enable_gpu", False) is not False for options in (local, world))
            or local.get("registration_error_factor_type") != "VGICP"
            or world.get("registration_error_factor_type") != "VGICP"
            or world.get("randomsampling_rate") != 1.0):
        raise ValueError("explicit_cpu_local_global_imu_mapping_configuration_required")
    return before, after


def verify_bundle(root, expected, *, baseline, core_manifest, core_libraries, platform, observed=False):
    root, baseline = direct(root, directory=True), direct(baseline, directory=True)
    manifest = root / MANIFEST
    if not digest(expected) or sha256(manifest) != expected or platform not in PLATFORMS:
        raise ValueError("explicit_full_mapping_manifest_and_platform_required")
    value = read_json(manifest)
    before, after = verify_configuration(root, baseline, observed=observed)
    fields = {"schemaVersion", "engine", "platform", "sourceSha256", "baselineConfigurationSha256",
              "configurationSha256", "profileSha256", "coreManifestSha256", "coreLibrariesSha256",
              "files", "targetExecutionValidated", "geometryValidated", "navigationValidated"}
    if (type(value) is not dict or set(value) != fields or type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != 1 or value["engine"] != (OBSERVED_ENGINE if observed else ENGINE)
            or value["platform"] != platform
            or value["sourceSha256"] != source_hashes(observed=observed) or value["baselineConfigurationSha256"] != before
            or value["configurationSha256"] != after or value["profileSha256"] != sha256(baseline / "private-profile.json")
            or not digest(core_manifest) or value["coreManifestSha256"] != core_manifest
            or set(core_libraries) != set(CORE_LIBRARIES) or value["coreLibrariesSha256"] != core_libraries
            or any(not digest(entry) for entry in core_libraries.values())
            or any(value[name] is not False for name in
                   ("targetExecutionValidated", "geometryValidated", "navigationValidated"))
            or value["files"] != inventory(root)
            or not {"build-report.json", "lib/" + LIBRARY}.issubset(value["files"])):
        raise ValueError("unchanged_source_bound_full_mapping_bundle_required")
    with (root / "lib" / LIBRARY).open("rb") as stream:
        header = stream.read(20)
    if (len(header) != 20 or header[:6] != b"\x7fELF\x02\x01"
            or int.from_bytes(header[18:20], "little") != PLATFORMS[platform]):
        raise ValueError("matching_full_mapping_elf_platform_required")
    report = read_json(root / "build-report.json")
    if observed:
        if platform == "linux-aarch64":
            artifacts = report.get("artifacts", [])
            matches = [row for row in artifacts if row.get("file") == LIBRARY]
            if (report.get("result") != "arm64_observed_mapper_and_fixture_linked_not_target_executed"
                    or len(matches) != 1 or matches[0].get("sha256") != value["files"]["lib/" + LIBRARY]["sha256"]
                    or report.get("privateEofSymbolsVerified") is not True):
                raise ValueError("verified_observed_mapping_cross_build_required")
            acquisition = report["inputs"]["observations"]
        else:
            if (report.get("result") != "observed_mapping_adapter_built_not_geometry_acceptance"
                    or report.get("librarySha256") != value["files"]["lib/" + LIBRARY]["sha256"]):
                raise ValueError("verified_observed_mapping_host_build_required")
            acquisition = report["inputs"]
        expected_sources = {name.removeprefix("tools/glim-observations/"): value["sourceSha256"][name]
                            for name in OBSERVATION_SOURCES if name.startswith("tools/glim-observations/")}
        if acquisition.get("observations") != expected_sources:
            raise ValueError("identical_observation_capture_native_sources_required")
        native = acquisition["mapping"]
    elif platform == "linux-aarch64":
        if (report.get("result") != "arm64_bounded_async_mapper_linked_not_target_executed"
                or report.get("artifact", {}).get("sha256") != value["files"]["lib/" + LIBRARY]["sha256"]
                or report.get("privateEofSymbolsVerified") is not True):
            raise ValueError("verified_full_mapping_cross_build_required")
        native = report["inputs"]["mapper"]
    else:
        if (report.get("result") != "bounded_async_mapping_built_not_target_acceptance"
                or report.get("librarySha256") != value["files"]["lib/" + LIBRARY]["sha256"]):
            raise ValueError("verified_full_mapping_host_build_required")
        native = report["inputs"]
    for key, prefix in (("nativeSources", "tools/glim-full-mapping/"),
                        ("asyncSources", "tools/glim-async-mapping/")):
        expected_sources = {name.removeprefix(prefix): value["sourceSha256"][name]
                            for name in SOURCES if name.startswith(prefix)}
        if native.get(key) != expected_sources:
            raise ValueError("identical_full_mapping_native_sources_required")
    return value
