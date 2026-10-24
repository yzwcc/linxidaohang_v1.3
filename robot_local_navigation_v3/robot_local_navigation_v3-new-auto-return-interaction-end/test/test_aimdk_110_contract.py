from pathlib import Path


VERSION = "aimdk v1.1.0-gfaf4c94"
ARCHIVE_SHA256 = "dc80fe5d2907881ed77beb230680867efac6bd28b458f83d2ff4e9479abe3615"
SOURCE = (
    Path(__file__).resolve().parents[1]
    / "robot_local_navigation_v3"
    / "navigation_client.py"
)


def test_sdk_release_identity_is_pinned_for_this_adapter():
    assert VERSION == "aimdk v1.1.0-gfaf4c94"
    assert len(ARCHIVE_SHA256) == 64


def test_pnc_request_uses_best_effort_volatile_qos():
    source = SOURCE.read_text(encoding="utf-8")
    request_qos = source.split("cmd_qos = QoSProfile(", 1)[1].split("\n        )", 1)[0]
    assert "QoSReliabilityPolicy.BEST_EFFORT" in request_qos
    assert "QoSDurabilityPolicy.VOLATILE" in request_qos


def test_pnc_response_uses_best_effort_transient_local_qos():
    source = SOURCE.read_text(encoding="utf-8")
    response_qos = source.split("response_qos = QoSProfile(", 1)[1].split("\n            )", 1)[0]
    assert "QoSReliabilityPolicy.BEST_EFFORT" in response_qos
    assert "QoSDurabilityPolicy.TRANSIENT_LOCAL" in response_qos


def test_start_radius_is_clamped_to_sdk_minimum():
    source = SOURCE.read_text(encoding="utf-8")
    assert "MIN_TARGET_POSE_RADIUS" in source
    assert "max(\n            MIN_TARGET_POSE_RADIUS" in source


def test_stop_radius_remains_zero():
    source = SOURCE.read_text(encoding="utf-8")
    assert "msg.target_pose_radius = 0.0" in source
