#!/usr/bin/env python3
"""Query only typed GetRobotResources; never execute the returned resources."""

import argparse
from pathlib import Path
import re
import sys

from probe import new_report, write_report


def decode_resources(response):
    # CommonResponse.status is not the service success code.
    if response.header.header.code != 0:
        raise ValueError("GetRobotResources returned a nonzero response code")
    resources = []
    seen = set()
    for resource in response.robot_resources:
        key = resource.resource_key
        version = resource.current_version.version
        name = resource.current_version.name
        if not all(isinstance(value, str) for value in (key, version, name)):
            raise ValueError("Unexpected resource field types")
        if not key.strip() or not version.strip():
            raise ValueError("Resource has no key or current version; inventory is incomplete")
        identity = (key, version)
        if identity in seen:
            continue
        seen.add(identity)
        resources.append({"resource_key": key, "version": version, "name": name})
    return resources


def query_resources(service, timeout):
    # Imports remain optional: offline interface inspection needs only Python.
    import rclpy
    from rclpy.context import Context
    from aimdk_msgs.srv import GetRobotResources

    context = Context()
    rclpy.init(context=context)
    node = None
    try:
        node = rclpy.create_node(
            "lingxi_dance_resource_probe", context=context,
            start_parameter_services=False, enable_rosout=False,
        )
        client = node.create_client(GetRobotResources, service)
        if not client.wait_for_service(timeout_sec=timeout):
            raise TimeoutError("GetRobotResources is unavailable")
        future = client.call_async(GetRobotResources.Request())
        rclpy.spin_until_future_complete(node, future, timeout_sec=timeout)
        if not future.done():
            future.cancel()
            raise TimeoutError("GetRobotResources response timed out")
        response = future.result()
        if response is None:
            raise RuntimeError("GetRobotResources returned no response")
        return decode_resources(response)
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown(context=context)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True, help="Observed endpoint of type aimdk_msgs/srv/GetRobotResources")
    parser.add_argument("--timeout", type=float, default=5.0, help="Seconds each for discovery and response")
    parser.add_argument("--output", type=Path, help="New JSON report; parent directory must exist")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"/(?:[A-Za-z_][A-Za-z0-9_]*/)*[A-Za-z_][A-Za-z0-9_]*", args.service):
        parser.error("--service must be an absolute ROS service name")
    if not 0 < args.timeout <= 30:
        parser.error("--timeout must be greater than 0 and at most 30")
    report = new_report()
    report["limitations"] = [
        "Only GetRobotResources is called; no motion or audio is started.",
        "A listed resource is not evidence that pause/resume is supported.",
    ]
    report["resource_service"] = args.service
    try:
        report["resources"] = query_resources(args.service, args.timeout)
        report["collection_status"] = "ok"
    except Exception as exc:
        # Persist an unavailable/failed query as evidence, never as an empty
        # successful catalog. This also covers missing ROS/AIMDK installations.
        report["resources"] = None
        report["collection_status"] = "error"
        report["error"] = type(exc).__name__ + ": " + str(exc)
    try:
        write_report(report, args.output)
    except OSError as exc:
        print("Cannot write report: " + str(exc), file=sys.stderr)
        return 1
    return 0 if report["collection_status"] == "ok" else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
