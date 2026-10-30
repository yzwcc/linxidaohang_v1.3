"""Isolated synthetic child/service ownership fixture; no ROS or robot connection."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam")]
from localization_service import LocalizationBinding, LocalizationManager, SelectedMap, serve


def main():
    runs, endpoint = map(Path, sys.argv[1:])
    binding = LocalizationBinding("0" * 32, "live", "slam-" + "b" * 32,
                                  "c" * 64, "d" * 64, "e" * 64)
    fixture = Path(__file__).with_name("managed_localization_child.py")
    manager = LocalizationManager(runs, lambda *_: SelectedMap(runs, binding),
        lambda chosen, output: [sys.executable, "-B", str(fixture), "native", "--output", str(output),
                               "--scenario", "live-normal"],
        {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}, input_mode="live")
    serve(endpoint, manager, parent_input=sys.stdin.buffer,
          status=lambda value: print(json.dumps(value), flush=True))
    (runs / "service-final.json").write_text(json.dumps(manager.runtime()))


if __name__ == "__main__":
    main()
