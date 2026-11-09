import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import probe
import collect_resources


class InterfaceProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.base = self.root / "src/aimdk_msgs/interface"
        self.base.mkdir(parents=True)

    def definition(self, name, text):
        path = self.base / "robot/test" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_extracts_declarations_and_hash_without_comments(self):
        path = self.definition(
            "ExecuteActionResource.srv", "# a comment\nstring meta # seek is not documented\n---\nCommonResponse header\n"
        )
        result = probe.inspect_sdk(self.root)
        entry = result["core_definitions"][0]
        self.assertEqual(entry["declarations"], [
            {"line": 2, "declaration": "string meta"},
            {"line": 3, "declaration": "---"},
            {"line": 4, "declaration": "CommonResponse header"},
        ])
        self.assertEqual(len(entry["sha256"]), 64)
        before = entry["sha256"]
        path.write_text("string changed\n", encoding="utf-8")
        self.assertNotEqual(probe.inspect_sdk(self.root)["core_definitions"][0]["sha256"], before)

    def test_pause_name_and_timestamp_never_approve_resume(self):
        self.definition("PauseDance.srv", "uint64 offset_ms\n---\nbool success\n")
        self.definition("SetMcPresetMotion.srv", "uint64 play_timestamp\n---\nCommonTaskResponse response\n")
        result = probe.inspect_sdk(self.root)
        self.assertIn("robot/test/PauseDance.srv", result["candidate_names_only"])
        self.assertEqual(result["status"], "partial")
        report = probe.new_report()
        report["sdk"] = result
        self.assertEqual(report["playback_gate"], "NOT_VALIDATED")
        self.assertEqual(set(report["capabilities"].values()), {"UNVERIFIED"})

    def test_complete_collection_still_does_not_validate_hardware(self):
        for name in probe.CORE_INTERFACES:
            self.definition(name, "string field\n")
        output = self.root / "report.json"
        self.assertEqual(probe.main(["--sdk-root", str(self.root), "--output", str(output)]), 0)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["sdk"]["status"], "ok")
        self.assertEqual(result["physical_validation"], "NOT_RUN")
        self.assertEqual(result["playback_gate"], "NOT_VALIDATED")

    def test_only_selected_tree_is_read(self):
        self.definition("ExecuteActionResource.srv", "string meta\n")
        (self.root / "secrets.env").write_text("DO_NOT_COLLECT=example", encoding="utf-8")
        rendered = json.dumps(probe.inspect_sdk(self.root))
        self.assertNotIn("DO_NOT_COLLECT", rendered)
        self.assertNotIn(str(self.root), rendered)

    def test_missing_tree_and_empty_tree_are_not_success(self):
        with self.assertRaises(ValueError):
            probe.inspect_sdk(self.root)
        other = self.root / "other"
        other.mkdir()
        with self.assertRaises(ValueError):
            probe.interface_root(other)

    def test_report_is_utf8_and_preserves_existing_file(self):
        output = self.root / "report.json"
        probe.write_report({"name": "舞蹈"}, output)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"name": "舞蹈"})
        with self.assertRaises(FileExistsError):
            probe.write_report({"changed": True}, output)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"name": "舞蹈"})

    def test_failed_sdk_collection_persists_failure(self):
        output = self.root / "report.json"
        code = probe.main(["--sdk-root", str(self.root / "missing"), "--output", str(output)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["sdk"]["status"], "error")

    def test_ros_commands_are_fixed_observations_with_timeouts(self):
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout="observed", stderr="")
        result = probe.inspect_ros(2, runner)
        self.assertEqual(len(calls), len(probe.ROS_QUERIES))
        self.assertTrue(all(entry["status"] == "ok" for entry in result.values()))
        for command, kwargs in calls:
            self.assertIn(tuple(command[:3]), (
                ("ros2", "service", "list"), ("ros2", "topic", "list"),
                ("ros2", "interface", "list"), ("ros2", "interface", "show"),
            ))
            self.assertEqual(kwargs["timeout"], 2)
            self.assertIs(kwargs["shell"], False)

    def test_missing_ros_timeout_and_rejected_query_are_distinct(self):
        for exception, expected in (
            (FileNotFoundError(), "missing"),
            (subprocess.TimeoutExpired("ros2", 1), "timeout"),
            (PermissionError("denied"), "error"),
        ):
            def runner(*args, **kwargs):
                raise exception
            self.assertEqual(probe.observation(("ros2", "service", "list"), 1, runner)["status"], expected)
        runner = lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 2, stdout="", stderr="not sourced")
        self.assertEqual(probe.observation(("ros2", "service", "list"), 1, runner)["status"], "error")


