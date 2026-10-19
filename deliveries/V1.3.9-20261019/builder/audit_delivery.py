"""Audit complete archive membership, hashes, executable modes and source identity."""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile
import build_delivery as build


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def tar_files(path):
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        require(bool(members), "Empty TAR")
        top = PurePosixPath(members[0].name).parts[0]
        files = {}
        seen = set()
        for item in members:
            name = PurePosixPath(item.name)
            require(not name.is_absolute() and ".." not in name.parts and name.parts[0] == top, "Unsafe TAR path")
            require(not any(part in build.GENERATED for part in name.parts) and not name.name.endswith(".pyc"), "Generated cache in TAR")
            require(item.name not in seen, "Duplicate TAR member")
            seen.add(item.name)
            require(item.isfile() or item.isdir(), "TAR contains a link or special file")
            if item.isfile():
                relative = name.relative_to(top).as_posix()
                files[relative] = archive.extractfile(item).read()
                if build.executable(item.name):
                    require(item.mode & 0o111, "Missing executable permission: " + item.name)
    return files


def sums(files, name):
    listed = {}
    require(b"\r" not in files[name], "CRLF checksums")
    for line in files[name].decode("utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        require(relative not in listed and relative in files, "Duplicate or absent checksum entry")
        require(digest(files[relative]) == expected, "Hash mismatch: " + relative)
        listed[relative] = expected
    require(set(listed) == set(files) - {name}, "Checksum manifest is incomplete")


def audit_inner(files):
    sums(files, "SHA256SUMS")
    info = json.loads(files["release-info.json"])
    require(info["version"] == build.NAV_VERSION, "Wrong inner version")
    payload = json.loads(files["payload-manifest.json"])["files"]
    require(len(payload) == len({e["path"] for e in payload}), "Duplicate payload entries")
    require({e["path"] for e in payload} == {n[8:] for n in files if n.startswith("payload/")}, "Incomplete payload")
    for entry in payload:
        data = files["payload/" + entry["path"]]
        require(digest(data) == entry["sha256"] and len(data) == entry["size"], "Invalid payload: " + entry["path"])
    for relative, expected in build.source_hashes().items():
        require(digest(files["source/" + relative]) == expected, "Wrong source: " + relative)
    for p in (build.SOURCE / "navigation/robot_local_navigation_v3").glob("*.py"):
        require(files["payload/navigation/robot_local_navigation_v3/" + p.name] == p.read_bytes().replace(b"\r\n", b"\n"), "Navigation differs from tested source")
    for p in (build.SOURCE / "server/dist").rglob("*"):
        if p.is_file():
            require(files["payload/console/dist/" + p.relative_to(build.SOURCE / "server/dist").as_posix()] == p.read_bytes(), "Stale console output")
    require(digest(files["payload/java/robot-local-navigation-java-all.jar"]) == build.sha(build.JAVA) == info["java_sha256"], "Java mismatch")
    for name in ("payload/runtime/node", "payload/runtime/java/bin/java"):
        elf = files[name][:20]
        require(elf[:4] == b"\x7fELF" and int.from_bytes(elf[18:20], "little") == 183, "Not ARM64: " + name)
    for name, data in files.items():
        require(not name.endswith((".pem", ".key")) and Path(name).name not in {"secrets.env", ".env", "console.env", "runtime.env"}, "Sensitive file: " + name)
        if "/node_modules/" not in name and not name.startswith("payload/runtime/") and Path(name).suffix in build.TEXT:
            require(b"\r\n" not in data, "CRLF text: " + name)
            require(not re.search(rb"sk-[A-Za-z0-9_-]{20,}|AKLT[A-Za-z0-9_-]{12,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", data), "Secret pattern: " + name)
    print(f"Inner: {len(files)} files; complete manifests, source/runtime identity, ARM64 and secret checks PASS")


def compare_zip(path, expected, checksum):
    with zipfile.ZipFile(path) as archive:
        require(archive.testzip() is None, "ZIP corruption")
        require(len(archive.namelist()) == len(set(archive.namelist())), "Duplicate ZIP names")
        files = {}
        for item in archive.infolist():
            name = PurePosixPath(item.filename)
            require(not name.is_absolute() and ".." not in name.parts, "Unsafe ZIP path")
            require(name.parts[0] == path.name.removesuffix(".zip"), "Wrong ZIP root")
            require(stat_is_regular(item.external_attr >> 16), "Non-regular ZIP member")
            if build.executable(item.filename):
                require((item.external_attr >> 16) & 0o111, "ZIP missing executable permission")
            files[name.relative_to(name.parts[0]).as_posix()] = archive.read(item)
        sums(files, checksum)
        require({n: digest(data) for n, data in files.items()} == expected, "ZIP/TAR differ")


def stat_is_regular(mode):
    import stat
    return stat.S_ISREG(mode)


def audit():
    tar_path = build.DELIVERY / (build.NAME + ".tar.gz")
    files = tar_files(tar_path)
    sums(files, "SHA256SUMS.txt")
    manifest = json.loads(files["bundle-manifest.json"])
    require(manifest["version"] == build.VERSION and manifest["navigation_version"] == build.NAV_VERSION, "Wrong bundle versions")
    for name in ("install_all.sh", "control_all.sh"):
        require(build.VERSION.encode() in files[name] and build.NAV_VERSION.encode() in files[name], "Stale installer version")
    expected_components = {
        "aimdk-aarch64-1bde262f-artifacts.7z": "f13147a175bc3fb621fb118698aae0353c097adda38bba640f9a39c01433cc98",
        "sparkrobot-lingxi-kb-1.0.2-20260902.zip": "b8f73d925cc914b0440bc6478424f7759f9472cc9c61d8ce06050c37ddf9cf30",
    }
    for name, expected in manifest["components"].items():
        require(digest(files["components/" + name]) == expected, "Component hash mismatch")
        if name in expected_components:
            require(expected == expected_components[name], "Third-party baseline changed")
    require(set(manifest["components"]) == set(expected_components) | {build.NAV_NAME + ".tar.gz"}, "Wrong component set")
    with tarfile.open(fileobj=io.BytesIO(files["components/" + build.NAV_NAME + ".tar.gz"]), mode="r:gz") as archive:
        # Audit all member types using the same path reader as standalone archives.
        require(all(m.isfile() or m.isdir() for m in archive.getmembers()), "Nested link or special file")
    inner_tar = build.DELIVERY / "components" / (build.NAV_NAME + ".tar.gz")
    require(files["components/" + build.NAV_NAME + ".tar.gz"] == inner_tar.read_bytes(), "Nested component differs")
    inner_files = tar_files(inner_tar)
    audit_inner(inner_files)
    compare_zip(build.DELIVERY / "components" / (build.NAV_NAME + ".zip"),
                {n: digest(data) for n, data in inner_files.items()}, "SHA256SUMS")
    compare_zip(build.DELIVERY / (build.NAME + ".zip"), {n: digest(data) for n, data in files.items()}, "SHA256SUMS.txt")
    stage_files = {p.relative_to(build.OUTER).as_posix(): build.sha(p) for p in build.OUTER.rglob("*") if p.is_file()}
    require(stage_files == {n: digest(data) for n, data in files.items()}, "Outer archive differs from staging")
    for path in (tar_path, build.DELIVERY / (build.NAME + ".zip"), inner_tar,
                 build.DELIVERY / "components" / (build.NAV_NAME + ".zip")):
        require(path.with_name(path.name + ".sha256").read_text().split()[0] == build.sha(path), "Sidecar mismatch")
    paths = (tar_path, build.DELIVERY / (build.NAME + ".zip"), inner_tar,
             build.DELIVERY / "components" / (build.NAV_NAME + ".zip"))
    build.write(build.DELIVERY / "archive-audit.json", {
        "passed": True, "complete_version": build.VERSION, "navigation_version": build.NAV_VERSION,
        "inner_files": len(inner_files), "outer_files": len(files),
        "checks": ["complete-checksums", "payload-size-and-hash", "source-and-built-runtime-identity",
                   "tar-zip-equivalence", "executable-modes", "arm64-elf", "sensitive-file-and-pattern-scan",
                   "third-party-pinned-hashes", "outer-stage-identity", "sidecar-hashes", "no-python-cache"],
        "archives": [{"path": p.relative_to(build.DELIVERY).as_posix(), "sha256": build.sha(p),
                      "bytes": p.stat().st_size} for p in paths],
    })
    print(f"Outer: {len(files)} files; both inner/outer TAR and ZIP, all hashes and contents PASS")


if __name__ == "__main__":
    audit()
