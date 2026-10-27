from __future__ import annotations

import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from r19_full_deployment import build_r19_release as release


class ReleaseTransformationTests(unittest.TestCase):
    def test_removing_class_removes_its_decorators(self) -> None:
        source = (
            "from dataclasses import dataclass, field\n"
            "@dataclass\n"
            "class Removed:\n"
            "    enabled: bool = True\n"
            "\n"
            "@dataclass\n"
            "class Remaining:\n"
            "    value: int = 1\n"
            "    items: list = field(default_factory=list)\n"
        )
        result = release.remove_python_class(source, "Removed")
        self.assertEqual(result.count("@dataclass"), 1)
        namespace: dict = {"__name__": __name__}
        exec(compile(result, "<transformed>", "exec"), namespace)
        remaining = namespace["Remaining"]
        self.assertIsNot(remaining().items, remaining().items)

    def test_removing_class_removes_stacked_and_multiline_decorators(self) -> None:
        source = (
            "before = 1\n"
            "@outer(\n"
            "    enabled=True,\n"
            ")\n"
            "@inner\n"
            "class Removed:\n"
            "    pass\n"
            "\n"
            "after = 2\n"
        )
        result = release.remove_python_class(source, "Removed")
        self.assertEqual(result, "before = 1\n\nafter = 2\n")

    def test_undecorated_class_and_adjacent_code(self) -> None:
        source = "before = 1\nclass Removed:\n    pass\nafter = 2\n"
        self.assertEqual(release.remove_python_class(source, "Removed"), "before = 1\nafter = 2\n")

    def test_removing_static_method_keeps_following_method_bound(self) -> None:
        source = (
            "class Client:\n"
            "    @staticmethod\n"
            "    def removed():\n"
            "        return True\n"
            "\n"
            "    def cancel(self):\n"
            "        return False\n"
        )
        result = release.remove_python_method(source, "Client", "removed")
        namespace: dict = {}
        exec(compile(result, "<transformed-method>", "exec"), namespace)
        self.assertFalse(namespace["Client"]().cancel())

    def test_final_r18_navigation_models_import(self) -> None:
        with tempfile.TemporaryDirectory(prefix="r19-models-test-") as temporary:
            root = Path(temporary)
            release.git_snapshot(root)
            navigation = root / release.R18_SOURCE_PATHS[1]
            release.strip_navigation_path_safety(navigation)
            release.validate_navigation_models(navigation)
            release.validate_navigation_contracts(navigation)
            bridge = navigation / "robot_local_navigation_v3/lingxi_nav_bridge.py"
            source = bridge.read_text(encoding="utf-8")
            bridge.write_text(source.replace("            odom_topic=self.odom_topic,\n",
                                             "            odom_topic=self.odom_topic, map_root='invalid',\n"),
                              encoding="utf-8")
            with self.assertRaises(TypeError):
                release.validate_navigation_contracts(navigation)

    def test_model_validation_rejects_broken_generated_payload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="r19-invalid-models-") as temporary:
            root = Path(temporary)
            package = root / "robot_local_navigation_v3"
            package.mkdir()
            (package / "models.py").write_text("raise RuntimeError('invalid payload')\n", encoding="ascii")
            with self.assertRaises(subprocess.CalledProcessError):
                release.validate_navigation_models(root)

    def test_voice_bridge_imports_without_optional_aimdk_media_service(self) -> None:
        with tempfile.TemporaryDirectory(prefix="r19-voice-test-") as temporary:
            root = Path(temporary)
            release.git_snapshot(root)
            bridge = root / "deploy/robot/robot_lingxi_bridge.py"
            release.configure_voice_bridge(bridge)
            modules = {name: types.ModuleType(name) for name in (
                "rclpy", "rclpy.node", "aimdk_msgs", "aimdk_msgs.msg", "aimdk_msgs.srv",
            )}
            modules["rclpy.node"].Node = object
            for name in ("McActionCommand", "McControlArea", "McPresetMotion", "TtsPriorityLevel"):
                setattr(modules["aimdk_msgs.msg"], name, type(name, (), {}))
            for name in ("ExecuteActionResource", "PlayAudioFile", "PlayTts", "SetMcAction", "SetMcPresetMotion"):
                setattr(modules["aimdk_msgs.srv"], name, type(name, (), {}))
            namespace = {"__name__": "r19_voice_test"}
            with patch.dict(sys.modules, modules):
                exec(compile(bridge.read_text(encoding="utf-8"), str(bridge), "exec"), namespace)
            self.assertIsNone(namespace["PlayMediaFile"])
            result = namespace["LingxiRobotBridge"]._sing_media_file(types.SimpleNamespace(media_client=None), "test")
            self.assertFalse(result["accepted"])
            self.assertEqual(result["status"], "media_service_unsupported")


if __name__ == "__main__":
    unittest.main()
