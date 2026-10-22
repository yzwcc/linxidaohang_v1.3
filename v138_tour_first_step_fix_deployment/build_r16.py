"""Apply the reviewed tour scheduling changes to the immutable r16 runtime."""
from __future__ import annotations

import ast
import copy
import io
import json
from pathlib import Path
import tarfile

from build import BRIDGE, HERE, digest, normalized, prepare_reference, write_archive

NAME = "sparkrobot-lingxi-v1.3.8-tour-first-step-fix-r16-1.0.0-20261022"
RELEASE = "1.0.19-20261019-r16"
BASELINE = HERE.parent / "v138_complete_r16_deployment/dist/lingxi-v1.3.8-complete-20261019-r16-deployment.tar.gz"
BASELINE_SHA = "1f17375157f06334ac95ec5836020136005dc178aba4be9de573080b30857069"
REFERENCE = HERE / "reference/r16"
CHANGED = {
    "__init__", "start_task", "handle_navigation_event", "_load_tour_task_points",
    "_start_task_step", "_queue_task_navigation_retry", "_on_task_navigation_retry",
    "_on_task_failure_advance", "_clear_task_locked",
    "_is_task_navigation_event_locked",
}
ADDED = (
    "_make_task_navigation_listener",
    "_is_current_task_step_locked",
    "_is_current_task_navigation_callback_locked",
)
SILENT_ASSIGNMENT = '            self._task_silent_receipts = bool(event_id) and payload.get("silent_control_reply") is True\n'


def bridge_class(tree):
    return next(node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == "LingxiNavBridge")


def method_nodes(source):
    return {node.name: node for node in bridge_class(ast.parse(source)).body
            if isinstance(node, ast.FunctionDef)}


def method_source(source, node):
    return "".join(source.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])


def without_changed_methods(source):
    tree = copy.deepcopy(ast.parse(source))
    cls = bridge_class(tree)
    cls.body = [node for node in cls.body
                if not isinstance(node, ast.FunctionDef) or node.name not in CHANGED | set(ADDED)]
    return ast.dump(tree, include_attributes=False)


def adapt_bridge(r16, original_r20, patched_r20):
    old_nodes, patch_nodes, target_nodes = (
        method_nodes(source) for source in (original_r20, patched_r20, r16)
    )
    actual_changed = {
        name for name in old_nodes
        if ast.dump(old_nodes[name], include_attributes=False)
        != ast.dump(patch_nodes[name], include_attributes=False)
    }
    if actual_changed != CHANGED or set(patch_nodes) - set(old_nodes) != set(ADDED):
        raise RuntimeError("reviewed r20 patch scope changed; r16 adaptation requires review")
    if any(name in target_nodes for name in ADDED):
        raise RuntimeError("r16 already defines a new scheduling helper")
    lines = r16.splitlines(keepends=True)
    edits = []
    for name in CHANGED:
        old = method_source(original_r20, old_nodes[name])
        new = method_source(patched_r20, patch_nodes[name])
        if name == "start_task":
            if old.count(SILENT_ASSIGNMENT) != 1 or new.count(SILENT_ASSIGNMENT) != 1:
                raise RuntimeError("r20 start_task voice assignment differs from reviewed baseline")
            old = old.replace(SILENT_ASSIGNMENT, "")
            new = new.replace(SILENT_ASSIGNMENT, "")
        target = target_nodes[name]
        if old != method_source(r16, target):
            raise RuntimeError("r16 task method requires a different adaptation: " + name)
        if name == "_queue_task_navigation_retry":
            helpers = "\n".join(method_source(patched_r20, patch_nodes[added]) for added in ADDED)
            new = helpers + "\n" + new
        edits.append((target.lineno - 1, target.end_lineno, new))
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = replacement.splitlines(keepends=True)
    candidate = "".join(lines)
    compile(candidate, BRIDGE, "exec")
    if without_changed_methods(r16) != without_changed_methods(candidate):
        raise RuntimeError("r16 adaptation modified unrelated runtime behavior")
    return candidate.encode()


def prepare_r16():
    if digest(BASELINE.read_bytes()) != BASELINE_SHA:
        raise RuntimeError("r16 archive differs from verified baseline")
    with tarfile.open(BASELINE, "r:gz") as outer:
        prefix = "lingxi-v1.3.8-complete-20261019-r16-deployment/"
        data = outer.extractfile(prefix + f"components/sparkrobot-lingxi-nav-internal-{RELEASE}.tar.gz").read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as inner:
        prefix = f"sparkrobot-lingxi-nav-internal-{RELEASE}/"
        for member in inner.getmembers():
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            name = member.name[len(prefix):]
            if name.startswith("payload/navigation/"):
                relative = "navigation/" + name[len("payload/navigation/"):]
            elif name.startswith("source/navigation/test/"):
                relative = "navigation/test/" + name[len("source/navigation/test/"):]
            elif name in {"scripts/navigation_control.sh", "payload-manifest.json"}:
                relative = "runtime/" + name
            else:
                continue
            path = REFERENCE / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(inner.extractfile(member).read())
    manifest = json.loads((REFERENCE / "runtime/payload-manifest.json").read_bytes())
    entries = {entry["path"]: entry for entry in manifest["files"]}
    for name in (BRIDGE, "navigation/robot_local_navigation_v3/navigation_client.py"):
        content = (REFERENCE / name).read_bytes()
        if digest(content) != entries[name]["sha256"] or len(content) != entries[name]["size"]:
            raise RuntimeError("r16 runtime differs from manifest: " + name)
    target = REFERENCE / "runtime" / BRIDGE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((REFERENCE / BRIDGE).read_bytes())
    prepare_reference()
    candidate = adapt_bridge(
        (REFERENCE / BRIDGE).read_text(encoding="utf-8"),
        (HERE / "reference/runtime" / BRIDGE).read_text(encoding="utf-8"),
        (HERE / "source" / BRIDGE).read_text(encoding="utf-8"),
    )
    path = REFERENCE / "patched" / BRIDGE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(candidate)
    return manifest, candidate


def build():
    manifest, candidate = prepare_r16()
    entries = {entry["path"]: entry for entry in manifest["files"]}
    meta = {
        "version": "1.0.0-20261022",
        "name": NAME,
        "allowed_releases": [RELEASE],
        "baseline_archive_sha256": BASELINE_SHA,
        "baseline_bridge_sha256": entries[BRIDGE]["sha256"],
        "patched_bridge_sha256": digest(candidate),
        "client_sha256": entries["navigation/robot_local_navigation_v3/navigation_client.py"]["sha256"],
        "controller_sha256": digest((REFERENCE / "runtime/scripts/navigation_control.sh").read_bytes()),
    }
    files = {name: normalized(HERE / name) for name in ("install.py", "install.sh")}
    files.update({
        "README.md": normalized(HERE / "README-r16.md"),
        "docs/部署说明.md": normalized(HERE / "docs/部署说明-r16.md"),
        "payload/" + BRIDGE: candidate,
        "release.json": (json.dumps(meta, indent=2) + "\n").encode(),
    })
    files["SHA256SUMS"] = "".join(
        f"{digest(content)}  {name}\n" for name, content in sorted(files.items())
    ).encode()
    write_archive(files, NAME, "部署说明-r16.md")


if __name__ == "__main__":
    build()
