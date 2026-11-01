"""The independent scene grader must reject false successes; no ROS required."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from run_nav2_offline import (SyntheticFusedMaps, assess_fused_costmaps, assess_trial, world)
from lingxi_navigation.runtime import MapIdentity


class TrialTests(unittest.TestCase):
    def state(self, phase='finished', **changes):
        return dict(phase=phase, outputQuiescent=True, requiresStop=False, **changes)

    def assess(self, case, poses, commands=((.1, 0., 0.),), state=None):
        return assess_trial(case, world(case), poses, commands, [[[0., 0.], [2., 0.]]],
                            state or self.state())

    def test_open_requires_actual_arrival_heading_and_motion(self):
        poses = [[0., 0., 0.], [2., 0., 0.]]
        self.assertTrue(self.assess('open', poses)['passed'])
        for modified in ([[0., 0., 0.]], [[0., 0., 0.], [2., 0., .16]]):
            self.assertFalse(self.assess('open', modified)['passed'])
        self.assertFalse(self.assess('open', poses, commands=[])['passed'])

    def test_detour_cannot_pass_by_cutting_through_obstacle_between_frames(self):
        value = self.assess('detour', [[0., 0., 0.], [2., 0., 0.]])
        self.assertFalse(value['checks']['sweptObstacleClearance'])
        self.assertAlmostEqual(0., value['minimumSweptObstacleDistanceMetres'])
        self.assertFalse(value['passed'])

    def test_detour_checks_clearance_and_nonzero_bypass(self):
        path = [[0., 0., 0.], [0., 1., 0.], [2., 1., 0.], [2., 0., 0.]]
        value = self.assess('detour', path)
        self.assertTrue(value['passed'])
        self.assertAlmostEqual(.7, value['minimumSweptObstacleDistanceMetres'])
        path[1][1], path[2][1] = .5, .5
        self.assertFalse(self.assess('detour', path)['passed'])

    def test_blocked_must_fail_action_and_remain_stationary_even_with_zero_packets(self):
        self.assertTrue(self.assess('blocked', [[0., 0., 0.], [0., 0., 0.]],
                        commands=[[0., 0., 0.]], state=self.state('failed'))['passed'])
        self.assertFalse(self.assess('blocked', [[0., 0., 0.], [.1, 0., 0.]],
                         commands=[], state=self.state('failed'))['passed'])

    def test_full_envelope_not_just_center_must_avoid_unknown_ground(self):
        value = self.assess('open', [[0., 0., 0.], [2., 2.6, 0.], [2., 0., 0.]])
        self.assertFalse(value['checks']['fullEnvelopeSupported'])

    def test_appearing_wall_requires_motion_then_stopped_failure_before_envelope_collision(self):
        value = self.assess('appearing-wall', [[0., 0., 0.], [.3, 0., 0.], [.5, 0., 0.]],
                            state=self.state('failed'))
        self.assertTrue(value['passed'])
        for path in ([[0., 0., 0.]], [[0., 0., 0.], [.8, 0., 0.]]):
            self.assertFalse(self.assess('appearing-wall', path, state=self.state('failed'))['passed'])

    def test_quiescence_fault_and_velocity_limits_are_independent_gates(self):
        path = [[0., 0., 0.], [2., 0., 0.]]
        for state in (dict(phase='finished', outputQuiescent=False, requiresStop=False),
                      dict(phase='finished', outputQuiescent=True, requiresStop=True)):
            self.assertFalse(self.assess('open', path, state=state)['passed'])
        for command in ((.21, 0., 0.), (-.01, 0., 0.), (.1, .11, 0.), (.1, 0., .31)):
            self.assertFalse(self.assess('open', path, commands=[command])['passed'])

    def test_support_is_checked_between_poses_not_just_at_endpoints(self):
        source = world('unsupported-shortcut')
        value = assess_trial('unsupported-shortcut', source, [[0., 0., 0.], [2., 0., 0.]],
                             [[.1, 0., 0.]], [[[0., 0.], [2., 0.]]], self.state())
        self.assertFalse(value['checks']['fullEnvelopeSupported'])
        self.assertFalse(value['passed'])
        path = [[0., 0., 0.], [0., 1., 0.], [2., 1., 0.], [2., 0., 0.]]
        value = assess_trial('unsupported-shortcut', source, path,
                             [[.1, 0., 0.]], [path], self.state())
        self.assertTrue(value['passed'])

    def test_removed_obstacle_still_blocks_the_pre_removal_trajectory(self):
        source = world('disappearing-obstacle')
        source['obstacle_removed_pose_index'] = 1
        value = assess_trial('disappearing-obstacle', source,
            [[0., 0., 0.], [1., 0., 0.], [2., 0., 0.]],
            [[.1, 0., 0.]], [[[0., 0.], [2., 0.]]], self.state())
        self.assertFalse(value['checks']['sweptObstacleClearance'])

    def test_fused_fixture_revises_obstacles_but_cannot_clear_unknown_support(self):
        identity = MapIdentity('slam-' + 'a' * 32, 'b' * 64, 'c' * 64, 'd' * 64)
        source = world('disappearing-obstacle')
        fixture = SyntheticFusedMaps(source, identity)
        old = fixture.update(True)
        new = fixture.update(False)
        self.assertIn(100, old)
        self.assertNotIn(100, new)
        self.assertEqual(2, fixture.latest.grid.revision)
        self.assertEqual(-1, new[0])
        fixture = SyntheticFusedMaps(world('unsupported-shortcut'), identity)
        for _ in range(3):
            self.assertEqual(-1, fixture.update(False)[60 * source['width'] + 60])

    def test_actual_costmap_gates_require_observation_clearing_and_blocked_unknowns(self):
        for blocked in (-1, 100):
            samples = [dict(unknown=blocked, obstacle=100, removed=False),
                       dict(unknown=blocked, obstacle=0, removed=True)]
            self.assertTrue(all(assess_fused_costmaps('disappearing-obstacle', samples).values()))
        for samples in ([], [dict(unknown=-1, obstacle=0, removed=True)],
                        [dict(unknown=-1, obstacle=100, removed=False)],
                        [dict(unknown=0, obstacle=100, removed=False),
                         dict(unknown=-1, obstacle=0, removed=True)]):
            self.assertFalse(all(assess_fused_costmaps('disappearing-obstacle', samples).values()))
        for value in (-1, 100, 0, 99):
            result = assess_fused_costmaps('unsupported-shortcut',
                [dict(unknown=-1, obstacle=value, removed=False)])
            self.assertEqual(value in (-1, 100), all(result.values()))


if __name__ == '__main__':
    unittest.main()
