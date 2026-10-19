"""Check current SDK motion/area pairs despite stale generated enum constants.

Pairs come from the pinned 1bde262f SDK's preset_motion.html table, which
explicitly supersedes pre-v0.8 control-area enums. No ROS library is imported.
"""
import ast
import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace as NS

import pytest
from robot_local_navigation_v3.motion_option_policy import (
    SPECIAL_MOTION_ALIASES, SPECIAL_MOTION_ALLOWED_AREAS, parse_numeric_area_id,
)

DOCUMENTED = {
    "raise_hand": (1001, (1, 2)), "wave": (1002, (1, 2)),
    "handshake": (1003, (1, 2)), "flying_kiss": (1004, (1, 2)),
    "sweetheart": (1007, (1, 2, 3)), "high_five": (1008, (1, 2)),
    "horizontal_arm": (1010, (1, 2, 3)), "chestwave": (1011, (1, 2)),
    "salute": (1013, (1, 2)), "bow": (3001, (11,)),
    "lightwave": (3007, (11,)), "hug": (3008, (11,)), "handx": (3009, (11,)),
    "cheer": (3011, (11,)), "clap": (3017, (11,)), "scratch_head": (3024, (11,)),
    "scratch_back": (3025, (11,)), "bye": (3031, (11,)),
}


@pytest.fixture
def wire():
    spec = importlib.util.find_spec("robot_local_navigation_v3")
    path = Path(next(iter(spec.submodule_search_locations))) / "sdk_motion_controller.py"
    tree = ast.parse(path.read_bytes())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SdkMotionController")
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef)
                and n.name in {"resolve_motion_id", "resolve_area_id", "_dispatch_motion_request_now"}]
    names = {"MOTION_ALIASES", "MOTION_ALLOWED_AREAS", "MOTION_DEFAULT_AREAS", "AREA_ALIASES"}
    assignments = [n for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and
                   any(isinstance(t, ast.Name) and t.id in names for t in
                       (n.targets if isinstance(n, ast.Assign) else [n.target]))]

    class LegacyConstants(type):
        def __getattr__(cls, name):
            return 0

    class PresetMotion(metaclass=LegacyConstants):
        # This stale value really exists in the bundled generated SDK messages.
        INTERACTION_SWEATHEART = 3004

    class Area:
        NONE, LEFT_HAND, RIGHT_HAND, HEAD, WAIST = 0, 1, 2, 4, 8

    env = {
        "McPresetMotion": PresetMotion, "McControlArea": Area,
        "SPECIAL_MOTION_ALIASES": SPECIAL_MOTION_ALIASES,
        "SPECIAL_MOTION_ALLOWED_AREAS": SPECIAL_MOTION_ALLOWED_AREAS,
        "parse_numeric_area_id": parse_numeric_area_id,
        "PRESET_MOTION_INPUT_SOURCE_NAME": "node.preset_motion",
        "PRESET_MOTION_INPUT_SOURCE_PRIORITY": 40,
        "PRESET_MOTION_INPUT_SOURCE_TIMEOUT_MS": 1000,
        "RequestHeader": lambda: NS(stamp=None),
    }
    program = ast.Module(body=[ast.ImportFrom(module="__future__",
        names=[ast.alias(name="annotations")], level=0), *assignments, cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(program), str(path), "exec"), env)
    controller = env["SdkMotionController"]()
    controller.node = NS(get_logger=lambda: NS(warning=lambda *_: None),
                         get_clock=lambda: NS(now=lambda: NS(to_msg=lambda: "fixture-stamp")))
    controller.wait_timeout_sec = 0
    return controller, env


@pytest.mark.parametrize("name", DOCUMENTED)
def test_every_offered_motion_matches_current_vendor_pair_table(wire, name):
    controller, env = wire
    motion_id, areas = DOCUMENTED[name]
    assert controller.resolve_motion_id(name) == motion_id
    assert env["MOTION_ALLOWED_AREAS"][motion_id] == set(areas)
    for area in areas:
        assert controller.resolve_area_id(area, motion_id=motion_id) == area


@pytest.mark.parametrize("has_input_source", [False, True])
def test_actual_request_fields_support_pinned_and_newer_sdk_schema(wire, has_input_source):
    controller, env = wire
    fields = ["header", "area", "motion", "interrupt", "ani_path", "play_timestamp"]
    if has_input_source:
        fields.append("input_source")

    def init(request):
        if has_input_source:
            request.input_source = NS()

    request_type = type("SdkRequest", (), {"__slots__": fields, "__init__": init})
    env["SetMcPresetMotion"] = NS(Request=request_type)
    sent = []
    future = NS(add_done_callback=lambda callback: None)
    controller.motion_client = NS(wait_for_service=lambda **kw: True,
                                  call_async=lambda request: sent.append(request) or future)
    info = NS(motion_id=controller.resolve_motion_id("sweetheart"), area_id=3,
              interrupt=False, ani_path="", play_timestamp=0)
    assert controller._dispatch_motion_request_now(info, 1, None, threading.Event()) is future
    request = sent[0]
    assert request.motion.value == 1007
    assert request.area.value == 3
    assert request.header.stamp == "fixture-stamp"
    assert request.interrupt is False and request.ani_path == "" and request.play_timestamp == 0
    if has_input_source:
        assert request.input_source.name == "node.preset_motion"
        assert request.input_source.priority == 40
        assert request.input_source.timeout == 1000
