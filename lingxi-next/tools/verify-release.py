"""Audit the actual ARM64 archive, without extracting or executing its content."""
import hashlib
import json
import argparse
from pathlib import Path, PurePosixPath
import posixpath
import re
import tarfile

parser = argparse.ArgumentParser()
parser.add_argument("archive", type=Path)
parser.add_argument("--report", type=Path)
args = parser.parse_args()
filename = args.archive.resolve(strict=True)
expected = Path(str(filename) + ".sha256").read_text().split()[0]
digest = hashlib.file_digest(filename.open("rb"), "sha256").hexdigest()
assert digest == expected, "Archive checksum mismatch"
with tarfile.open(filename, "r|gz") as archive:
    names = set()
    links = []
    files = {}
    hashes = {}
    headers = {}
    captured = {}
    root = None
    for entry in archive:
        root = root or entry.name.split("/")[0]
        assert entry.name not in names, "Duplicate archive member"
        names.add(entry.name)
        assert entry.name == root or entry.name.startswith(root + "/"), "Multiple archive roots"
        relative = entry.name.removeprefix(root + "/")
        parts = PurePosixPath(entry.name).parts
        assert not entry.name.startswith("/") and ".." not in parts, "Unsafe path"
        assert not re.search(r"(^|/)(credentials|var|\.local|\.git)(/|$)|\.(jks|keystore|key|pem)$", relative), relative
        assert not relative.endswith(".env"), relative
        if entry.issym() or entry.islnk():
            resolved = posixpath.normpath(entry.linkname if entry.islnk() else
                                         posixpath.join(posixpath.dirname(entry.name), entry.linkname))
            assert not entry.linkname.startswith("/") and resolved.startswith(root + "/"), "Escaping link"
            links.append((entry.name, resolved))
        if entry.isfile():
            files[relative] = entry
            # Read each regular file once. Random extraction from gzip for
            # alphabetically sorted checksums repeatedly inflates whole runtimes.
            stream = archive.extractfile(entry)
            header = stream.read(20)
            header_digest = hashlib.sha256(header)
            headers[relative] = header
            content = bytearray(header) if relative in ("SHA256SUMS", "release-status.json") else None
            while chunk := stream.read(1024 * 1024):
                header_digest.update(chunk)
                if content is not None:
                    content.extend(chunk)
            stream.close()
            hashes[relative] = header_digest.hexdigest()
            if content is not None:
                captured[relative] = bytes(content)
        else:
            assert entry.isdir() or entry.issym() or entry.islnk(), "Special file"
    for name, resolved in links:
        assert resolved in names, f"Broken link: {name}"
    checksum_lines = captured["SHA256SUMS"].decode().splitlines()
    checked = set()
    for line in checksum_lines:
        checksum, relative = line.split("  ", 1)
        assert re.fullmatch(r"[a-f0-9]{64}", checksum)
        assert relative in files and relative not in checked, relative
        assert hashes[relative] == checksum, relative
        checked.add(relative)
    assert checked == set(files) - {"SHA256SUMS"}, "Unhashed release files"
    for relative in ("runtime/node/bin/node", "runtime/java/bin/java", "runtime/java/lib/jspawnhelper"):
        entry = files[relative]
        header = headers[relative]
        assert header[:5] == b"\x7fELF\x02", relative
        assert int.from_bytes(header[18:20], "little") == 183, relative
        assert entry.mode & 0o111, f"Missing executable mode: {relative}"
    for relative, entry in files.items():
        if relative.endswith(".sh"):
            assert entry.mode & 0o111, relative
    sdk = "agent/lib/linksoul-agentsdk-1.5.0-SNAPSHOT.jar"
    assert hashes[sdk] == \
        "810c5e7a7d40191097dc5c4c37e3133407e63307fcb204d35caacb7768912c25"
    status = json.loads(captured["release-status.json"])
    assert status["classification"] == "commissioning" and status["productionAccepted"] is False
report = json.dumps({"archive": filename.name, "sha256": digest, "verifiedFiles": len(checked),
                     "architecture": "ELF64 AArch64", "executableModes": "verified",
                     "classification": "commissioning", "productionAccepted": False}, indent=2)
if args.report:
    args.report.write_text(report + "\n", encoding="utf-8")
print(report)
