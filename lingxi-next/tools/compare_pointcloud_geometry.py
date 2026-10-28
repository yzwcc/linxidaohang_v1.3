"""All-point bidirectional distances, not correspondences, calibration, or truth accuracy."""
import numpy as np
from scipy.spatial import cKDTree

from compare_glim_architectures import distribution
from run_kiss_offline_trial import valid_pose


def point_array(value):
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 3 or not len(array) or not np.isfinite(array).all():
        raise ValueError("nonempty_finite_complete_xyz_cloud_required")
    return array


def direction(source, destination):
    distances, _ = cKDTree(destination).query(source, k=1, eps=0, p=2, workers=1)
    result = distribution(distances)
    result["p99"] = float(np.percentile(distances, 99))
    result["pointsCompared"] = len(source)
    return result


def compare_clouds(first, second):
    first, second = point_array(first), point_array(second)
    return {
        "referenceToCandidateRawUnits": direction(first, second),
        "candidateToReferenceRawUnits": direction(second, first),
        "exactEuclideanNearestNeighbors": True, "allPointsWithoutSamplingOrOutlierRemoval": True,
        "multiplicityOrOneToOneCorrespondenceProven": False, "accuracyAgainstTruthMeasured": False,
    }


def compare_maps(first, second):
    left, right = point_array(first["points"]), point_array(second["points"])
    for value in (first["saved"][0], second["saved"][0]):
        valid_pose(value)
    alignment = first["saved"][0] @ np.linalg.inv(second["saved"][0])
    aligned = right @ alignment[:3, :3].T + alignment[:3, 3]
    return {
        "unadjusted": compare_clouds(left, right),
        "firstSavedLidarPoseRigidAlignmentOnly": compare_clouds(left, aligned),
        "bestFitCloudRegistrationUsed": False, "scaleFitted": False,
        "referencePointCount": len(left), "candidatePointCount": len(right),
        "geometryAcceptanceThresholdSelected": False,
    }
