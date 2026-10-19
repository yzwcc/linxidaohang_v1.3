"""Inspect only voice implementation artifacts from a user-provided V1.3.1 ZIP."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import zipfile


def inspect(archive, output):
    output.mkdir(parents=True, exist_ok=True)
    records = []

    def capture(component, name, data):
        if not (name.endswith(".java") and "com/agibot/aiem/nav/" in name
                or name.endswith((".properties.template", ".properties.example"))
                or Path(name).name in {
                    "robot-local-navigation-java-all.jar",
                    "robot-local-navigation-java-all-kb.jar",
                    "linksoul-agentsdk-1.4.0.jar",
                    "lingxi_java_agent_control.sh",
                    "install_voice_agent.sh",
                }):
            return
        member_path = PurePosixPath(name)
        if (member_path.is_absolute() or ".." in member_path.parts
                or "\\" in name or ":" in name or member_path.as_posix() != name):
            raise ValueError("Unsafe reference member: " + name)
        relative = Path(Path(component).name) / name
        target = output / relative
        if not target.resolve().is_relative_to(output.resolve()):
            raise ValueError("Reference member escapes inspection directory: " + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        records.append({
            "component": component, "path": name, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "local_path": str(target),
        })

    with zipfile.ZipFile(archive) as bundle:
        package = next(n for n in bundle.namelist() if n.endswith("-r7-deployment.tar.gz"))
        with bundle.open(package) as stream, tarfile.open(fileobj=stream, mode="r|gz") as outer:
            for member in outer:
                if not member.isfile() or not member.name.endswith((".tar.gz", ".zip")):
                    continue
                if not any(key in member.name for key in ("nav-internal", "kb", "full-deployment")):
                    continue
                component = outer.extractfile(member).read()
                if member.name.endswith(".zip"):
                    with zipfile.ZipFile(io.BytesIO(component)) as nested:
                        for entry in nested.infolist():
                            if not entry.is_dir() and (
                                    entry.filename.endswith((".java", ".jar", ".properties.example",
                                                             ".properties.template", "lingxi_java_agent_control.sh",
                                                             "install_voice_agent.sh"))):
                                capture(member.name, entry.filename, nested.read(entry))
                    continue
                with tarfile.open(fileobj=io.BytesIO(component), mode="r:gz") as nested:
                    for entry in nested:
                        if not entry.isfile():
                            continue
                        name = entry.name
                        if name.endswith((".java", ".jar", ".properties.example",
                                          ".properties.template", "lingxi_java_agent_control.sh")):
                            capture(member.name, name, nested.extractfile(entry).read())
    (output / "inspection.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    inspect(args.archive, args.output)
