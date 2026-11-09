#!/usr/bin/env python3
"""Read-only AIMDK interface evidence collection; never approves playback."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


CORE_INTERFACES = (
    "ExecuteActionResource.srv",
    "GetRobotResources.srv",
    "SetMcPresetMotion.srv",
    "McPlayerState.msg",
    "AudioFile.msg",
    "PlayMediaFileRequest.msg",
    "CommonResponse.msg",
    "RobotResource.msg",
    "CurrentVersion.msg",
)
CANDIDATE_NAME = re.compile(
    r"pause|resume|seek|play|progress|checkpoint|phase|resource|audio|media|motion",
    re.IGNORECASE,
)
CAPABILITIES = (
    "authoritative_position_and_completion",
    "pause_checkpoint_and_stand",
    "resume_body_from_stand_at_checkpoint",
    "synchronized_audio_resume_and_voice_handoff",
    "target_version_and_resource_compatibility",
)
# Fixed observation commands only. Never invoke service call, topic pub, param
# set, launch, run, a sample player, or a low-level motion controller.
ROS_QUERIES = (
    ("services", ("ros2", "service", "list", "-t")),
    ("topics", ("ros2", "topic", "list", "-t")),
    ("interfaces", ("ros2", "interface", "list")),
    *(
        (name, ("ros2", "interface", "show", "aimdk_msgs/" + name))
        for name in (
            "srv/ExecuteActionResource",
            "srv/GetRobotResources",
            "srv/SetMcPresetMotion",
            "msg/McPlayerState",
            "msg/PlayMediaFileRequest",
        )
    ),
)


def interface_root(root):
    """Accept an artifact root or an already selected interface/share root."""
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("SDK root must be a directory")
    for suffix in (
        "src/aimdk_msgs/interface",
        "interface",
        "share/aimdk_msgs",
        "src/aimdk_msgs/prebuilt_aarch64/share/aimdk_msgs",
    ):
        candidate = root / suffix
        if candidate.is_dir():
            return candidate
    if root.name == "interface" or (root / "srv").is_dir():
        return root
    raise ValueError("No interface tree found; select the SDK artifact or aimdk_msgs share directory")


def inspect_sdk(root):
    base = interface_root(root)
    files = sorted(
        p for p in base.rglob("*")
        if p.is_file() and p.suffix in (".srv", ".msg", ".action")
        and not p.is_symlink()
    )
    if not files:
        raise ValueError("No ROS .srv/.msg/.action definitions found")
    evidence = []
    candidates = []
    for path in files:
        relative = path.relative_to(base).as_posix()
        # Do not follow a symlinked parent outside the explicitly selected tree.
        if not path.resolve().is_relative_to(base):
            continue
        if CANDIDATE_NAME.search(path.stem):
            candidates.append(relative)
        if path.name not in CORE_INTERFACES:
            continue
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024:
            raise ValueError("Unexpectedly large interface: " + relative)
        source = raw.decode("utf-8-sig")
        declarations = []
        for number, line in enumerate(source.splitlines(), 1):
            declaration = line.split("#", 1)[0].strip()
            if declaration:
                declarations.append({"line": number, "declaration": declaration})
        evidence.append({
            "path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "declarations": declarations,
        })
    found = {Path(entry["path"]).name for entry in evidence}
    missing = sorted(set(CORE_INTERFACES) - found)
    return {
        "status": "ok" if not missing else "partial",
        "interface_count": len(files),
        "core_definitions": evidence,
        "missing_core_definitions": missing,
        "candidate_names_only": candidates,
        "note": "Names and field presence are evidence, not proof of pause/resume support.",
    }


def observation(command, timeout, runner=subprocess.run):
    try:
        result = runner(
            list(command), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False, shell=False,
        )
    except FileNotFoundError:
        return {"command": list(command), "status": "missing", "error": "ros2 not found"}
    except subprocess.TimeoutExpired:
        return {"command": list(command), "status": "timeout", "timeout_seconds": timeout}
    except OSError as exc:
        return {"command": list(command), "status": "error", "error": str(exc)}
    return {
        "command": list(command),
        "status": "ok" if result.returncode == 0 else "error",
        "returncode": result.returncode,
        "stdout": result.stdout[:64000],
        "stderr": result.stderr[:4000],
        "truncated": len(result.stdout) > 64000 or len(result.stderr) > 4000,
    }


def inspect_ros(timeout, runner=subprocess.run):
    return {name: observation(command, timeout, runner) for name, command in ROS_QUERIES}


def new_report(model=None, firmware=None, aimdk=None):
    return {
        "schema_version": 1,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "interface_evidence_only",
        "physical_validation": "NOT_RUN",
        "playback_gate": "NOT_VALIDATED",
        "target_operator_supplied": {
            "model": model, "firmware": firmware, "aimdk_version": aimdk,
        },
        "capabilities": {name: "UNVERIFIED" for name in CAPABILITIES},
        "sdk": None,
        "ros": None,
        "limitations": [
            "An offline SDK does not establish the running robot version.",
            "Discovery success does not establish motion support or execution success.",
            "No robot action, audio playback, configuration change, or service call is performed.",
        ],
    }


def write_report(report, output=None):
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output is None:
        sys.stdout.write(rendered)
    else:
        # Preserve prior evidence rather than silently replacing a report.
        with Path(output).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-root", type=Path, help="Local extracted SDK artifact or interface directory")
    parser.add_argument("--ros", action="store_true", help="Run fixed read-only ROS discovery commands")
    parser.add_argument("--timeout", type=float, default=5.0, help="Seconds per ROS observation (default: 5)")
    parser.add_argument("--robot-model")
    parser.add_argument("--firmware")
    parser.add_argument("--aimdk-version")
    parser.add_argument("--output", type=Path, help="New JSON report file; parent directory must exist")
    args = parser.parse_args(argv)
    if not args.sdk_root and not args.ros:
        parser.error("select --sdk-root and/or --ros")
    if not 0 < args.timeout <= 30:
        parser.error("--timeout must be greater than 0 and at most 30")
    report = new_report(args.robot_model, args.firmware, args.aimdk_version)
    complete = True
    if args.sdk_root:
        try:
            report["sdk"] = inspect_sdk(args.sdk_root)
            complete = report["sdk"]["status"] == "ok"
        except (OSError, ValueError) as exc:
            report["sdk"] = {"status": "error", "error": str(exc)}
            complete = False
    if args.ros:
        report["ros"] = inspect_ros(args.timeout)
        complete = complete and all(
            value["status"] == "ok" and not value.get("truncated", False)
            for value in report["ros"].values()
        )
    try:
        write_report(report, args.output)
    except OSError as exc:
        print("Cannot write report: " + str(exc), file=sys.stderr)
        return 1
    return 0 if complete else 1


if __name__ == "__main__":
    # Windows terminals may otherwise use GBK; reports are always UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
