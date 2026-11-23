"""Run in a disposable Humble namespace with only lo and a private /dev/shm."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time


def writer():
    from array import array
    import rclpy
    from sensor_msgs.msg import Imu, PointCloud2

    rclpy.init()
    node = rclpy.create_node("lingxi_udp_fixture_writer")
    cloud = node.create_publisher(PointCloud2, "/lingxi_udp_fixture/cloud", 20)
    imu = node.create_publisher(Imu, "/lingxi_udp_fixture/imu", 2000)
    frame = PointCloud2()
    frame.header.frame_id = "fixture"
    frame.height, frame.width = 1, 40000
    frame.point_step, frame.row_step = 32, 1280000
    frame.data = array("B", [7]) * frame.row_step
    sample = Imu()
    sample.header.frame_id = "fixture"
    try:
        deadline = time.monotonic() + 8
        while not (cloud.get_subscription_count() and imu.get_subscription_count()):
            if time.monotonic() >= deadline:
                raise TimeoutError("synthetic listeners did not match")
            rclpy.spin_once(node, timeout_sec=.1)
        start = time.monotonic()
        for index in range(600):
            time.sleep(max(0., start + index * .005 - time.monotonic()))
            sample.header.stamp = node.get_clock().now().to_msg()
            imu.publish(sample)
            if index % 20 == 0:
                frame.header.stamp = sample.header.stamp
                cloud.publish(frame)
        time.sleep(.2)
    finally:
        node.destroy_node()
        rclpy.shutdown()


def trial():
    sys.path.insert(0, str(Path(__file__).absolute().parents[2] / "deploy"))
    from reference_dds_profile import check, derive
    from test_reference_dds_profile import TEMPLATE

    with TemporaryDirectory(prefix="lingxi-udp-") as tmp:
        path = Path(tmp) / "udp.xml"
        data = derive(TEMPLATE.replace(b"192.0.2.4", b"127.0.0.1"))
        check(data)
        path.write_bytes(data)
        os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"] = str(path)
        os.environ["FASTDDS_BUILTIN_TRANSPORTS"] = "UDPv4"
        for name in ("FASTDDS_DEFAULT_PROFILES_FILE", "RMW_FASTRTPS_USE_QOS_FROM_XML"):
            os.environ.pop(name, None)
        import rclpy
        from sensor_msgs.msg import Imu, PointCloud2

        rclpy.init()
        node = rclpy.create_node("lingxi_udp_fixture_reader")
        counts = {"cloud": 0, "imu": 0, "invalidCloud": 0}

        def cloud(msg):
            counts["cloud"] += 1
            counts["invalidCloud"] += not (len(msg.data) == 1280000 and msg.data[0] == msg.data[-1] == 7)

        def imu(_):
            counts["imu"] += 1

        peer = None
        try:
            node.create_subscription(PointCloud2, "/lingxi_udp_fixture/cloud", cloud, 20)
            node.create_subscription(Imu, "/lingxi_udp_fixture/imu", imu, 2000)
            peer = subprocess.Popen([sys.executable, "-B", __file__, "--writer"])
            deadline = time.monotonic() + 15
            shm_seen = False
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=.05)
                shm_seen |= any(Path("/dev/shm").iterdir())
                if peer.poll() is not None and (peer.returncode != 0 or counts["cloud"] == 30 and counts["imu"] == 600):
                    break
            print(json.dumps({**counts, "sharedMemoryEntriesObserved": shm_seen, "peerExit": peer.poll(),
                              "classification": "isolated_synthetic_ros_no_robot"}), flush=True)
            if peer.poll() != 0 or counts != {"cloud": 30, "imu": 600, "invalidCloud": 0} or shm_seen:
                raise RuntimeError("UDP-only ROS transport trial failed")
        finally:
            if peer is not None and peer.poll() is None:
                peer.kill()
                peer.wait(2)
            node.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    if (set(name for _, name in socket.if_nameindex()) != {"lo"} or any(Path("/dev/shm").iterdir())
            or os.environ.get("ROS_DOMAIN_ID") != "173" or os.environ.get("ROS_LOCALHOST_ONLY") != "0"
            or os.environ.get("RMW_IMPLEMENTATION") != "rmw_fastrtps_cpp"):
        raise SystemExit("loopback-only namespace and private /dev/shm required")
    if sys.argv[1:] == ["--writer"]:
        writer()
    elif not sys.argv[1:]:
        trial()
    else:
        raise SystemExit("unexpected argument")
