"""Build r4 from versioned sources and the verified r3 third-party components."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import stat
import tarfile
import zipfile

HERE = Path(__file__).resolve().parent
DELIVERY = HERE.parent
ROOT = HERE.parents[2]
SOURCE = DELIVERY / "source"
BASE = ROOT / "deliveries/V1.3.9-20261015"
BASE_OUTER = BASE / "lingxi-v1.3.9-complete-20261015-r3-deployment"
BASE_INNER = BASE / ".build/sparkrobot-lingxi-nav-internal-1.0.21-20261015"
VERSION = "1.3.9-complete-20261019-r4"
NAV_VERSION = "1.0.22-20261019"
NAME = f"lingxi-v{VERSION}-deployment"
NAV_NAME = f"sparkrobot-lingxi-nav-internal-{NAV_VERSION}"
INNER = DELIVERY / ".build" / NAV_NAME
OUTER = DELIVERY / NAME
JAVA = SOURCE / "java/target/robot-local-navigation-java-0.1.0-all.jar"
GENERATED = {"__pycache__", ".pytest_cache"}
EXCLUDES = shutil.ignore_patterns("node_modules", "dist", "target", *GENERATED, "*.pyc", "artifacts", "*.log")
TEXT = {".sh", ".py", ".js", ".cjs", ".mjs", ".ts", ".html", ".css", ".json", ".md", ".java", ".xml", ".properties", ".env", ".service"}


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.write_text(value.replace("\r\n", "\n"), encoding="utf-8", newline="\n")


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copytree(source, target):
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=EXCLUDES)


def source_hashes():
    return {
        p.relative_to(SOURCE).as_posix(): sha(p)
        for p in sorted(SOURCE.rglob("*")) if p.is_file() and not any(
            part in {"node_modules", "target", "dist", *GENERATED, "artifacts"} for part in p.relative_to(SOURCE).parts
        ) and p.suffix != ".log"
    }


def sums(stage, name):
    for p in stage.rglob("*"):
        if p.is_file() and p.suffix in TEXT and "node_modules" not in p.parts and not p.is_relative_to(stage / "payload/runtime"):
            data = p.read_bytes()
            if b"\r\n" in data:
                p.write_bytes(data.replace(b"\r\n", b"\n"))
    write(stage / name, "".join(
        f"{sha(p)}  {p.relative_to(stage).as_posix()}\n"
        for p in sorted(stage.rglob("*")) if p.is_file() and p.name != name
    ))


def executable(relative):
    return relative.endswith(".sh") or relative.endswith("/runtime/node") or "/runtime/java/bin/" in relative or relative.endswith("/lib/jspawnhelper")


def archive(stage, output, checksum):
    sums(stage, checksum)
    output.mkdir(parents=True, exist_ok=True)
    for suffix in (".tar.gz", ".zip"):
        target = output / (stage.name + suffix)
        if target.exists():
            raise RuntimeError(f"Archive already exists: {target}")
        temporary = target.with_name(target.name + ".partial")
        if suffix == ".tar.gz":
            def normalize(info):
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mode = 0o755 if info.isdir() or executable(info.name) else 0o644
                return info
            with tarfile.open(temporary, "w:gz", format=tarfile.PAX_FORMAT) as stream:
                stream.add(stage, arcname=stage.name, filter=normalize)
        else:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as stream:
                for p in sorted(stage.rglob("*")):
                    if p.is_file():
                        name = stage.name + "/" + p.relative_to(stage).as_posix()
                        item = zipfile.ZipInfo(name)
                        item.create_system = 3
                        item.external_attr = (stat.S_IFREG | (0o755 if executable(name) else 0o644)) << 16
                        stream.writestr(item, p.read_bytes())
        temporary.replace(target)
        write(target.with_name(target.name + ".sha256"), f"{sha(target)}  {target.name}\n")
        print(target.name, sha(target), target.stat().st_size, flush=True)


def check_java_changes():
    baseline = BASE_INNER / "payload/java/robot-local-navigation-java-all.jar"
    with zipfile.ZipFile(baseline) as old, zipfile.ZipFile(JAVA) as new:
        changed = []
        classes = {n for n in old.namelist() + new.namelist() if n.endswith(".class")}
        for name in sorted(classes):
            if name in old.namelist() and name in new.namelist() and old.read(name) == new.read(name):
                continue
            if not name.startswith("com/agibot/aiem/nav/LingxiNavAgentApp"):
                raise RuntimeError("Unexpected Java class change: " + name)
            changed.append(name)
    if not changed:
        raise RuntimeError("Java command acknowledgement fix was not built")
    return changed


def remove_generated(stage):
    stage = stage.resolve()
    if not stage.is_relative_to(DELIVERY.resolve()):
        raise ValueError("Cleanup target must stay inside this delivery")
    for path in list(stage.rglob("*")):
        if not path.exists():
            continue
        if path.name in GENERATED and path.is_dir():
            if not path.resolve().is_relative_to(stage):
                raise ValueError("Generated cache escapes staging")
            shutil.rmtree(path)


def sanitize_stage():
    for stage_dir in (INNER, OUTER):
        remove_generated(stage_dir)
    provenance = json.loads((INNER / "source-provenance.json").read_text(encoding="utf-8"))
    provenance["source_files"] = {
        name: value for name, value in provenance["source_files"].items()
        if not any(part in GENERATED for part in Path(name).parts)
    }
    if source_hashes() != provenance["source_files"] or sha(JAVA) != provenance["java_sha256"]:
        raise RuntimeError("Sources or compiled Java changed after staging")
    write(INNER / "source-provenance.json", provenance)
    manifest = json.loads((INNER / "payload-manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = [
        entry for entry in manifest["files"]
        if not any(part in GENERATED for part in Path(entry["path"]).parts)
    ]
    for entry in manifest["files"]:
        path = INNER / "payload" / entry["path"]
        if sha(path) != entry["sha256"] or path.stat().st_size != entry["size"]:
            raise RuntimeError("Runtime changed after staging: " + entry["path"])
    if {e["path"] for e in manifest["files"]} != {
        p.relative_to(INNER / "payload").as_posix() for p in (INNER / "payload").rglob("*") if p.is_file()
    }:
        raise RuntimeError("Unexpected payload after staging")
    write(INNER / "payload-manifest.json", manifest)
    sums(INNER, "SHA256SUMS")


def stage():
    if INNER.exists() or OUTER.exists():
        raise RuntimeError("Staging directory already exists; preserve it and investigate before rebuilding")
    manifest = json.loads((BASE_OUTER / "bundle-manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["components"].items():
        if sha(BASE_OUTER / "components" / name) != expected:
            raise RuntimeError("Baseline component mismatch: " + name)
    for line in (BASE_INNER / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        if sha(BASE_INNER / name) != expected:
            raise RuntimeError("Baseline inner mismatch: " + name)
    if (SOURCE / "server/package-lock.json").read_bytes() != (BASE_INNER / "source/server/package-lock.json").read_bytes():
        raise RuntimeError("Production dependencies changed; rebuild dependencies explicitly")
    changed_classes = check_java_changes()
    shutil.copytree(BASE_INNER, INNER)
    copytree(SOURCE, INNER / "source")
    copytree(SOURCE / "navigation/robot_local_navigation_v3", INNER / "payload/navigation/robot_local_navigation_v3")
    copytree(SOURCE / "server/dist", INNER / "payload/console/dist")
    copy(JAVA, INNER / "payload/java/robot-local-navigation-java-all.jar")
    for name in ("install.sh", "configure.sh", "control.sh", "quick_install.sh"):
        copy(HERE / name, INNER / name)
    for name in ("scripts", "templates", "systemd", "docs"):
        copytree(HERE / name, INNER / name)
    copy(HERE / "docs/V1.3.9-完整部署说明.md", INNER / "docs/内部部署与命令手册.md")
    info = json.loads((INNER / "release-info.json").read_text(encoding="utf-8"))
    info.update(version=NAV_VERSION, java_sha256=sha(JAVA),
                created_at=datetime.now(timezone.utc).isoformat(),
                baseline="1.0.21-20261015")
    write(INNER / "release-info.json", info)
    write(INNER / "source-provenance.json", {
        "baseline": manifest["version"], "source_files": source_hashes(),
        "java_sha256": sha(JAVA), "changed_java_classes": changed_classes,
        "baseline_components": manifest["components"],
    })
    payload = [
        {"path": p.relative_to(INNER / "payload").as_posix(), "sha256": sha(p), "size": p.stat().st_size}
        for p in sorted((INNER / "payload").rglob("*")) if p.is_file()
    ]
    write(INNER / "payload-manifest.json", {"format": "sparkrobot-internal-payload-v1", "files": payload})
    sums(INNER, "SHA256SUMS")
    shutil.copytree(BASE_OUTER, OUTER, ignore=shutil.ignore_patterns("components", "audit", "SHA256SUMS.txt"))
    for name in ("install_all.sh", "control_all.sh"):
        copy(HERE / name, OUTER / name)
    for name in ("docs", "scripts"):
        copytree(HERE / name, OUTER / name)
    # The outer scripts are also used for preparing a fresh robot.
    (OUTER / "components").mkdir()
    for name in ("aimdk-aarch64-1bde262f-artifacts.7z", "sparkrobot-lingxi-kb-1.0.2-20260902.zip"):
        copy(BASE_OUTER / "components" / name, OUTER / "components" / name)
    manifest.update(
        version=VERSION, navigation_version=NAV_VERSION,
        baseline=manifest["version"], java_sha256=sha(JAVA),
        validation="见 audit/验证记录.md；本地回归及 Linux 隔离验证，尚未实机验收",
    )
    manifest["merged_increments"] += [
        "skipped-same-point-arrival-guard-20261019",
        "strict-control-ack-and-non-command-guard-20261019",
        "firmware-preflight-and-restore-validation-20261019",
        "exact-runtime-provenance-20261019",
    ]
    write(OUTER / "bundle-manifest.json", manifest)
    write(OUTER / "VERSION", "LINGXI_COMPLETE_VERSION=" + VERSION + "\n")
    write(OUTER / "README.md", (
        f"# 灵犀导航 V1.3.9 r4\n\n完整版本：`{VERSION}`；导航主包：`{NAV_VERSION}`。\n\n"
        "部署入口：`docs/V1.3.9-完整部署说明.md`。修复和验证范围：`docs/V1.3.9-r4-修复与验收.md`。\n\n"
        "保留 Java/AgentSDK 语音、知识库、循环讲解与机器人本机网页架构。\n"
        "本次完成本地源码、异常回执、Linux 隔离安装和归档验证；实机验收状态见 `audit/验证记录.md`。\n"
    ))
    print("Staged:", INNER, OUTER, flush=True)


def finalize():
    sanitize_stage()
    provenance = json.loads((INNER / "source-provenance.json").read_text(encoding="utf-8"))
    if source_hashes() != provenance["source_files"] or sha(JAVA) != provenance["java_sha256"]:
        raise RuntimeError("Sources or compiled Java changed after staging")
    for target in (INNER / "docs", OUTER / "docs"):
        copytree(HERE / "docs", target)
    copy(HERE / "docs/V1.3.9-完整部署说明.md", INNER / "docs/内部部署与命令手册.md")
    archive(INNER, DELIVERY / "components", "SHA256SUMS")
    copy(DELIVERY / "components" / (NAV_NAME + ".tar.gz"), OUTER / "components" / (NAV_NAME + ".tar.gz"))
    manifest = json.loads((OUTER / "bundle-manifest.json").read_text(encoding="utf-8"))
    manifest["components"] = {p.name: sha(p) for p in sorted((OUTER / "components").iterdir())}
    write(OUTER / "bundle-manifest.json", manifest)
    for name in ("验证记录.md", "validation-results.json"):
        copy(DELIVERY / name, OUTER / "audit" / name)
    copy(DELIVERY / "reports/packaged/results.json", OUTER / "audit/packaged-results.json")
    copy(DELIVERY / "reports/java-results.json", OUTER / "audit/java-results.json")
    copy(INNER / "source-provenance.json", OUTER / "audit/source-provenance.json")
    for name in ("report.json", "after-320.png", "after-390.png", "after-1440.png"):
        copy(DELIVERY / "reports/visual" / name, OUTER / "audit/visual" / name)
    archive(OUTER, DELIVERY, "SHA256SUMS.txt")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("stage", "finalize"))
    args = parser.parse_args()
    stage() if args.mode == "stage" else finalize()
