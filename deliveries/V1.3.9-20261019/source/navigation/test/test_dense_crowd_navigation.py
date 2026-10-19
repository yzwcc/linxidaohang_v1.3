from __future__ import annotations

import sys
from pathlib import Path


NAV_ROOT = Path(__file__).resolve().parents[1]
if str(NAV_ROOT) not in sys.path:
    sys.path.insert(0, str(NAV_ROOT))

from robot_local_navigation_v3.models import UnifiedNavConfig


def test_dense_crowd_profile_defaults_are_bounded_and_faster_than_slow_mode():
    config = UnifiedNavConfig.from_dict({"navigation": {"dense_crowd": {"enabled": True}}})
    profile = config.navigation.dense_crowd

    assert profile.enabled is True
    assert profile.target_pose_radius == 0.45
    assert profile.position_tolerance_m == 0.45
    assert round(profile.yaw_tolerance_rad * 180.0 / 3.141592653589793) == 30
    assert profile.max_forward_speed == 0.30
    assert profile.max_forward_speed <= 0.30
    assert profile.require_low_speed is True


def test_dense_crowd_profile_clamps_unsafe_values():
    config = UnifiedNavConfig.from_dict({
        "navigation": {
            "dense_crowd": {
                "enabled": True,
                "target_pose_radius": 99,
                "position_tolerance_m": 99,
                "max_forward_speed": 99,
                "timeout_sec": 1,
            },
            "task_failure_policy": {"max_retries_per_step": 99},
        }
    })

    profile = config.navigation.dense_crowd
    policy = config.navigation.task_failure_policy
    assert profile.target_pose_radius == 1.0
    assert profile.position_tolerance_m == 1.0
    assert profile.max_forward_speed == 0.5
    assert profile.timeout_sec == 30.0
    assert policy.max_retries_per_step == 3


def test_legacy_navigation_config_keeps_dense_profile_disabled():
    config = UnifiedNavConfig.from_dict({"navigation": {"default_target_pose_radius": 0.15}})
    assert config.navigation.dense_crowd.enabled is False
    assert config.navigation.default_target_pose_radius == 0.15
    assert config.navigation.task_failure_policy.continue_after_failure is True
