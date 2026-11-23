"""Display-only overview. Neither preview coordinates nor history authorize motion."""
import csv
import math
import threading
import time

import numpy as np


class SurveyCloud:
    def __init__(self, identity, limit=30000):
        self.identity = identity
        self.limit = limit
        self.voxel = .085
        self.cells = {}
        self.trail = []
        self.last = 0
        self.pose = None
        self.view_from_source = None
        self.lock = threading.Lock()

    def add(self, frame, base_from_imu):
        stamp = frame['stamp_ns']
        if stamp <= self.last or base_from_imu is None:
            return
        source_from_imu = frame['odom_imu']
        source_from_base = source_from_imu @ np.linalg.inv(base_from_imu)
        # A stable view looking down on the initial standing body's XY plane.
        # The saved exporter independently computes its validated ground frame.
        view = self.view_from_source if self.view_from_source is not None else np.linalg.inv(source_from_base)
        transform = view @ source_from_imu
        points = frame['body_points']
        sample = points[::max(1, math.ceil(len(points) / 1800)), :3]
        xyz = sample @ transform[:3, :3].T + transform[:3, 3]
        xyz = xyz[np.isfinite(xyz).all(axis=1) & (np.abs(xyz) < 10000).all(axis=1)]
        body = view @ source_from_base
        if not len(xyz) or not np.isfinite(body).all():
            return
        pose = [float(body[0, 3]), float(body[1, 3]), math.atan2(body[1, 0], body[0, 0])]
        with self.lock:
            self.view_from_source = view
            self.cells.update(zip(map(tuple, np.floor(xyz / self.voxel).astype(int).tolist()), xyz.round(3).tolist()))
            while len(self.cells) > self.limit:
                self.voxel *= 2
                self.cells = {tuple(math.floor(v / self.voxel) for v in p): p for p in self.cells.values()}
            self.trail.append([*pose[:2], stamp / 1e9]); self.last = stamp; self.pose = pose
            if len(self.trail) > 8192:
                self.trail = self.trail[::2] + ([self.trail[-1]] if len(self.trail) % 2 == 0 else [])

    def snapshot(self):
        with self.lock:
            return dict(id=self.identity, frame='start-base', stampMs=self.last // 1000000,
                        xyz=[v for point in self.cells.values() for v in point],
                        trail=[v for row in self.trail for v in row],
                        pose=self.pose if -.1 <= time.time() - self.last / 1e9 <= 1.5 else None)


def saved_trail(path, expected_count):
    if type(expected_count) is not int or not 2 <= expected_count <= 2_000_000 or path.stat().st_size > 256_000_000:
        raise ValueError('invalid_trajectory_size')
    with path.open(encoding='utf-8', newline='') as handle:
        rows = csv.reader(handle)
        if next(rows, None) != 'time_s,x,y,z,qx,qy,qz,qw'.split(','):
            raise ValueError('invalid_trajectory_header')
        result, last, count = [], 0., 0
        step = math.ceil(expected_count / 8191)
        for i, row in enumerate(rows):
            if len(row) != 8 or i >= expected_count:
                raise ValueError('invalid_trajectory_row')
            t, x, y, *rest = map(float, row)
            if not all(map(math.isfinite, [t, x, y, *rest])) or t <= last:
                raise ValueError('invalid_trajectory_pose')
            if i % step == 0 or i == expected_count - 1:
                result.extend((x, y, t))
            last, count = t, i + 1
        if count != expected_count:
            raise ValueError('trajectory_count_mismatch')
        return result
