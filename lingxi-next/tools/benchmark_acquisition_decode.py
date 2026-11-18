"""Compare live decode with the previous matrix-composition path, without robot IO."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import struct
import sys
import time
from unittest.mock import patch

import numpy as np
import scipy
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'robot-slam'))
from lingxi_slam import observation_archive
from lingxi_slam.acquisition_output import decode_acquisition


def previous_rays(trajectory, times, origin, vectors):
    """The live geometry calculation used before direct ray application."""
    keys = Rotation.from_quat(trajectory[:, 4:])
    relative = (keys[:-1].inv() * keys[1:]).as_rotvec()
    index = np.maximum(np.searchsorted(trajectory[:, 0], times) - 1, 0)
    alpha = ((times - trajectory[index, 0])
             / (trajectory[index + 1, 0] - trajectory[index, 0]))
    delta = Rotation.from_rotvec(relative[index] * alpha[:, None]).as_matrix()
    rotations = np.einsum('nij,njk->nik', keys.as_matrix()[index], delta)
    return np.einsum('nij,j->ni', rotations, origin), np.einsum('nij,nj->ni', rotations, vectors)


def fixture(points):
    keys, stamp = 24, 1794546000.
    times = stamp + np.linspace(0, .1, keys)
    rotations = Rotation.from_rotvec(np.column_stack((np.zeros((keys, 2)), np.linspace(0, .01, keys))))
    trajectory = np.column_stack((times, np.linspace([0., 0., 0.], [.01, .02, .005], keys), rotations.as_quat()))
    returns = np.column_stack((np.linspace(0, .099, points), np.random.default_rng(2).normal(size=(points, 3))))
    identity = np.eye(4)
    extrinsic = identity.copy()
    extrinsic[:3, :3] = Rotation.from_euler('xyz', [.2, -.1, .3]).as_matrix()
    extrinsic[:3, 3] = [.2, -.1, .3]
    matrix = lambda value: value.astype('<f8').tobytes()
    data = (b'LXOBS002' + struct.pack('<4Q', 1, 1, points, 1) + struct.pack('<Q', 0)
            + matrix(identity) + struct.pack('<Q', 1)
            + struct.pack('<QddQQQ', 1, stamp, stamp + .1, points, keys, 2)
            + matrix(identity) + matrix(extrinsic) + matrix(identity)
            + matrix(trajectory) + matrix(returns))
    return data, (1, points, keys, len(data)), stamp, np.linalg.inv(extrinsic)


def benchmark(points, iterations):
    args = fixture(points)
    current = observation_archive.acquisition_rays
    functions = {'previous': previous_rays, 'direct': current}
    packets, timings = {}, {key: [] for key in functions}
    for key, function in functions.items():
        with patch.object(observation_archive, 'acquisition_rays', function):
            for _ in range(10):
                packets[key] = decode_acquisition(*args)
    for name in ('origins', 'endpoints', 'world_lidar_pose', 'world_lidar_last_return_pose'):
        np.testing.assert_allclose(getattr(packets['previous'], name), getattr(packets['direct'], name),
                                   rtol=0, atol=1e-12)
    assert packets['previous'].source_sha256 == packets['direct'].source_sha256
    for iteration in range(iterations):
        # Alternate execution order to reduce drift from CPU scheduling and temperature.
        order = tuple(functions) if iteration % 2 == 0 else tuple(reversed(functions))
        for key in order:
            with patch.object(observation_archive, 'acquisition_rays', functions[key]):
                started = time.perf_counter()
                decode_acquisition(*args)
                timings[key].append((time.perf_counter() - started) * 1000)
    result = {key: dict(p50=float(np.quantile(values, .5)), p95=float(np.quantile(values, .95)))
              for key, values in timings.items()}
    return dict(points=points, iterations=iterations, decodeMilliseconds=result,
                p50ReductionPercent=100 * (1 - result['direct']['p50'] / result['previous']['p50']),
                matchingGeometry=True, sourceSha256=packets['direct'].source_sha256)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iterations', type=int, default=150)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 10 <= args.iterations <= 2000:
        parser.error('iterations must be between 10 and 2000')
    sources = ('robot-slam/lingxi_slam/observation_archive.py', 'robot-slam/lingxi_slam/acquisition_output.py')
    result = dict(schemaVersion=1, purpose='synthetic_host_decode_benchmark_not_robot_latency',
                  machine=platform.machine(), python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
                  sourceSha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
                  cases=[benchmark(points, args.iterations) for points in (5000, 7100)], robotContacted=False)
    text = json.dumps(result, indent=2) + '\n'
    if args.output is not None:
        args.output.write_text(text)
    print(text, end='')


if __name__ == '__main__':
    main()
