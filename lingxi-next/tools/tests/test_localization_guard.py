"""Synthetic read-only handoff contracts; no robot IO or calibration claims."""
import copy
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
from lingxi_navigation.localization_guard import LocalizationGuard, LocalizationMonitor, parse_snapshot, _strict_json
from lingxi_navigation.physical_bridge import BridgeError
from test_physical_bridge import FakeClock, evidence

BINDING = dict(points_sha256='d' * 64, profile_sha256='e' * 64)


def snapshot(identity=None):
    identity = identity or evidence()
    matrix = [[1., 0., 0., 1.], [0., 1., 0., 2.], [0., 0., 1., 0.], [0., 0., 0., 1.]]
    return dict(runtime=dict(backend='glim', inputMode='live', serviceId='1' * 32, generation=1,
        runId='2' * 32, phase='running', selectedMap=dict(mapId=identity.mapId, receiptSha256=identity.receiptSha256),
        cleanupComplete=False, closed=False, localizationReady=False, navigationReady=False),
        state=dict(binding=dict(session='3' * 32, input_mode='live', map_id=identity.mapId,
            receipt_sha256=identity.receiptSha256, **BINDING), generation=1, state='tracking', reason='tracking',
            requestPending=False, workerReady=True, workerClosed=False, cleanupComplete=False,
            consecutiveMatches=3, lastFrame=7, referenceFrame=1, mapFromSensor=copy.deepcopy(matrix),
            coordinateUnits='native-raw', sourceFrame='lidar_chest_front', localizationReady=False, navigationReady=False,
            basePose=dict(status='available', reason='diagnostic_quality_only', mapFromBase=matrix, lastFrame=7,
                scanHeaderNs='1000000000', effectiveScanNs='1001000000', metresPerMapUnit=.5,
                coordinateUnits='metres-at-declared-scale', coordinateFrame='lingxi_saved_map', bodyFrame='base_link',
                physicalScaleValidated=False, navigationReady=False)))


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.identity, self.value = FakeClock(), evidence(), snapshot()
        self.monitor = LocalizationMonitor('/unused-test.sock', self.identity, BINDING,
            clock=self.clock, exchange=lambda: copy.deepcopy(self.value))
        self.addCleanup(self.monitor.close)
        self.guard = LocalizationGuard(self.monitor)
        self.monitor.refresh()

    def activate(self):
        return self.guard.activate_map(self.identity.mapId, None)

    def test_requires_explicit_activation_without_promoting_diagnostic_flags(self):
        self.assertIsNone(self.guard.current())
        self.assertEqual(self.identity, self.activate())
        sample = self.guard.current()
        self.assertEqual((1., 2., 0.), sample.base_pose)
        self.assertEqual(1001000000, sample.scan_ns)
        self.assertFalse(self.value['state']['navigationReady'])
        self.assertFalse(self.value['state']['basePose']['physicalScaleValidated'])
        with self.assertRaises(Exception):
            sample.frame = 8

    def test_wrong_map_and_planar_seed_are_side_effect_free(self):
        self.activate()
        original = self.guard.activation
        for map_id, pose in (('slam-' + 'f' * 32, None), (self.identity.mapId, dict(x=1., y=2., yaw=0.))):
            with self.assertRaises(BridgeError):
                self.guard.activate_map(map_id, pose)
            self.assertEqual(original, self.guard.activation)

    def test_changed_service_run_binding_or_seed_needs_new_activation(self):
        for where, key, value in (('runtime', 'serviceId', '4' * 32), ('runtime', 'runId', '4' * 32),
                ('runtime', 'generation', 2), ('state', 'generation', 2)):
            with self.subTest(key=key):
                self.value = snapshot()
                self.monitor.refresh()
                self.activate()
                self.value[where][key] = value
                self.monitor.refresh()
                self.assertIsNone(self.guard.current())
                self.activate()
                self.assertIsNotNone(self.guard.current())
        self.value['state']['binding']['session'] = '5' * 32
        self.monitor.refresh()
        self.assertIsNone(self.guard.current())

    def test_observed_loss_cannot_recover_between_owner_ticks(self):
        self.activate()
        self.value['state']['state'] = 'lost'
        self.monitor.refresh()
        self.value = snapshot()
        self.monitor.refresh()
        self.assertIsNone(self.guard.current())
        self.activate()
        self.assertIsNotNone(self.guard.current())

    def test_poll_freshness_and_source_progress_have_independent_deadlines(self):
        self.activate()
        self.clock.advance(.31)
        self.assertIsNone(self.guard.current())
        self.monitor.refresh()
        self.activate()
        self.clock.advance(.2)
        self.monitor.refresh()
        self.assertIsNone(self.monitor.current())
        self.value['state']['lastFrame'] += 1
        self.value['state']['basePose'].update(lastFrame=8, effectiveScanNs='1101000000')
        self.monitor.refresh()
        self.assertIsNotNone(self.monitor.current())
        self.assertIsNone(self.guard.current())

    def test_regressed_frames_stamps_and_changed_same_frame_are_rejected(self):
        for changes in ({'lastFrame': 6}, {'effectiveScanNs': '900000000'},
                        {'effectiveScanNs': '1101000000'}, {'lastFrame': 8}):
            self.value = snapshot()
            self.monitor.refresh()
            self.activate()
            self.value['state']['basePose'].update(changes)
            if 'lastFrame' in changes:
                self.value['state']['lastFrame'] = changes['lastFrame']
            self.monitor.refresh()
            self.assertIsNone(self.guard.current())
        self.value = snapshot()
        self.monitor.refresh()
        self.activate()
        self.value['state']['basePose']['mapFromBase'][0][3] += .01
        self.monitor.refresh()
        self.assertIsNone(self.guard.current())

    def test_exact_original_time_pose_required_not_latest_same_frame_odometry(self):
        self.activate()
        seen = []
        def source(stamp):
            seen.append(stamp)
            return dict(x=1., y=2., yaw=2*math.pi)
        sensors = NS(localization_sample_at=source)
        observation = NS(identity=self.identity, pose=dict(x=90., y=0., yaw=1.))
        self.assertTrue(self.guard.validate_observation(sensors, observation))
        self.assertEqual([1001000000], seen)
        sensors.localization_sample_at = lambda _: None
        self.assertFalse(self.guard.validate_observation(sensors, observation))
        sensors.localization_sample_at = lambda _: dict(x=1.001, y=2., yaw=0.)
        self.assertFalse(self.guard.validate_observation(sensors, observation))
        self.assertIsNone(self.guard.current())

    def test_invalid_runtime_binding_tracking_projection_and_matrices_withdraw(self):
        cases = [('runtime', 'inputMode', 'recorded'), ('runtime', 'closed', True),
            ('runtime', 'generation', True), ('runtime', 'phase', 'stopping'),
            ('state', 'generation', True), ('state', 'state', 'paused'), ('state', 'requestPending', True),
            ('state', 'workerClosed', True), ('state', 'consecutiveMatches', 2),
            ('state', 'mapFromSensor', None), ('state', 'sourceFrame', 'base_link'),
            ('state', 'navigationReady', True)]
        for where, field, value in cases:
            changed = snapshot()
            changed[where][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_snapshot(changed, self.identity, BINDING, self.clock())
        for key in BINDING:
            changed = snapshot()
            changed['state']['binding'][key] = 'f' * 64
            with self.assertRaises(ValueError):
                parse_snapshot(changed, self.identity, BINDING, self.clock())
        for field, value in (('lastFrame', 6), ('physicalScaleValidated', True), ('mapFromBase', None),
                ('coordinateFrame', 'odom'), ('effectiveScanNs', 1001000000), ('metresPerMapUnit', 0.)):
            changed = snapshot()
            changed['state']['basePose'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_snapshot(changed, self.identity, BINDING, self.clock())
        for cell in (float('nan'), 2., -1.):
            changed = snapshot()
            changed['state']['basePose']['mapFromBase'][0][0] = cell
            with self.assertRaises(ValueError):
                parse_snapshot(changed, self.identity, BINDING, self.clock())

    def test_slow_or_failed_response_cannot_renew_previous_snapshot(self):
        self.activate()
        def slow():
            self.clock.advance(.31)
            return snapshot()
        self.monitor.exchange = slow
        self.monitor.refresh()
        self.assertIsNone(self.guard.current())
        self.monitor.exchange = lambda: (_ for _ in ()).throw(TimeoutError())
        self.monitor.refresh()
        self.assertIsNone(self.monitor.current())

    def test_background_read_does_not_block_output_owner_and_closes(self):
        entered, release = threading.Event(), threading.Event()
        def exchange():
            entered.set()
            release.wait(.2)
            return snapshot()
        self.monitor.exchange = exchange
        self.monitor.start()
        self.assertTrue(entered.wait(1.))
        started = time.monotonic()
        for _ in range(100):
            self.monitor.current()
        self.assertLess(time.monotonic() - started, .05)
        release.set()
        self.monitor.close()
        self.assertFalse(self.monitor.thread.is_alive())
        self.assertIsNone(self.monitor.current())

    def test_strict_json_rejects_duplicate_or_nonfinite_values(self):
        for payload in ('{"ok":true,"ok":false}', '{"value":NaN}'):
            with self.assertRaises(ValueError):
                _strict_json(payload)


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() != 0,
                     'actual ordinary-user Linux Unix socket required')
class SocketTests(unittest.TestCase):
    def test_unresponsive_private_peer_has_bounded_reader_shutdown(self):
        with tempfile.TemporaryDirectory(prefix='nav-localization-') as directory:
            root = Path(directory)
            root.chmod(0o700)
            endpoint = root / 'service.sock'
            accepted, release, requests = threading.Event(), threading.Event(), []
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(str(endpoint))
                endpoint.chmod(0o600)
                server.listen(1)
                server.settimeout(1.)
                def peer():
                    with server.accept()[0] as client:
                        client.settimeout(1.)
                        requests.append(client.recv(4096))
                        accepted.set()
                        release.wait(1.)
                thread = threading.Thread(target=peer, daemon=True)
                monitor = LocalizationMonitor(endpoint, evidence(), BINDING)
                try:
                    thread.start()
                    monitor.start()
                    self.assertTrue(accepted.wait(1.))
                    started = time.monotonic()
                    monitor.close()
                    self.assertLess(time.monotonic() - started, .6)
                    self.assertFalse(monitor.thread.is_alive())
                    self.assertIsNone(monitor.current())
                    self.assertEqual([b'{"action":"navigation_snapshot"}\n'], requests)
                finally:
                    release.set()
                    monitor.close()
                    thread.join(2.)
                self.assertFalse(thread.is_alive())

    def test_nonprivate_parent_or_socket_is_rejected_before_connect(self):
        with tempfile.TemporaryDirectory(prefix='nav-localization-') as directory:
            root = Path(directory)
            endpoint = root / 'service.sock'
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(str(endpoint))
                server.listen(1)
                for parent_mode, socket_mode in ((0o755, 0o600), (0o700, 0o660)):
                    root.chmod(parent_mode)
                    endpoint.chmod(socket_mode)
                    monitor = LocalizationMonitor(endpoint, evidence(), BINDING)
                    with self.subTest(parent=parent_mode, socket=socket_mode), self.assertRaisesRegex(
                            ValueError, 'owned_private_localization_socket_required'):
                        monitor._exchange()
                    monitor.refresh()
                    self.assertIsNone(monitor.current())
                    monitor.close()


if __name__ == '__main__':
    unittest.main()
