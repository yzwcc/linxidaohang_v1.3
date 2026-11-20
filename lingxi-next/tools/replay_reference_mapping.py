"""Run the pinned X2 mapper and recorder on a bag in a loopback-only namespace."""

import argparse
from collections import Counter
from contextlib import ExitStack
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

import yaml


CORE_OUTPUT = ["/Odometry", "/cloud_registered_body", "/fast_lio/gravity"]


def read_counts(bag):
    metadata = yaml.safe_load((bag / "metadata.yaml").read_text(encoding="utf-8"))
    return Counter({entry["topic_metadata"]["name"]: entry["message_count"]
                    for entry in metadata["rosbag2_bagfile_information"]["topics_with_message_count"]})


def recorded_sensor_transform(bag):
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from rclpy.time import Time
    from scipy.spatial.transform import Rotation
    from tf2_msgs.msg import TFMessage
    from tf2_ros import Buffer

    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=str(bag), storage_id="sqlite3"), rosbag2_py.ConverterOptions("cdr", "cdr"))
    reader.set_filter(rosbag2_py.StorageFilter(topics=["/tf_static"]))
    buffer = Buffer()
    seen = {}
    while reader.has_next():
        _, data, _ = reader.read_next()
        for transform in deserialize_message(data, TFMessage).transforms:
            child = transform.child_frame_id
            body = transform.transform
            values = [transform.header.frame_id, body.translation.x, body.translation.y, body.translation.z,
                      body.rotation.x, body.rotation.y, body.rotation.z, body.rotation.w]
            if child in seen and seen[child] != values:
                raise ValueError(f"Conflicting recorded static TF: {child}")
            seen[child] = values
            buffer.set_transform_static(transform, "recorded_tf")
    transform = buffer.lookup_transform("lidar_imu_chest_front", "lidar_chest_front", Time()).transform
    q, t = transform.rotation, transform.translation
    return Rotation.from_quat([q.x, q.y, q.z, q.w]).as_matrix().ravel().tolist(), [t.x, t.y, t.z]


def run_replay(bag, output, mapper, config, recorder, ros2, rate, use_recorded_calibration=False):
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("Replay requires an isolated network namespace with only loopback")
    os.environ["ROS_LOCALHOST_ONLY"] = "1"
    sensor_tf = ["/tf", "/tf_static"]
    parameters = yaml.safe_load(config.read_text(encoding="utf-8"))
    config_values = parameters["/**"]["ros__parameters"]
    sensor_topics = [config_values["common"][key] for key in ("lid_topic", "imu_topic")]
    counts = read_counts(bag)
    if any(counts[topic] == 0 for topic in sensor_topics):
        raise ValueError("The source bag must contain both X2 raw sensor topics")
    playback_topics = sensor_topics + [topic for topic in sensor_tf if counts[topic]]
    if use_recorded_calibration:
        rotation, translation = recorded_sensor_transform(bag)
        config_values["mapping"].update(extrinsic_R=rotation, extrinsic_T=translation, extrinsic_est_en=False)

    output.mkdir(mode=0o700)
    saved_bag = output / "rosbag"
    pcd = output / "fastlio.pcd"
    config_values.update(map_file_path=str(pcd), required_output_subscribers=1, required_gravity_subscribers=1)
    config_values["pcd_save"].update(pcd_save_en=True, interval=-1)
    config_values["publish"].update(scan_publish_en=True, scan_bodyframe_pub_en=True)
    mapper_config = output / "fastlio.yaml"
    recorder_config = output / "recorder.yaml"
    mapper_config.write_text(yaml.safe_dump(parameters), encoding="utf-8")
    recorder_config.write_text(yaml.safe_dump({"recording_dir": str(saved_bag), "node_name": "reference_replay_recorder",
                                               "topics": sensor_topics + sensor_tf + CORE_OUTPUT}), encoding="utf-8")

    def stop(process):
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            return process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise

    with ExitStack() as resources:
        def launch(name, command):
            log = resources.enter_context((output / f"{name}.log").open("wb"))
            process = subprocess.Popen(command, cwd=output, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            resources.callback(stop, process)
            return process

        bag_recorder = launch("recorder", [recorder, "--config", recorder_config])
        fastlio = launch("mapper", [mapper, "--ros-args", "--params-file", mapper_config])
        player = launch("playback", [ros2, "bag", "play", bag, "--rate", str(rate), "--delay", "3",
                                     "--topics", *playback_topics])
        deadline = time.monotonic() + 360
        while player.poll() is None:
            if fastlio.poll() is not None or bag_recorder.poll() is not None:
                raise RuntimeError("Mapper or recorder exited during playback. See the output logs.")
            if time.monotonic() >= deadline:
                raise TimeoutError("Playback did not complete")
            time.sleep(0.25)
        if player.returncode != 0:
            raise RuntimeError("Bag playback failed. See playback.log.")
        time.sleep(1)
        if stop(fastlio) not in (0, -signal.SIGINT) or stop(bag_recorder) != 0:
            raise RuntimeError("Mapper or recorder did not save cleanly")

    recorded = read_counts(saved_bag)
    if not pcd.is_file() or not pcd.stat().st_size or any(recorded[topic] < 3 for topic in sensor_topics + CORE_OUTPUT):
        raise RuntimeError(f"Missing mapping output. Recorded topics: {dict(recorded)}")
    return recorded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("bag", "output", "mapper", "config", "recorder", "ros2"):
        parser.add_argument(f"--{key}", type=Path, required=True)
    parser.add_argument("--rate", type=float, default=1.)
    parser.add_argument("--use-recorded-calibration", action="store_true",
                        help="Use the bag's static LiDAR–IMU TF for this replay only")
    args = parser.parse_args()
    if not 0.25 <= args.rate <= 1.0:
        parser.error("--rate must be between 0.25 and 1.0")
    print(dict(run_replay(args.bag.resolve(strict=True), args.output.resolve(), args.mapper.resolve(strict=True),
                          args.config.resolve(strict=True), args.recorder.resolve(strict=True),
                          args.ros2.resolve(strict=True), args.rate, args.use_recorded_calibration)))


if __name__ == "__main__":
    main()