def response(code=0, resources=()):
    return SimpleNamespace(
        header=SimpleNamespace(header=SimpleNamespace(code=code), status=SimpleNamespace(value=999)),
        robot_resources=list(resources),
    )


def resource(key="key", version="v1", name="舞蹈"):
    return SimpleNamespace(resource_key=key, current_version=SimpleNamespace(version=version, name=name))


class ResourceProbeTests(unittest.TestCase):
    def ros_modules(self, discovered=True, done=True):
        """Small stand-ins for optional ROS imports; no ROS process is started."""
        ros = ModuleType("rclpy")
        ctx = ModuleType("rclpy.context")
        ctx.Context = Mock()
        srv = ModuleType("aimdk_msgs.srv")
        srv.GetRobotResources = type("GetRobotResources", (), {"Request": type("Request", (), {})})
        node = Mock()
        client = node.create_client.return_value
        client.wait_for_service.return_value = discovered
        future = client.call_async.return_value
        future.done.return_value = done
        future.result.return_value = response(resources=[resource()])
        ros.create_node = Mock(return_value=node)
        ros.init = Mock()
        ros.shutdown = Mock()
        ros.spin_until_future_complete = Mock()
        modules = {"rclpy": ros, "rclpy.context": ctx, "aimdk_msgs": ModuleType("aimdk_msgs"), "aimdk_msgs.srv": srv}
        return modules, ros, node, client, future, srv.GetRobotResources

    def test_live_query_calls_only_typed_resource_service_and_cleans_up(self):
        modules, ros, node, client, future, service_type = self.ros_modules()
        with patch.dict(sys.modules, modules):
            result = collect_resources.query_resources("/observed/resources", 2)
        node.create_client.assert_called_once_with(service_type, "/observed/resources")
        client.wait_for_service.assert_called_once_with(timeout_sec=2)
        client.call_async.assert_called_once()
        self.assertIsInstance(client.call_async.call_args.args[0], service_type.Request)
        ros.spin_until_future_complete.assert_called_once_with(node, future, timeout_sec=2)
        node.destroy_node.assert_called_once()
        ros.shutdown.assert_called_once()
        self.assertEqual(result[0]["version"], "v1")

    def test_discovery_timeout_does_not_dispatch_request(self):
        modules, ros, node, client, _, _ = self.ros_modules(discovered=False)
        with patch.dict(sys.modules, modules), self.assertRaises(TimeoutError):
            collect_resources.query_resources("/observed/resources", 2)
        client.call_async.assert_not_called()
        node.destroy_node.assert_called_once()
        ros.shutdown.assert_called_once()

    def test_response_timeout_cancels_local_future_without_retry(self):
        modules, ros, node, client, future, _ = self.ros_modules(done=False)
        with patch.dict(sys.modules, modules), self.assertRaises(TimeoutError):
            collect_resources.query_resources("/observed/resources", 2)
        client.call_async.assert_called_once()
        future.cancel.assert_called_once()
        node.destroy_node.assert_called_once()
        ros.shutdown.assert_called_once()

    def test_inventory_preserves_resource_version_and_unicode(self):
        result = collect_resources.decode_resources(response(resources=[resource(), resource(), resource(version="v2")]))
        self.assertEqual(result, [
            {"resource_key": "key", "version": "v1", "name": "舞蹈"},
            {"resource_key": "key", "version": "v2", "name": "舞蹈"},
        ])

    def test_error_is_not_an_empty_successful_inventory(self):
        with self.assertRaises(ValueError):
            collect_resources.decode_resources(response(code=5))
        for item in (resource(key=""), resource(version=""), resource(version=1)):
            with self.assertRaises(ValueError):
                collect_resources.decode_resources(response(resources=[item]))
        self.assertEqual(collect_resources.decode_resources(response()), [])

    def test_unavailable_robot_retains_failure_and_gate(self):
        with patch.object(collect_resources, "query_resources", side_effect=TimeoutError("offline")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = collect_resources.main(["--service", "/observed/resources"])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(report["collection_status"], "error")
        self.assertIsNone(report["resources"])
        self.assertEqual(report["playback_gate"], "NOT_VALIDATED")

    def test_bad_service_name_is_rejected_before_query(self):
        with patch.object(collect_resources, "query_resources") as query:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                collect_resources.main(["--service", "/service;bad"])
            query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
