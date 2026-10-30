"""Platform bindings for one managed mapper; configuration never comes from HTTP."""
import argparse
import ctypes
import json
import math
import os
from pathlib import Path
import platform
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-slam"), str(ROOT / "tools")]
HYPOTHESES = ("header_is_scan_start", "header_is_scan_end")


def verify_loaded_libraries(expected, text, *, required=None):
    from verify_slam_capture import sha256
    required = set(expected) if required is None else set(required)
    if not required.issubset(expected):
        raise ValueError("required_libraries_must_belong_to_verified_inventory")
    names = {path.name for path in expected}
    found = {Path(fields[5]) for line in text.splitlines()
             if len(fields := line.split(None, 5)) == 6 and Path(fields[5]).name in names}
    if (not required.issubset(found) or not found.issubset(expected)
            or any(expected[path] is not None and sha256(path) != expected[path] for path in found)):
        raise ValueError("actual_loaded_frozen_mapping_libraries_required")


def configuration(file):
    file = Path(file)
    if not file.is_absolute() or file.is_symlink() or file.stat().st_size > 4096:
        raise ValueError("direct_bounded_local_mapping_configuration_required")
    value = json.loads(file.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError("explicit_mapping_configuration_required")
    arm64 = value.get("platform") == "arm64-humble"
    mode = value.get("inputMode")
    if arm64:
        fields = {"platform", "inputMode", "root", "coreRoot", "mappingRoot",
                  "pointAdapter", "pointManifestSha256", "hypothesis"}
        fields |= {"reference", "rate"} if mode == "recorded" else {"maxSeconds"}
        paths = ("root", "coreRoot", "mappingRoot", "pointAdapter")
        if mode == "recorded":
            paths += ("reference",)
        from glim_point_target_probe import digest
        valid = (mode in ("recorded", "live") and set(value) == fields
                 and digest(value.get("pointManifestSha256")))
        if mode == "live":
            seconds = value.get("maxSeconds")
            valid = valid and type(seconds) in (int, float) and math.isfinite(seconds) and 5 <= seconds <= 300
    else:
        fields = {"inputMode", "work", "adapter", "mappingAdapter", "bundle", "reference", "hypothesis", "rate"}
        paths = ("work", "adapter", "mappingAdapter", "bundle", "reference")
        if "pointAdapter" in value:
            paths += ("pointAdapter",)
        valid = mode == "recorded" and set(value) in (fields, fields | {"pointAdapter"})
    if (not valid or value.get("hypothesis") not in HYPOTHESES
            or mode == "recorded" and (
                type(value.get("rate")) not in (int, float) or value["rate"] not in (.5, 1.))):
        raise ValueError("explicit_bounded_mapping_configuration_required")
    for name in paths:
        if type(value[name]) is not str:
            raise ValueError("direct_absolute_frozen_mapping_input_required")
        entry = Path(value[name])
        if not entry.is_absolute() or entry.resolve(strict=True) != entry or not entry.is_dir():
            raise ValueError("direct_absolute_frozen_mapping_input_required")
    if arm64:
        if mode == "live":
            from live_points import environment
        else:
            from glim_supervised_target import child_environment as environment
        child_env = environment(Path(value["root"]), Path(value["coreRoot"]))
    else:
        from run_glim_supervised_replay import child_environment
        child_env = child_environment(Path(value["work"]))
    return value, child_env


class MappingPlatform:
    def __init__(self, value):
        self.value = value
        self.arm64 = value.get("platform") == "arm64-humble"
        self.mode, self.hypothesis = value["inputMode"], value["hypothesis"]
        self.points_enabled = "pointAdapter" in value
        if self.arm64:
            self.args = argparse.Namespace(
                root=Path(value["root"]), core_root=Path(value["coreRoot"]),
                mapping_root=Path(value["mappingRoot"]), point_bundle=Path(value["pointAdapter"]),
                point_manifest_sha256=value["pointManifestSha256"], hypothesis=self.hypothesis,
                **({"reference": Path(value["reference"]), "rate": value["rate"]}
                   if self.mode == "recorded" else {}))
            self.config_dir = self.args.root / "config"
            self.profile = self.args.root / "private-profile.json"
            self.odometry_library = self.args.root / "lib/liblingxi_glim_offline.so"
            self.mapping_library = self.args.mapping_root / "lib/liblingxi_glim_mapping.so"
            self.cpu_library = self.args.core_root / "lib/libodometry_estimation_cpu.so"
            self.point_library = self.args.point_bundle / "lib/liblingxi_glim_point_output.so"
        else:
            self.args = argparse.Namespace(**{
                name: Path(value[key]) for name, key in (
                    ("work", "work"), ("adapter", "adapter"), ("mapping_adapter", "mappingAdapter"),
                    ("bundle", "bundle"), ("reference", "reference"))},
                hypothesis=self.hypothesis, rate=value["rate"])
            if self.points_enabled:
                self.args.points = Path(value["pointAdapter"])
            self.config_dir = self.args.bundle / "config"
            self.profile = self.args.bundle / "private-profile.json"
            self.odometry_library = self.args.adapter / "build/liblingxi_glim_offline.so"
            self.mapping_library = self.args.mapping_adapter / "build/liblingxi_glim_mapping.so"
            self.cpu_library = self.args.work / "prefix/lib/libodometry_estimation_cpu.so"
            self.point_library = (self.args.points / "build/liblingxi_glim_point_output.so"
                                  if self.points_enabled else None)
        self.frozen = self.verify()

    def sensor_provenance(self):
        from glim_target_controlled_mapping import configuration_files
        from lingxi_slam.map_provenance import sensor_provenance
        from verify_slam_capture import sha256
        return sensor_provenance({
            "schemaVersion": 1, "profileSha256": sha256(self.profile),
            "timingHypothesis": self.hypothesis,
            "configurationSha256": configuration_files(self.config_dir),
        })

    def verify(self):
        from verify_slam_capture import sha256
        if self.arm64:
            if (sys.platform != "linux" or platform.machine() != "aarch64"
                    or os.geteuid() == 0 or sys.version_info[:2] != (3, 10)):
                raise ValueError("ordinary_user_arm64_humble_python310_required")
            from glim_target_controlled_mapping import configuration_files, verify_control, PURPOSE
            from glim_target_replay import verify_replay_bundle
            from slam_target_probe import verify_bundle
            from glim_point_target_probe import verify_bundle as verify_points, LIBRARY
            args = self.args
            if self.mode == "recorded":
                from glim_supervised_target import inputs
                frozen = inputs(args)
            else:
                from live_child import verified_inputs
                verified_inputs(args)
                frozen = {"ddsProfileSha256": sha256(Path(
                    "/agibot/software/entry/cfg/ros_dds_configuration.xml"))}
            core = verify_bundle(args.core_root)
            mapper = verify_replay_bundle(args.mapping_root, PURPOSE)
            verify_control(args.mapping_root)
            points = verify_points(args.point_bundle, args.point_manifest_sha256)
            evidence = json.loads((ROOT / "docs/slam-point-arm64-target-evidence.json").read_text())
            core_digest = sha256(args.core_root / "bundle-manifest.json")
            if (mapper["coreBundleManifestSha256"] != core_digest
                    or configuration_files(args.mapping_root / "config") != configuration_files(self.config_dir)
                    or mapper["odometryAdapterSha256"] != sha256(self.odometry_library)
                    or mapper["mappingAdapterSha256"] != sha256(self.mapping_library)
                    or evidence["bundleManifestSha256"] != args.point_manifest_sha256
                    or evidence["coreManifestSha256"] != core_digest
                    or evidence["actualLoadedLibrariesSha256"][LIBRARY] != sha256(self.point_library)
                    or any(core["files"].get("lib/" + name, {}).get("sha256") != digest
                           for name, digest in points["expectedCoreLibrariesSha256"].items())):
                raise ValueError("same_verified_arm64_mapping_point_core_and_configuration_required")
            return {**frozen, "coreManifestSha256": core_digest,
                    "mappingManifestSha256": sha256(args.mapping_root / "replay-manifest.json"),
                    "mappingLibrarySha256": sha256(self.mapping_library),
                    "odometryLibrarySha256": sha256(self.odometry_library),
                    "pointManifestSha256": args.point_manifest_sha256,
                    "pointLibrarySha256": sha256(self.point_library),
                    "coreLibrariesSha256": {
                        Path(name).name: entry["sha256"] for name, entry in core["files"].items()
                        if name.startswith("lib/")}}
        from run_glim_supervised_replay import inputs
        from run_glim_point_output_trial import integrity
        from run_glim_mapping_trial import verify_extension
        frozen = integrity(self.args) if self.points_enabled else inputs(self.args)
        verify_extension(self.args.mapping_adapter, self.args.work)
        return {**frozen, "mappingLibrarySha256": sha256(self.mapping_library)}

    def loaded(self, *, mapping=False, points=False, odometry=True):
        if not self.arm64 and odometry:
            from run_glim_stream_trial import actual_libraries
            actual_libraries(self.args.work, self.args.adapter, self.frozen["nativeLibrariesSha256"])
        expected, required = {}, set()
        if odometry:
            expected[self.odometry_library] = self.frozen.get("odometryLibrarySha256")
            required.add(self.odometry_library)
        if self.arm64:
            expected.update({self.args.core_root / "lib" / name: digest
                             for name, digest in self.frozen["coreLibrariesSha256"].items()})
            required.add(self.args.core_root / "lib/libglim.so")
            if odometry:
                required.add(self.cpu_library)
        if mapping:
            expected[self.mapping_library] = self.frozen["mappingLibrarySha256"]
            required.add(self.mapping_library)
        if points:
            expected[self.point_library] = self.frozen[
                "pointLibrarySha256" if self.arm64 else "pointExtensionLibrarySha256"]
            required.add(self.point_library)
        verify_loaded_libraries(expected, Path("/proc/self/maps").read_text(), required=required)

    def native(self, record):
        from run_glim_offline_trial import Native
        from run_glim_supervised_replay import OwnedNative
        return OwnedNative(lambda: Native(self.odometry_library, self.config_dir, self.cpu_library),
                           record, self.loaded)

    def mapper(self):
        from run_glim_mapping_trial import MappingLibrary
        library = MappingLibrary(self.mapping_library)
        self.loaded(mapping=True, points=self.points_enabled)
        return library

    def decoder(self):
        if self.arm64:
            from rclpy.serialization import deserialize_message
            from rclpy.type_support import check_for_type_support
            from sensor_msgs.msg import Imu, PointCloud2
            for kind in (Imu, PointCloud2):
                check_for_type_support(kind)
            return lambda kind, payload: deserialize_message(payload, Imu if kind == "imu" else PointCloud2)
        from rosbags.typesys import Stores, get_typestore
        types = get_typestore(Stores.ROS2_HUMBLE)
        for kind in ("Imu", "PointCloud2"):
            types.get_msgdef("sensor_msgs/msg/" + kind)
        return lambda kind, payload: types.deserialize_cdr(
            payload, "sensor_msgs/msg/" + ("Imu" if kind == "imu" else "PointCloud2"))


def reload_saved(config_file, saved):
    """Independent process uses the same verified platform, without any sensor input."""
    from run_glim_mapping_trial import MappingLibrary, inventory_map, compare_points
    from mapping_service import private_json
    value, environment = configuration(config_file)
    if any(os.environ.get(key) != entry for key, entry in environment.items()):
        raise ValueError("unchanged_reviewed_native_environment_required")
    binding = MappingPlatform(value)
    provenance = binding.sensor_provenance()
    before = inventory_map(saved / "map")
    stats = (ctypes.c_double * 4)()
    MappingLibrary(binding.mapping_library).call(
        "reload", os.fsencode(binding.config_dir), os.fsencode(saved / "map"),
        os.fsencode(saved / "private-reloaded-points.f64"), stats)
    binding.loaded(mapping=True, odometry=False)
    if (any(not math.isfinite(value) or value <= 0 or value != int(value) for value in stats)
            or any(int(stats[index]) != before["num_submaps"] for index in (0, 2, 3))):
        raise ValueError("complete_saved_graph_reload_required")
    comparison = compare_points(saved / "private-saved-points.f64",
                                saved / "private-reloaded-points.f64", int(stats[1]))
    if (inventory_map(saved / "map") != before or binding.verify() != binding.frozen
            or binding.sensor_provenance() != provenance):
        raise ValueError("saved_map_or_platform_changed_during_reload")
    private_json(saved / "reload-report.json", {
        "pid": os.getpid(), "loadedSubmaps": int(stats[0]),
        "loadedGraphFactors": int(stats[2]), "loadedGraphValues": int(stats[3]),
        "comparison": comparison, "mapInventory": before,
        "sensorProvenance": provenance,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--saved", type=Path, required=True)
    arguments = parser.parse_args()
    reload_saved(arguments.config, arguments.saved)
