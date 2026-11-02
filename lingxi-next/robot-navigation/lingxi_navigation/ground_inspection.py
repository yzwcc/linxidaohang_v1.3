"""Read-only commissioning hints; a fitted plane is never physical calibration."""
import hashlib
import math

import numpy as np
from scipy.optimize import least_squares

from .ground import GroundDeclaration, GroundLabels
from .navfn import finite
from .occupancy import canonical


def inspect_ground_labels(points, labels, declaration, *, anchor_z_metres):
    """Inspect actual source-bound native labels in the declared sensor frame.

    Above-sensor labels cannot seed a floor hint. Even a low-residual plane may
    be a platform or another object: no transform, height or runtime permit is
    returned or changed. Solving successfully is not evidence of a planar floor.
    """
    if (type(points) is not tuple or not 3 <= len(points) <= 8192
            or any(type(point) is not tuple or len(point) != 3
                or any(not finite(value) or abs(value) > 100 for value in point) for point in points)
            or type(labels) is not GroundLabels or len(labels.labels) != len(points)
            or type(declaration) is not GroundDeclaration or not finite(anchor_z_metres)
            or abs(anchor_z_metres) > 500
            or labels.input_sha256 != hashlib.sha256(canonical(points)).hexdigest()
            or labels.declaration_sha256 != declaration.sha256):
        raise ValueError('complete_source_bound_declared_ground_inspection_required')
    values = np.asarray(points, dtype=np.float64)
    codes = np.frombuffer(labels.labels, dtype=np.uint8)
    radius = np.linalg.norm(values[:, :2], axis=1)
    in_range = (radius >= declaration.minimum_range_metres) & (radius <= declaration.maximum_range_metres)
    native_ground = codes == 0
    band = np.abs(values[:, 2] + anchor_z_metres - declaration.ground_z_metres) <= declaration.height_tolerance_metres
    below = native_ground & in_range & (values[:, 2] < -.2)
    candidate = values[below]
    band_ground = int(np.count_nonzero(native_ground & in_range & band))
    above_ground = int(np.count_nonzero(native_ground & (values[:, 2] >= 0)))
    issues, hint = [], None
    if above_ground:
        issues.append('native_ground_labels_include_above_sensor_returns')
    if band_ground == 0:
        issues.append('declared_floor_band_has_no_native_ground_returns')
    height_error = abs(anchor_z_metres - declaration.ground_z_metres - declaration.sensor_height_metres)
    if height_error > declaration.height_tolerance_metres:
        issues.append('declared_sensor_origin_height_mismatch')
    if len(candidate) < 30:
        issues.append('insufficient_below_sensor_candidates')
    else:
        # Reject line-like geometry before fitting a plane; normalize for count.
        xy = candidate[:, :2]
        spread = np.linalg.svd(xy - xy.mean(axis=0), compute_uv=False) / math.sqrt(len(xy))
        if spread[-1] < .1:
            issues.append('insufficient_two_dimensional_coverage')
        else:
            design = np.column_stack((xy, np.ones(len(xy))))
            initial = np.linalg.lstsq(design, candidate[:, 2], rcond=None)[0]
            fit = least_squares(lambda beta: design @ beta - candidate[:, 2], initial,
                loss='soft_l1', f_scale=.02, max_nfev=80)
            normalizer = math.sqrt(1. + float(np.sum(fit.x[:2] ** 2)))
            residual = np.abs(design @ fit.x - candidate[:, 2]) / normalizer
            median, p95, largest = map(float, np.quantile(residual, (.5, .95, 1.)))
            offset = float(-fit.x[2])
            tilt = math.degrees(math.atan(float(np.linalg.norm(fit.x[:2]))))
            consistent = (bool(fit.success) and all(map(math.isfinite, (median, p95, largest, offset, tilt)))
                and p95 <= declaration.maximum_height_spread_metres and .2 <= offset <= 3. and tilt <= 15.)
            hint = dict(solverConverged=bool(fit.success), geometricallyConsistent=consistent,
                verticalOffsetDeclaredMetres=offset, tiltDegrees=tilt,
                normalResidualDeclaredMetres=dict(median=median, p95=p95, maximum=largest),
                xySpreadDeclaredMetres=list(map(float, spread)),
                status='candidate_only_not_identified_floor' if consistent else 'inconclusive_not_a_single_usable_plane')
            if not consistent:
                issues.append('below_sensor_plane_hint_inconclusive')
    return dict(returns=len(points), nativeLabels={str(code): labels.labels.count(code) for code in (0, 254, 255)},
        nativeGroundInDeclaredFloorBand=band_ground, nativeGroundAboveSensor=above_ground,
        belowSensorCandidates=len(candidate), declaredSensorHeightError=height_error,
        declaredFloorBandHasNativeGroundReturns=band_ground >= declaration.minimum_points_per_cell,
        labelBytesSha256=hashlib.sha256(labels.labels).hexdigest(), planeHint=hint, issues=issues,
        units='declared_metres_not_physical_calibration', requiresPhysicalMeasurement=True,
        physicalFloorIdentified=False, physicalScaleValidated=False, groundSupportValidated=False,
        configurationApplied=False, navigationReady=False)
