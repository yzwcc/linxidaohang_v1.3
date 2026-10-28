"""Isolated synthetic lifecycle peer. Never imports ROS or a native SLAM library."""
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS

from lingxi_slam.process_protocol import HealthEmitter


def status(state="running", completed=0, failure=None):
    return {"state": state, "failure": failure, "counts": {"completedMessages": completed},
            "stream": {"counts": {"processedScans": completed // 10, "estimatedPoses": completed // 10}}}


def synthetic_worker(mode):
    import numpy as np
    from lingxi_slam.messages import SensorProfile
    from lingxi_slam.stream import Limits
    from lingxi_slam.worker import SensorWorker, WorkerLimits

    class Engine:
        def __init__(self):
            self.frames = 0
            if mode == "worker_start_hang":
                threading.Event().wait()

        def imu(self, *_):
            pass

        def frame(self, _, points, __):
            self.frames += 1
            if mode == "worker_frame_hang" and self.frames >= 3:
                threading.Event().wait()
            return np.eye(4), [0, 0, len(points), 0]

        def close(self):
            if mode == "worker_close_hang":
                threading.Event().wait()

    worker = SensorWorker(
        Engine, SensorProfile("synthetic_cloud", "synthetic_imu", "frame_relative_nanoseconds_hypothesis"),
        "header_is_scan_start",
        limits=WorkerLimits(maximum_processing_seconds=.25, tick_seconds=.01, shutdown_seconds=.1),
        stream_limits=Limits(initial_wait_seconds=.4, maximum_sensor_silence_seconds=.4),
    ).start()

    def feed(index):
        ns = index * 5_000_000
        def header(frame):
            return NS(frame_id=frame, stamp=NS(sec=1700000000 + ns // 10**9, nanosec=ns % 10**9))
        worker.offer_imu(NS(
            header=header("synthetic_imu"), linear_acceleration=NS(x=0., y=0., z=9.81),
            angular_velocity=NS(x=0., y=0., z=0.),
            linear_acceleration_covariance=[0.] * 9, angular_velocity_covariance=[0.] * 9,
        ))
        if index % 10 == 0:
            payload = bytearray(320)
            for point in range(10):
                struct.pack_into("<fff", payload, point * 32, point + 1, 2, 3)
                struct.pack_into("<d", payload, point * 32 + 24, point * 2_000_000)
            worker.offer_cloud(NS(
                header=header("synthetic_cloud"), width=10, height=1, point_step=32, row_step=320,
                is_bigendian=False, data=payload,
                fields=[NS(name=name, offset=offset, datatype=datatype, count=1)
                        for name, offset, datatype in (("x", 0, 7), ("y", 4, 7), ("z", 8, 7),
                                                       ("timestamp", 24, 8))],
            ))
    return worker, feed


def main():
    mode = sys.argv[1]
    if mode == "descendant_sleep":
        while True:
            time.sleep(1)
    if mode == "check_environment" and "LINGXI_TEST_AMBIENT" in os.environ:
        raise RuntimeError("ambient_environment_must_not_be_inherited")
    if mode in ("ignore_stop_and_term", "no_start"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    session = os.environ["LINGXI_SLAM_SESSION"]
    emitter = HealthEmitter(sys.stdout.buffer, "f" * 32 if mode == "wrong_session" else session,
                            clock=(lambda: time.monotonic() - 10) if mode == "stale" else time.monotonic)
    stop = threading.Event()
    def listen():
        command = sys.stdin.buffer.readline(16)
        if command in (b"stop\n", b""):
            stop.set()
    threading.Thread(target=listen, daemon=True).start()
    if mode == "no_start":
        while True:
            time.sleep(1)
    worker, feed = synthetic_worker(mode) if mode.startswith("worker_") else (None, None)
    index = 0
    try:
        while not stop.is_set() or mode == "ignore_stop_and_term":
            if worker is not None:
                feed(index)
                emitter.emit(worker.snapshot())
            else:
                emitter.emit(status(completed=0 if mode == "heartbeats_only" else index))
            if mode == "early_exit":
                os._exit(23)
            if mode == "descendant_pipe":
                subprocess.Popen([sys.executable, "-u", __file__, "descendant_sleep"],
                                 stdin=subprocess.DEVNULL)
                os._exit(0)
            if mode == "failed_status":
                emitter.emit(status("failed", index, "synthetic_engine_failed"))
                return
            index += 1
            time.sleep(.005 if worker is not None else .02)
    finally:
        if worker is not None:
            emitter.emit(worker.stop())
        else:
            # A valid but late active status cannot revive a retired owner.
            emitter.emit(status(completed=index))
            emitter.emit(status("stopped", index))


if __name__ == "__main__":
    main()
