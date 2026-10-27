"""Assemble Linux runtimes directly from pinned archives, preserving POSIX links."""
import copy
import hashlib
import io
import pathlib
import posixpath
import re
import sys
import tarfile

stage = pathlib.Path(sys.argv[1]).resolve(strict=True)
output = pathlib.Path(sys.argv[2]).resolve()
if stage.parent.name != "artifacts" or not stage.name.startswith("lingxi-next-"):
    raise SystemExit("Release stage must be inside artifacts")
checksums = {}
names = set()


class HashingReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size):
        data = self.stream.read(size)
        self.digest.update(data)
        return data


def validate_name(name):
    parts = pathlib.PurePosixPath(name).parts
    if name.startswith("/") or ".." in parts or "\\" in name:
        raise ValueError(f"Unsafe archive path: {name}")
    relative = name.removeprefix(stage.name + "/")
    if re.search(r"(^|/)(credentials|var|\.local|\.git)(/|$)|\.(jks|keystore|key|pem)$", relative):
        raise ValueError(f"Forbidden release content: {relative}")
    if relative.endswith(".env"):
        raise ValueError(f"Forbidden release content: {relative}")
    if name in names:
        raise ValueError(f"Duplicate archive member: {name}")
    names.add(name)


def permissions(info):
    relative = info.name.removeprefix(stage.name + "/")
    validate_name(info.name)
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    if info.isdir():
        info.mode = 0o755
    elif info.isfile():
        info.mode = 0o755 if relative.endswith(".sh") or info.mode & 0o111 else 0o644
    elif info.issym() or info.islnk():
        target = pathlib.PurePosixPath(info.linkname)
        resolved = posixpath.normpath(info.linkname if info.islnk() else
                                     posixpath.join(posixpath.dirname(info.name), info.linkname))
        if target.is_absolute() or not resolved.startswith(stage.name + "/"):
            raise ValueError("Symlink outside release not permitted")
    else:
        raise ValueError("Special files are not permitted in releases")
    return info


with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT) as archive:
    # Windows cannot reliably extract Linux symlinks. Never extract these runtimes
    # on the build workstation; stream the original entries into the final tar.
    for item in sorted(stage.rglob("*")):
        relative = item.relative_to(stage).as_posix()
        if relative == "SHA256SUMS":
            continue
        if relative == "runtime" or relative.startswith("runtime/"):
            raise ValueError("Staging must not contain partially extracted runtimes")
        if item.is_file():
            with item.open("rb") as stream:
                checksums[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
        archive.add(item, arcname=f"{stage.name}/{relative}", recursive=False, filter=permissions)
    for prefix, filename in zip(("runtime/node", "runtime/java"), sys.argv[3:5]):
        with tarfile.open(filename, "r|gz") as runtime:
            runtime_root = None
            for original in runtime:
                parts = pathlib.PurePosixPath(original.name).parts
                if not parts or original.name.startswith("/") or ".." in parts:
                    raise ValueError("Unsafe runtime member")
                runtime_root = runtime_root or parts[0]
                if parts[0] != runtime_root:
                    raise ValueError("Runtime must have one root directory")
                relative = "/".join(parts[1:])
                member = copy.copy(original)
                member.name = f"{stage.name}/{prefix}" + (f"/{relative}" if relative else "")
                if member.islnk():
                    link_parts = pathlib.PurePosixPath(member.linkname).parts
                    if not link_parts or link_parts[0] != runtime_root or ".." in link_parts:
                        raise ValueError("Unsafe runtime hardlink")
                    member.linkname = f"{stage.name}/{prefix}/" + "/".join(link_parts[1:])
                permissions(member)
                if member.issym():
                    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.linkname))
                    if not resolved.startswith(f"{stage.name}/{prefix}/"):
                        raise ValueError("Runtime link must stay inside its runtime")
                if member.isfile():
                    with runtime.extractfile(original) as stream:
                        reader = HashingReader(stream)
                        archive.addfile(member, reader)
                        checksums[f"{prefix}/{relative}"] = reader.digest.hexdigest()
                else:
                    archive.addfile(member)
    manifest = "".join(f"{checksum}  {name}\n" for name, checksum in sorted(checksums.items())).encode()
    (stage / "SHA256SUMS").write_bytes(manifest)
    info = tarfile.TarInfo(f"{stage.name}/SHA256SUMS")
    info.size = len(manifest)
    archive.addfile(permissions(info), io.BytesIO(manifest))
print(output.name)
