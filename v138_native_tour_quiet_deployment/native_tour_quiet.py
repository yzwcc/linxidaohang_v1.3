#!/usr/bin/env python3
"""Disable only the historical firmware tour mapping, with guarded rollback."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

VERSION = "1.0.10-20261019"
CONFIGS = (
    "/agibot/software/agent/bin/cfg/skills.json",
    "/agibot/software/agent_cfg/skills.json",
)
BACKUPS = "/var/lib/sparkrobot/lingxi-native-tour-quiet/backups"
LIMIT = 4 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def read_file(path):
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise RuntimeError("Expected a regular file: " + str(path))
    with path.open("rb") as stream:
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise RuntimeError("Configuration exceeds size limit: " + str(path))
    return data


def atomic(path, data, metadata):
    mode, uid, gid = metadata
    descriptor, temporary = tempfile.mkstemp(prefix=".lingxi-tour-quiet-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def disable_mapping(document):
    result = copy.deepcopy(document)
    count = changed = 0

    def visit(value):
        nonlocal count, changed
        if isinstance(value, dict):
            if (value.get("value") == "exhibit_commentary"
                    or value.get("skill_name") == "start_fixed_line_intro"
                    or value.get("skillName") == "start_fixed_line_intro"):
                count += 1
                if value.get("enabled") is not False:
                    value["enabled"] = False
                    changed += 1
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(result)
    return result, count, changed


class NativeConfig:
    def __init__(self, root=Path("/")):
        self.root = Path(root).resolve()
        self.backups = self.path(BACKUPS)

    def path(self, absolute):
        return self.root / absolute.lstrip("/")

    def resolve(self, alias):
        if alias not in CONFIGS:
            raise RuntimeError("Unexpected firmware configuration alias")
        path = self.path(alias).resolve(strict=True)
        if self.root != Path("/") and self.root not in path.parents:
            raise RuntimeError("Configuration escaped fixture root")
        return path

    def plan(self):
        entries = []
        seen = set()
        for alias in CONFIGS:
            requested = self.path(alias)
            if not requested.exists() and not requested.is_symlink():
                continue
            path = self.resolve(alias)
            if path in seen:
                continue
            seen.add(path)
            raw = read_file(path)
            document = json.loads(raw)
            if not isinstance(document, (dict, list)):
                raise RuntimeError("Invalid skills.json root: " + alias)
            patched, count, changed = disable_mapping(document)
            after = encoded(patched) if changed else raw
            reference = path.stat()
            entries.append(dict(alias=alias, resolved=str(path), before=digest(raw),
                                after=digest(after), matches=count, changes=changed,
                                mode=stat.S_IMODE(reference.st_mode),
                                uid=reference.st_uid, gid=reference.st_gid))
        result = dict(version=VERSION, entries=entries,
                      matches=sum(item["matches"] for item in entries),
                      changes=sum(item["changes"] for item in entries))
        result["fingerprint"] = digest(encoded(result))
        return result

    def backup_path(self, transaction):
        if not re.fullmatch(r"[0-9]{8}T[0-9]{6}-[a-f0-9]{12}", transaction):
            raise RuntimeError("Invalid transaction ID")
        self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.backups.is_symlink():
            raise RuntimeError("Backup directory must not be a symlink")
        return self.backups / transaction

    def apply(self, transaction, expected):
        plan = self.plan()
        if plan["fingerprint"] != expected:
            raise RuntimeError("Firmware configuration changed after preflight")
        backup = self.backup_path(transaction)
        if backup.exists() or backup.is_symlink():
            raise RuntimeError("Native transaction already exists")
        backup.mkdir(mode=0o700)
        records, replacements = [], []
        for index, entry in enumerate(plan["entries"]):
            if not entry["changes"]:
                continue
            path = self.resolve(entry["alias"])
            raw = read_file(path)
            if digest(raw) != entry["before"] or str(path) != entry["resolved"]:
                raise RuntimeError("Firmware configuration changed before backup")
            patched, _, _ = disable_mapping(json.loads(raw))
            replacement = encoded(patched)
            saved = backup / (str(index) + ".json")
            saved.write_bytes(raw)
            saved.chmod(0o600)
            record_entry = {**entry, "saved": saved.name}
            records.append(record_entry)
            replacements.append((path, replacement, record_entry))
        record = dict(version=VERSION, transaction=transaction, records=records,
                      state="prepared", matches=plan["matches"], changes=plan["changes"])
        manifest = backup / "transaction.json"
        manifest.write_bytes(encoded(record))
        manifest.chmod(0o600)
        written = []
        try:
            for path, replacement, entry in replacements:
                if digest(read_file(path)) != entry["before"]:
                    raise RuntimeError("Concurrent firmware configuration change")
                atomic(path, replacement, (entry["mode"], entry["uid"], entry["gid"]))
                written.append(entry)
            verified = self.plan()
            if verified["changes"]:
                raise RuntimeError("Legacy tour mapping remains enabled")
            record["state"] = "applied"
            manifest.write_bytes(encoded(record))
            return dict(transaction=transaction, backup=str(backup), changes=plan["changes"],
                        matches=plan["matches"], verified=True)
        except BaseException:
            for entry in reversed(written):
                path = self.resolve(entry["alias"])
                if digest(read_file(path)) != entry["after"]:
                    raise RuntimeError("Native rollback blocked by a concurrent change")
                atomic(path, read_file(backup / entry["saved"]),
                       (entry["mode"], entry["uid"], entry["gid"]))
            record["state"] = "restored_after_error"
            manifest.write_bytes(encoded(record))
            raise

    def rollback(self, transaction, *, check=False):
        backup = self.backup_path(transaction)
        if backup.is_symlink():
            raise RuntimeError("Native transaction must not be a symlink")
        record = json.loads(read_file(backup / "transaction.json"))
        if record.get("transaction") != transaction or record.get("version") != VERSION:
            raise RuntimeError("Native backup belongs to another transaction")
        prepared = []
        for entry in record["records"]:
            path = self.resolve(entry["alias"])
            if str(path) != entry["resolved"]:
                raise RuntimeError("Firmware configuration target changed")
            saved = entry["saved"]
            if not re.fullmatch(r"[0-9]+\.json", saved):
                raise RuntimeError("Invalid native backup filename")
            raw = read_file(backup / saved)
            current = digest(read_file(path))
            if digest(raw) != entry["before"] or current not in {entry["after"], entry["before"]}:
                raise RuntimeError("Native backup or live configuration changed; refusing rollback")
            prepared.append((path, raw, entry))
        if check:
            return dict(transaction=transaction, rollback_ready=True)
        for path, raw, entry in prepared:
            atomic(path, raw, (entry["mode"], entry["uid"], entry["gid"]))
        record["state"] = "rolled_back"
        (backup / "transaction.json").write_bytes(encoded(record))
        return dict(transaction=transaction, restored=len(prepared), verified=True)


def reload_native_service():
    subprocess.run(["systemctl", "restart", "agibot_software.service"], check=True, timeout=90)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        commands = (["systemctl", "is-active", "--quiet", "agibot_software.service"],
                    ["pgrep", "-f", "[a]ima-agent-app"],
                    ["pgrep", "-f", "[a]ima-interaction-app"],
                    ["pgrep", "-f", "[a]ima-audiohal-app"])
        if all(subprocess.run(command, capture_output=True, timeout=5).returncode == 0
               for command in commands):
            return dict(service="agibot_software.service", reloaded=True, active=True)
        time.sleep(1)
    raise RuntimeError("Native voice processes did not become ready after restart")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "apply", "verify", "rollback", "check-rollback", "recover", "reload"))
    parser.add_argument("--transaction")
    parser.add_argument("--expected")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    native = NativeConfig()
    if args.report and (args.report.is_symlink() or args.report.exists()):
        raise RuntimeError("Report destination already exists")
    if args.mode in {"apply", "rollback", "recover", "reload"} and os.geteuid() != 0:
        raise RuntimeError("Run the native configuration helper using sudo")
    if args.mode == "reload":
        result = reload_native_service()
    elif args.mode in {"plan", "verify"}:
        result = native.plan()
        if args.mode == "verify" and result["changes"]:
            raise RuntimeError("Legacy native tour mapping is still enabled")
    elif args.mode == "apply":
        result = native.apply(args.transaction or "", args.expected)
    elif args.mode == "recover":
        backup = native.backup_path(args.transaction or "")
        if not backup.exists():
            result = dict(transaction=args.transaction, recovery="no_transaction")
        else:
            manifest = backup / "transaction.json"
            if not manifest.exists():
                # No mutation starts until this durable manifest exists.
                result = dict(transaction=args.transaction, recovery="not_started")
            else:
                result = native.rollback(args.transaction)
    else:
        result = native.rollback(args.transaction or "", check=args.mode == "check-rollback")
    output = encoded(result)
    if args.report:
        if args.report.is_symlink() or args.report.exists():
            raise RuntimeError("Report destination already exists")
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(output)
    print(output.decode("utf-8").strip())


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Native tour configuration failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
