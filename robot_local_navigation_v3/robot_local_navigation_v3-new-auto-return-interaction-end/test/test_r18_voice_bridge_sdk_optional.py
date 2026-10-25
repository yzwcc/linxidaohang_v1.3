from pathlib import Path
import ast
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[3]
BRIDGE = ROOT / "deploy" / "robot" / "robot_lingxi_bridge.py"


class VoiceBridgeSdkCompatibilityTest(unittest.TestCase):
    def test_sdk_without_play_media_file_imports_bridge_capability_as_unavailable(self) -> None:
        tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
        srv_import = next(
            node for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "aimdk_msgs.srv"
        )
        optional_import = next(
            node for node in tree.body
            if isinstance(node, ast.Try)
            and any(
                isinstance(child, ast.ImportFrom)
                and child.module == "aimdk_msgs.srv"
                and any(alias.name == "PlayMediaFile" for alias in child.names)
                for child in node.body
            )
        )
        srv_module = types.ModuleType("aimdk_msgs.srv")
        for alias in srv_import.names:
            setattr(srv_module, alias.name, type(alias.name, (), {}))
        aimdk_module = types.ModuleType("aimdk_msgs")
        aimdk_module.srv = srv_module
        prior_aimdk = sys.modules.get("aimdk_msgs")
        prior_srv = sys.modules.get("aimdk_msgs.srv")
        sys.modules["aimdk_msgs"] = aimdk_module
        sys.modules["aimdk_msgs.srv"] = srv_module
        namespace: dict[str, object] = {}
        try:
            exec(compile(ast.Module(body=[srv_import, optional_import], type_ignores=[]), str(BRIDGE), "exec"), namespace)
            self.assertIsNone(namespace["PlayMediaFile"])
        finally:
            if prior_aimdk is None:
                sys.modules.pop("aimdk_msgs", None)
            else:
                sys.modules["aimdk_msgs"] = prior_aimdk
            if prior_srv is None:
                sys.modules.pop("aimdk_msgs.srv", None)
            else:
                sys.modules["aimdk_msgs.srv"] = prior_srv

    def test_optional_media_service_does_not_block_bridge_import(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = [node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == "aimdk_msgs.srv"]
        imported = {alias.name for node in imports for alias in node.names}
        self.assertNotIn("PlayMediaFile", imported)
        self.assertIn("import PlayMediaFile", source)
        self.assertIn("except ImportError", source)
        self.assertIn("if PlayMediaFile is not None", source)

    def test_missing_media_sdk_returns_a_specific_capability_error(self) -> None:
        tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
        bridge_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "LingxiRobotBridge"
        )
        methods = {
            node.name: node
            for node in bridge_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        init_source = ast.unparse(methods["__init__"])
        self.assertIn("if PlayMediaFile is not None", init_source)

        health_source = ast.unparse(methods["health"])
        self.assertIn("self.media_client is not None", health_source)
        self.assertIn("media_supported_by_sdk", health_source)
        self.assertIn("media_supported_by_sdk", ast.unparse(methods["health"]))

        sing_source = ast.unparse(methods["_sing_media_file"])
        self.assertLess(
            sing_source.index("if self.media_client is None"),
            sing_source.index("self.media_client.wait_for_service"),
        )
        self.assertIn("media_service_unsupported_by_sdk", sing_source)
        self.assertIn("media-file singing is disabled", init_source)


if __name__ == "__main__":
    unittest.main()
