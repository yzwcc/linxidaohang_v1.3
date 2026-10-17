#!/usr/bin/env python3
"""Apply and verify the firmware configuration effects of historical increments."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

HERE = Path(__file__).resolve().parent
PNC = "agibot/software/nav/config"
APP2 = PNC + "/vectorflux_common/a2/app2.yaml"
MPPI = PNC + "/vectorflux_common/a2/mppi_controller.yaml"
VECTOR = PNC + "/vectorflux/vectorflux_app_cfg.yaml"
SKILLS = "agibot/software/agent/bin/cfg/skills.json"

def module():
    spec = importlib.util.spec_from_file_location("apply_pnc_policy", HERE / "pnc_policy/apply_policy.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

def disable_legacy_skill(data, *, verify=False):
    matches = 0
    def walk(value):
        nonlocal matches
        if isinstance(value, dict):
            if (value.get("value") == "exhibit_commentary"
                or value.get("skill_name") == "start_fixed_line_intro"
                or value.get("skillName") == "start_fixed_line_intro"):
                matches += 1
                if verify and value.get("enabled") is not False:
                    raise ValueError("legacy exhibit_commentary mapping is still enabled")
                if not verify:
                    value["enabled"] = False
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(data)
    return matches

def atomic_json(path, data):
    original = path.stat()
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
        shutil.copystat(path, temporary)
        if hasattr(os, "chown"):
            os.chown(temporary, original.st_uid, original.st_gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def run(root, mode, skip_pnc=False):
    root = Path(root)
    result = {"pnc": "explicitly_skipped" if skip_pnc else "balanced-narrow"}
    pnc = module()
    policy = pnc.load_policy(HERE / "pnc_policy/policy.json")
    if not skip_pnc:
        vector = (root / VECTOR).read_text(encoding="utf-8")
        if not re.search(r"""main_config:\s*["']?\.\./config/vectorflux_common/a2/app2\.yaml""", vector):
            raise ValueError("当前 PNC 不是 A2 app2.yaml；本策略不能用于该机型")
        # Validate both files on copies before applying either file.
        with tempfile.TemporaryDirectory(prefix="lingxi-policy-precheck-") as temp:
            for relative, key in ((APP2, "app2.yaml"), (MPPI, "mppi_controller.yaml")):
                path = root / relative
                copy = Path(temp) / key
                shutil.copy2(path, copy)
                pnc.apply_file(copy, policy["files"][key], verify=mode == "verify")
        if mode == "apply":
            pnc.apply_policy(HERE / "pnc_policy/policy.json", root / APP2,
                             root / MPPI, verify=False)
    skills = root / SKILLS
    if skills.is_file():
        data = json.loads(skills.read_text(encoding="utf-8"))
        result["legacy_skill_mappings"] = disable_legacy_skill(data, verify=mode == "verify")
        if mode == "apply" and result["legacy_skill_mappings"]:
            atomic_json(skills, data)
    else:
        result["legacy_skill_mappings"] = "file_absent"
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "apply", "verify"))
    parser.add_argument("--root", default="/")
    parser.add_argument("--skip-pnc-policy", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.mode, args.skip_pnc_policy),
                     ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
