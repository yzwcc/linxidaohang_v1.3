"""Verify immutable reports against Git history and expose current-source drift.

No target execution, robot connection, report rewrite or deployment occurs.
Historical integrity is distinct from current source/ARM64 acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs/evidence/historical-source-bindings.json"
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def canonical_sha256(report):
    return sha256(json.dumps(report, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True, allow_nan=False).encode("utf-8"))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _load(data):
    return json.loads(data, object_pairs_hook=_unique)


def _path(name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or any(ord(c) < 32 for c in name)):
        raise ValueError("relative_evidence_path_required")
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name or not path.parts or ".." in path.parts:
        raise ValueError("relative_evidence_path_required")
    return path


def _ordinary_file(root, name):
    path = root / _path(name)
    if (not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)
                                if p != root and root in p.parents)):
        raise ValueError("ordinary_evidence_file_required:" + name)
    return path


def _git(root, *args):
    return subprocess.run(["git", "--no-replace-objects", *args], cwd=root,
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=20).stdout


def catalog(path=CATALOG):
    value = _load(Path(path).read_bytes())
    if (not isinstance(value, dict) or type(value.get("schemaVersion")) is not int
            or value["schemaVersion"] != 1
            or value.get("purpose") != "immutable_historical_evidence_not_current_target_acceptance"
            or not isinstance(value.get("entries"), dict) or not value["entries"]):
        raise ValueError("invalid_historical_catalog")
    for name, entry in value["entries"].items():
        _path(name)
        if (not isinstance(entry, dict)
                or set(entry) not in ({"revision", "reportCanonicalSha256", "sourceByteTransforms"},
                                     {"revision", "reportCanonicalSha256", "sourceByteTransforms", "sourceSection"})
                or entry.get("sourceSection", "sourceSha256") not in (
                    "sourceSha256", "actualRuntimeSourceSha256", "inputIntegrity.sourcesSha256")
                or not isinstance(entry["revision"], str) or not REVISION.fullmatch(entry["revision"])
                or not isinstance(entry["reportCanonicalSha256"], str)
                or not DIGEST.fullmatch(entry["reportCanonicalSha256"])
                or not isinstance(entry["sourceByteTransforms"], dict)):
            raise ValueError("invalid_historical_binding")
        for source, transform in entry["sourceByteTransforms"].items():
            _path(source)
            if transform != "lf_to_crlf":
                raise ValueError("unsupported_historical_byte_transform")
    return value["entries"]


def verify(name, *, root=ROOT, catalog_path=CATALOG):
    """Return historical provenance and exact current drift, never a new result."""
    root = Path(root).resolve()
    entry = catalog(catalog_path)[name]
    report = _load(_ordinary_file(root, name).read_bytes())
    if not isinstance(report, dict):
        raise ValueError("historical_report_object_required")
    if canonical_sha256(report) != entry["reportCanonicalSha256"]:
        raise ValueError("historical_report_changed:" + name)
    prefix = _git(root, "rev-parse", "--show-prefix").decode("utf-8").strip()
    revision = entry["revision"]
    original = _load(_git(root, "cat-file", "blob", revision + ":" + prefix + name))
    if canonical_sha256(original) != entry["reportCanonicalSha256"]:
        raise ValueError("historical_report_revision_mismatch:" + name)
    section = entry.get("sourceSection", "sourceSha256")
    if section == "inputIntegrity.sourcesSha256":
        integrity = report.get("inputIntegrity")
        sources = integrity.get("sourcesSha256") if isinstance(integrity, dict) else None
    else:
        sources = report.get(section)
    if (not isinstance(sources, dict) or not sources
            or not set(entry["sourceByteTransforms"]).issubset(sources)):
        raise ValueError("historical_sources_required")
    drift = {}
    for source, expected in sorted(sources.items()):
        _path(source)
        if not isinstance(expected, str) or not DIGEST.fullmatch(expected):
            raise ValueError("invalid_historical_source_digest")
        frozen = _git(root, "cat-file", "blob", revision + ":" + prefix + source)
        if source in entry["sourceByteTransforms"]:
            if b"\r" in frozen:
                raise ValueError("historical_crlf_requires_lf_git_blob")
            frozen = frozen.replace(b"\n", b"\r\n")
        if sha256(frozen) != expected:
            raise ValueError("historical_source_mismatch:" + source)
        try:
            actual = sha256(_ordinary_file(root, source).read_bytes())
        except ValueError:
            actual = None
        if actual != expected:
            drift[source] = {"recordedSha256": expected, "currentSha256": actual}
    return {"evidencePath": name, "revision": revision,
            "reportCanonicalSha256": entry["reportCanonicalSha256"],
            "historicalSourcesVerified": True, "recordedSourceCount": len(sources),
            "currentSourceBindingsMatch": not drift, "currentSourceDrift": drift}


def audit(*, root=ROOT, catalog_path=CATALOG):
    reports = [verify(name, root=root, catalog_path=catalog_path) for name in catalog(catalog_path)]
    matches = all(report["currentSourceBindingsMatch"] for report in reports)
    return {"schemaVersion": 1,
            "result": ("historical_integrity_verified_current_source_bindings_match" if matches
                       else "historical_integrity_verified_current_sources_drifted"),
            "currentSourceBindingsMatch": matches, "reports": reports,
            "boundaries": {"robotContacted": False, "targetChecksRerun": False,
                           "currentArm64Accepted": False, "navigationAccepted": False,
                           "deploymentApproved": False}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-current", action="store_true",
                        help="exit 2 if any current source differs; matching is not target acceptance")
    args = parser.parse_args(argv)
    result = audit()
    print(json.dumps(result, indent=2, allow_nan=False))
    return 2 if args.require_current and not result["currentSourceBindingsMatch"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
