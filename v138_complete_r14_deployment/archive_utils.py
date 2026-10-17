"""Strict archive I/O shared by the r14 builder and delivery verifier."""
import copy
import gzip
import hashlib
import io
import json
from pathlib import PurePosixPath
import tarfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_data(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def read_archive(data, prefix):
    result = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for entry in archive:
            path = PurePosixPath(entry.name)
            if path.is_absolute() or ".." in path.parts or "\\" in entry.name:
                raise RuntimeError("Unsafe archive path: " + entry.name)
            if entry.isdir():
                continue
            if not entry.isfile() or not entry.name.startswith(prefix + "/"):
                raise RuntimeError("Unexpected archive entry: " + entry.name)
            name = entry.name[len(prefix) + 1:]
            if name in result:
                raise RuntimeError("Duplicate archive entry: " + name)
            result[name] = (copy.copy(entry), archive.extractfile(entry).read())
    return result


def normalize_text(name, data):
    if PurePosixPath(name).suffix in {".sh", ".py", ".java", ".ts", ".js", ".json", ".md", ".txt", ".env", ".properties", ".service", ".html", ".css"} or PurePosixPath(name).name in {"VERSION", "SHA256SUMS", "SHA256SUMS.txt"}:
        return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data


def put(files, name, data, mode=None):
    entry = copy.copy(files[name][0]) if name in files else tarfile.TarInfo(name)
    entry.mode = mode if mode is not None else (entry.mode if name in files else (0o755 if name.endswith((".sh", ".py")) else 0o644))
    entry.size = len(data)
    files[name] = (entry, data)


def write_sums(files, filename):
    put(files, filename, "".join(f"{digest(data)}  {name}\n" for name, (_, data) in sorted(files.items()) if name != filename).encode("utf-8"), 0o644)


def verify_sums(files, filename):
    raw = files[filename][1]
    assert b"\r" not in raw
    covered = set()
    for line in raw.decode("utf-8").splitlines():
        expected, name = line.split("  ", 1)
        assert name not in covered and name in files and digest(files[name][1]) == expected, name
        covered.add(name)
    assert covered == set(files) - {filename}


def tar_data(files, prefix):
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0, compresslevel=6) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, (original, data) in sorted(files.items()):
                entry = copy.copy(original)
                entry.name = prefix + "/" + name
                entry.uid = entry.gid = entry.mtime = 0
                entry.uname = entry.gname = "root"
                entry.pax_headers = {}
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
    return output.getvalue()


def update_payload_manifest(nav):
    manifest = json.loads(nav["payload-manifest.json"][1])
    manifest["files"] = [{"path": n[len("payload/"):], "sha256": digest(data), "size": len(data)}
                         for n, (_, data) in sorted(nav.items()) if n.startswith("payload/")]
    put(nav, "payload-manifest.json", json_data(manifest))
