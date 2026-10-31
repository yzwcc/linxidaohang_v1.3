"""Seeded scan-to-map registration diagnostics, without navigation readiness."""
from dataclasses import dataclass
from importlib.metadata import version
import math
import threading
import time

import numpy as np
from scipy.spatial import cKDTree


SMALL_GICP_VERSION = "1.0.1"


@dataclass(frozen=True)
class RegistrationConfig:
    """Distances are uncalibrated map units; these are trial limits, not safety limits."""

    voxel_raw: float = .2
    correspondence_raw: float = 1.
    maximum_inlier_rms_raw: float = .25
    maximum_inlier_p95_raw: float = .45
    minimum_overlap: float = .6
    maximum_seed_translation_raw: float = 2.
    maximum_seed_rotation_radians: float = math.radians(30)
    minimum_geometry_ratio: float = .001
    minimum_native_information_ratio: float = 1e-6
    minimum_points: int = 64
    maximum_map_points: int = 2_000_000
    maximum_scan_points: int = 100_000
    maximum_iterations: int = 80

    def __post_init__(self):
        for name in (
                "voxel_raw", "correspondence_raw", "maximum_inlier_rms_raw",
                "maximum_inlier_p95_raw", "maximum_seed_translation_raw",
                "maximum_seed_rotation_radians", "minimum_overlap",
                "minimum_geometry_ratio", "minimum_native_information_ratio"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("finite_positive_registration_limits_required")
        if (self.minimum_overlap > 1 or self.minimum_geometry_ratio >= 1
                or self.minimum_native_information_ratio >= 1
                or self.maximum_seed_rotation_radians > math.pi
                or self.maximum_inlier_rms_raw > self.correspondence_raw
                or self.maximum_inlier_p95_raw > self.correspondence_raw):
            raise ValueError("consistent_registration_limits_required")
        for name, low, high in (
                ("minimum_points", 32, 100_000), ("maximum_map_points", 32, 2_000_000),
                ("maximum_scan_points", 32, 100_000), ("maximum_iterations", 1, 100)):
            if type(getattr(self, name)) is not int or not low <= getattr(self, name) <= high:
                raise ValueError("bounded_integer_registration_limits_required")
        if self.minimum_points > min(self.maximum_map_points, self.maximum_scan_points):
            raise ValueError("consistent_registration_point_limits_required")


def rigid_pose(value):
    if (not isinstance(value, np.ndarray) or value.shape != (4, 4)
            or value.dtype.kind not in "fi" or not np.isfinite(value).all()
            or not np.allclose(value[3], [0, 0, 0, 1], atol=1e-8, rtol=0)
            or np.linalg.norm(value[:3, :3].T @ value[:3, :3] - np.eye(3), ord="fro") > 1e-6
            or not np.isclose(np.linalg.det(value[:3, :3]), 1., atol=1e-6, rtol=0)):
        raise ValueError("finite_rigid_target_from_sensor_pose_required")
    return np.array(value, dtype=np.float64, order="C", copy=True)


def point_cloud(value, minimum, maximum, voxel):
    if (not isinstance(value, np.ndarray) or value.ndim != 2 or value.shape[1] != 3
            or value.dtype.kind not in "fi" or not minimum <= len(value) <= maximum
            or not np.isfinite(value).all()):
        raise ValueError("bounded_finite_xyz_registration_cloud_required")
    # Reject rather than silently losing coordinates outside the native voxel key range.
    if np.max(np.abs(value)) / voxel >= 2**20 - 1:
        raise ValueError("registration_cloud_outside_native_voxel_range")
    return np.array(value, dtype=np.float64, order="C", copy=True)


def sensor_scan_from_world(points_world, world_from_sensor):
    """Undo the corrected-point tap's world transform exactly once."""
    pose = rigid_pose(world_from_sensor)
    if (not isinstance(points_world, np.ndarray) or points_world.ndim != 2
            or points_world.shape[1] != 3 or not 1 <= len(points_world) <= 100_000
            or points_world.dtype.kind not in "fi" or not np.isfinite(points_world).all()):
        raise ValueError("bounded_finite_world_xyz_required")
    local = (points_world - pose[:3, 3]) @ pose[:3, :3]
    if not np.isfinite(local).all():
        raise ValueError("finite_sensor_xyz_required")
    return local


def _symmetric_ratio(matrix, *, equilibrate=False):
    if matrix.shape != (6, 6) or not np.isfinite(matrix).all():
        return 0.
    matrix = (matrix + matrix.T) / 2
    if equilibrate:
        diagonal = np.diag(matrix)
        if np.min(diagonal) <= 0:
            return 0.
        scale = np.sqrt(diagonal)
        matrix = matrix / scale[:, None] / scale[None, :]
    eigenvalues = np.linalg.eigvalsh(matrix)
    if eigenvalues[-1] <= 0:
        return 0.
    return max(0., float(eigenvalues[0] / eigenvalues[-1]))


@dataclass(frozen=True)
class RegistrationEstimate:
    accepted: bool
    reasons: tuple
    map_from_sensor: object
    metrics: dict


class RegistrationDiagnostics:
    """Bounded private quality evidence; no coordinates or change to acceptance."""

    METRICS = ("scanPoints", "mapPoints", "downsampledScanPoints", "downsampledMapPoints",
               "nativeConverged", "nativeInliers", "iterations", "overlap", "inlierRmsRaw",
               "inlierP95Raw", "seedTranslationCorrectionRaw", "seedRotationCorrectionRadians",
               "geometryInformationRatio", "nativeInformationRatio", "milliseconds")

    def __init__(self):
        self.attempts, self.accepted = 0, 0
        self.rejections, self.first_rejected, self.last = {}, [], None

    def record(self, estimate):
        self.attempts += 1
        self.accepted += int(estimate.accepted)
        event = {"attempt": self.attempts, "accepted": estimate.accepted,
                 "reasons": list(estimate.reasons),
                 "metrics": {key: estimate.metrics[key] for key in self.METRICS if key in estimate.metrics}}
        self.last = event
        if not estimate.accepted:
            for reason in estimate.reasons:
                self.rejections[reason] = self.rejections.get(reason, 0) + 1
            if len(self.first_rejected) < 8:
                self.first_rejected.append(event)

    def summary(self):
        def copy(event):
            return {**event, "reasons": list(event["reasons"]), "metrics": dict(event["metrics"])}
        return {"attempts": self.attempts, "accepted": self.accepted,
                "rejected": self.attempts - self.accepted, "rejectionReasons": dict(self.rejections),
                "firstRejected": [copy(event) for event in self.first_rejected],
                "last": copy(self.last) if self.last is not None else None}


class SeededMapRegistration:
    """Cache one map, use explicit nearby seeds, and reject weak estimates.

    Acceptance is a diagnostic quality gate only. This class neither tracks a
    robot nor publishes TF, changes map receipts, or enables navigation.
    Native calls have bounded work counts, not a hard wall-clock deadline;
    runtime integration must retain the existing process supervisor.
    """

    def __init__(self, map_points, config=None, *, diagnostics=None):
        self.config = RegistrationConfig() if config is None else config
        if not isinstance(self.config, RegistrationConfig):
            raise ValueError("registration_config_required")
        if diagnostics is not None and not isinstance(diagnostics, RegistrationDiagnostics):
            raise ValueError("registration_diagnostics_required")
        self.diagnostics = diagnostics
        if version("small-gicp") != SMALL_GICP_VERSION:
            raise RuntimeError("pinned_small_gicp_1_0_1_required")
        import small_gicp
        self.native, self.owner = small_gicp, threading.get_ident()
        c = self.config
        self.map_points = point_cloud(map_points, c.minimum_points, c.maximum_map_points, c.voxel_raw)
        self.target, self.target_tree = self._preprocess(self.map_points)
        self.target_points = np.asarray(self.target.points())[:, :3].copy()
        self.target_normals = np.asarray(self.target.normals())[:, :3].copy()
        self.map_tree, self.geometry_tree = cKDTree(self.map_points), cKDTree(self.target_points)
        for value in (self.map_points, self.target_points, self.target_normals):
            value.flags.writeable = False

    def _preprocess(self, points):
        cloud, tree = self.native.preprocess_points(
            points, downsampling_resolution=self.config.voxel_raw, num_neighbors=20, num_threads=1)
        if len(cloud) < self.config.minimum_points:
            raise ValueError("insufficient_points_after_registration_downsampling")
        return cloud, tree

    def register(self, scan_sensor, initial_map_from_sensor):
        if threading.get_ident() != self.owner:
            raise RuntimeError("single_registration_owner_required")
        c, started = self.config, time.perf_counter()
        seed = rigid_pose(initial_map_from_sensor)
        scan = point_cloud(scan_sensor, c.minimum_points, c.maximum_scan_points, c.voxel_raw)
        source, _ = self._preprocess(scan)
        result = self.native.align(
            self.target, source, self.target_tree, init_T_target_source=seed,
            registration_type="GICP", max_correspondence_distance=c.correspondence_raw,
            num_threads=1, max_iterations=c.maximum_iterations)
        metrics = {
            "scanPoints": len(scan), "mapPoints": len(self.map_points),
            "downsampledScanPoints": len(source), "downsampledMapPoints": len(self.target),
            "nativeConverged": bool(result.converged), "nativeInliers": int(result.num_inliers),
            "iterations": int(result.iterations),
        }
        try:
            pose = rigid_pose(result.T_target_source)
        except ValueError:
            metrics["milliseconds"] = (time.perf_counter() - started) * 1000
            return self._finish(RegistrationEstimate(False, ("invalid_native_transform",), None, metrics))
        transformed = scan @ pose[:3, :3].T + pose[:3, 3]
        distances, _ = self.map_tree.query(transformed, workers=1)
        inliers = distances <= c.correspondence_raw
        selected = distances[inliers]
        overlap = float(np.mean(inliers))
        rms = float(np.sqrt(np.mean(selected**2))) if len(selected) else None
        p95 = float(np.percentile(selected, 95)) if len(selected) else None
        correction = pose @ np.linalg.inv(seed)
        translation = float(np.linalg.norm(pose[:3, 3] - seed[:3, 3]))
        angle = float(np.arccos(np.clip((np.trace(correction[:3, :3]) - 1) / 2, -1, 1)))
        geometry_ratio = self._geometry_ratio(source, pose)
        native_ratio = _symmetric_ratio(np.asarray(result.H), equilibrate=True)
        metrics.update({
            "overlap": overlap, "inlierRmsRaw": rms, "inlierP95Raw": p95,
            "seedTranslationCorrectionRaw": translation, "seedRotationCorrectionRadians": angle,
            "geometryInformationRatio": geometry_ratio, "nativeInformationRatio": native_ratio,
            "milliseconds": (time.perf_counter() - started) * 1000,
        })
        reasons = []
        if (not result.converged or not 0 <= result.iterations <= c.maximum_iterations
                or not c.minimum_points <= result.num_inliers <= len(source)):
            reasons.append("native_not_converged_or_insufficient_inliers")
        if overlap < c.minimum_overlap:
            reasons.append("insufficient_scan_map_overlap")
        if rms is None or rms > c.maximum_inlier_rms_raw or p95 > c.maximum_inlier_p95_raw:
            reasons.append("excessive_scan_map_residual")
        if translation > c.maximum_seed_translation_raw or angle > c.maximum_seed_rotation_radians:
            reasons.append("excessive_seed_correction")
        if geometry_ratio < c.minimum_geometry_ratio or native_ratio < c.minimum_native_information_ratio:
            reasons.append("degenerate_registration_geometry")
        if reasons:
            return self._finish(RegistrationEstimate(False, tuple(reasons), None, metrics))
        pose.flags.writeable = False
        return self._finish(RegistrationEstimate(True, (), pose, metrics))

    def _finish(self, estimate):
        if self.diagnostics is not None:
            self.diagnostics.record(estimate)
        return estimate

    def _geometry_ratio(self, source, pose):
        points = np.asarray(source.points())[:, :3] @ pose[:3, :3].T + pose[:3, 3]
        distances, indices = self.geometry_tree.query(points, workers=1)
        mask = distances <= self.config.correspondence_raw
        points, normals = points[mask], self.target_normals[indices[mask]]
        if len(points) < self.config.minimum_points or not np.isfinite(normals).all():
            return 0.
        lengths = np.linalg.norm(normals, axis=1)
        if np.min(lengths) < .5:
            return 0.
        normals = normals / lengths[:, None]
        centered = points - np.mean(points, axis=0)
        radius = np.sqrt(np.mean(np.sum(centered**2, axis=1)))
        if radius <= self.config.voxel_raw:
            return 0.
        # Balanced point-to-plane Jacobian detects unobservable planes/corridors
        # even if GICP's covariance regularization yields an invertible Hessian.
        jacobian = np.column_stack((np.cross(centered / radius, normals), normals))
        return _symmetric_ratio(jacobian.T @ jacobian / len(points))
