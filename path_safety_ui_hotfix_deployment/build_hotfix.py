from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
NAME = "lingxi-v1.3.1-r16-path-safety-ui-hotfix-20261024"
BASE_ARCHIVE = ROOT / "lingxi_v1.3.1_deployment/dist/lingxi-v1.3.1-full-deployment-20261023-r16.zip"
BASE_BUNDLE = "lingxi-v1.3.1-full-deployment-20261023-r16"
BASE_NAV = "sparkrobot-lingxi-nav-internal-1.0.8-20261023-r16"
NAV_ARCHIVE = f"{BASE_BUNDLE}/packages/{BASE_NAV}.tar.gz"
OUTPUT_DIR = HERE / "dist"

BASELINE = {
    "console/dist/app.js": "27f249eb03e35a1a9dfa22cb5f1daed259c045d3d3296b5abbd6a9fe1b777185",
    "console/dist/robotClient.js": "a4b41a685205f345b90d8a8fce288b3ad158d3d0b9d1304fe3f9ef218bd20e79",
    "console/dist/public/app.js": "55d7154d8877784edbc901a2cbb8d0a84a030eec4edfab3d831999eed45f9f9a",
    "console/dist/public/index.html": "a689217542a93e14d210ded880d08601e5463bf9825c6c73a108866ff73298d0",
    "console/dist/public/styles.css": "3dad5f4344538fbb14b3e54ef60bcdc5acf8eee7fa690a4548060597ff04938a",
}

CHANGED_PATHS = tuple(BASELINE)


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_manifest(root: Path) -> None:
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "SHA256SUMS.txt"):
        entries.append(f"{digest_file(path)}  {path.relative_to(root).as_posix()}")
    (root / "SHA256SUMS.txt").write_text("\n".join(entries) + "\n", encoding="ascii", newline="\n")


def read_baseline() -> dict[str, bytes]:
    if not BASE_ARCHIVE.is_file():
        raise RuntimeError(f"R16 基线完整包不存在：{BASE_ARCHIVE}")
    checksum_file = BASE_ARCHIVE.with_suffix(BASE_ARCHIVE.suffix + ".sha256")
    if not checksum_file.is_file():
        raise RuntimeError(f"R16 基线完整包缺少 SHA-256 sidecar：{checksum_file}")
    expected_archive_hash = checksum_file.read_text(encoding="ascii").split()[0].lower()
    if digest_file(BASE_ARCHIVE) != expected_archive_hash:
        raise RuntimeError("R16 基线完整包 SHA-256 不匹配")
    with zipfile.ZipFile(BASE_ARCHIVE) as bundle:
        nav_bytes = bundle.read(NAV_ARCHIVE)
    with tarfile.open(fileobj=io.BytesIO(nav_bytes), mode="r:gz") as nav:
        prefix = f"{BASE_NAV}/payload/"
        result: dict[str, bytes] = {}
        for relative, expected in BASELINE.items():
            member = nav.extractfile(prefix + relative)
            if member is None:
                raise RuntimeError(f"R16 基线缺少文件：{relative}")
            value = member.read()
            actual = digest_bytes(value)
            if actual != expected:
                raise RuntimeError(f"R16 基线不匹配 {relative}: {actual}")
            result[relative] = value
    return result


def patch_once(source: bytes, marker: bytes, insertion: bytes, label: str) -> bytes:
    if source.count(marker) != 1:
        raise RuntimeError(f"R16 {label} 插入锚点不存在或不唯一")
    return source.replace(marker, insertion + marker, 1)


def make_runtime_files(baseline: dict[str, bytes]) -> dict[str, bytes]:
    files = {
        relative: (ROOT / "server/dist" / PurePosixPath(relative).relative_to("console/dist")).read_bytes()
        for relative in CHANGED_PATHS
        if relative.startswith("console/dist/public/")
    }
    app_path = "console/dist/app.js"
    app = baseline[app_path]
    app_marker = b'    app.get("/api/robots/default/relocalization/status"'
    app_routes = br'''    app.get("/api/robots/default/navigation/path-safety", async () => {
        const settings = await getRobot().pathSafety();
        return { ok: true, ...settings };
    });
    app.post("/api/robots/default/navigation/path-safety", async (request, reply) => {
        const parsed = z.object({
            robot_radius_m: z.number().finite().gt(0).max(2),
            extra_clearance_m: z.number().finite().min(0).max(2)
        }).strict().safeParse(request.body ?? {});
        if (!parsed.success) {
            return reply.code(400).send({
                ok: false,
                message: "\u673a\u5668\u4eba\u5b9e\u6d4b\u5916\u63a5\u534a\u5f84\u987b\u5927\u4e8e 0 \u4e14\u4e0d\u8d85\u8fc7 2 \u7c73\uff0c\u989d\u5916\u969c\u788d\u51c0\u7a7a\u987b\u5728 0 \u5230 2 \u7c73\u4e4b\u95f4"
            });
        }
        const settings = await getRobot().savePathSafety(parsed.data);
        return { ok: true, ...settings };
    });
'''
    files[app_path] = patch_once(app, app_marker, app_routes, "console app.js")

    client_path = "console/dist/robotClient.js"
    client_marker = b"        relocalizationStatus() {"
    client_methods = b'''        pathSafety() {
            return requestJson(`${webBase}/api/settings`, { timeoutMs: 5000 });
        },
        savePathSafety(pathSafety) {
            return mutation(() => requestJson(`${webBase}/api/settings`, {
                method: "POST",
                body: JSON.stringify({ path_safety: pathSafety }),
                timeoutMs: 5000
            }));
        },
'''
    files[client_path] = patch_once(baseline[client_path], client_marker, client_methods, "robotClient.js")
    return files


def stage_payload(payload: Path, baseline: dict[str, bytes]) -> None:
    runtime_files = make_runtime_files(baseline)
    for relative in CHANGED_PATHS:
        destination = payload / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative in runtime_files:
            destination.write_bytes(runtime_files[relative])
        else:
            source = ROOT / "server/dist" / PurePosixPath(relative).relative_to("console/dist")
            if not source.is_file():
                raise RuntimeError(f"缺少已构建控制台文件：{source}")
            shutil.copy2(source, destination)

        staged = (payload / relative).read_bytes()
        if digest_bytes(staged) == digest_bytes(baseline[relative]):
            raise RuntimeError(f"预期更新文件未发生修改：{relative}")

    index = (payload / "console/dist/public/index.html").read_text(encoding="utf-8")
    app = (payload / "console/dist/public/app.js").read_text(encoding="utf-8")
    server_app = (payload / "console/dist/app.js").read_text(encoding="utf-8")
    robot_client = (payload / "console/dist/robotClient.js").read_text(encoding="utf-8")
    for marker, text in (
        ('id="pointTaskType"', index),
        ('id="robotRadius"', index),
        ('id="extraClearance"', index),
        ('id="pointStart"', index),
        ("savePathSafety", app),
        ("/api/robots/default/navigation/path-safety", server_app),
        ("savePathSafety(pathSafety)", robot_client),
        ("/api/settings", robot_client),
        ("R16 \u4e0d\u63d0\u4f9b\u6865\u63a5\u5e94\u7528\u56de\u6267", app),
    ):
        if marker not in text:
            raise RuntimeError(f"热修运行文件缺少功能标记：{marker}")
    if not (
        index.index('id="pointTaskType"')
        < index.index('id="robotRadius"')
        < index.index('id="pointStart"')
    ):
        raise RuntimeError("全局安全字段必须紧邻 task_type 并位于点位起点设置之前")
    if "sendActionResult" in server_app or "accepted !== false" in server_app:
        raise RuntimeError("热修后端混入 R16 基线之外的语音操作拒绝行为")


def audit_package(stage: Path, baseline: dict[str, bytes]) -> None:
    required = {
        "install.sh",
        "README.md",
        "test_install.sh",
        "update_payload_manifest.py",
        "payload/SHA256SUMS.txt",
        "SHA256SUMS.txt",
        *(f"test-fixtures/{relative}" for relative in CHANGED_PATHS),
        *(f"payload/{relative}" for relative in CHANGED_PATHS),
    }
    actual = {path.relative_to(stage).as_posix() for path in stage.rglob("*") if path.is_file()}
    if actual != required:
        raise RuntimeError(f"热修文件清单错误：缺少={sorted(required-actual)} 多余={sorted(actual-required)}")
    for relative in CHANGED_PATHS:
        if digest_file(stage / "payload" / relative) == digest_bytes(baseline[relative]):
            raise RuntimeError(f"热修未更新目标文件：{relative}")


def main() -> None:
    baseline = read_baseline()
    output = OUTPUT_DIR / f"{NAME}.tar.gz"
    sidecar = output.with_suffix(output.suffix + ".sha256")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lingxi-path-safety-ui-", dir=OUTPUT_DIR) as temporary:
        stage = Path(temporary) / NAME
        payload = stage / "payload"
        payload.mkdir(parents=True)
        stage_payload(payload, baseline)
        for relative, value in baseline.items():
            fixture = stage / "test-fixtures" / relative
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_bytes(value)
        for name in ("install.sh", "README.md", "test_install.sh", "update_payload_manifest.py"):
            shutil.copy2(HERE / name, stage / name)
        (stage / "install.sh").chmod(0o755)
        (stage / "test_install.sh").chmod(0o755)
        (stage / "update_payload_manifest.py").chmod(0o755)
        write_manifest(payload)
        write_manifest(stage)
        audit_package(stage, baseline)

        temporary_archive = Path(temporary) / output.name
        with tarfile.open(temporary_archive, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(stage.rglob("*")):
                relative = PurePosixPath(NAME) / PurePosixPath(path.relative_to(stage).as_posix())
                info = archive.gettarinfo(str(path), arcname=relative.as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                if info.isdir():
                    info.mode = 0o755
                elif info.isfile():
                    info.mode = 0o755 if path.name in {"install.sh", "test_install.sh", "update_payload_manifest.py"} else 0o644
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)
                    continue
                archive.addfile(info)
        if output.exists() or sidecar.exists():
            raise RuntimeError(f"拒绝覆盖已存在交付物：{output}")
        shutil.copy2(temporary_archive, output)
    with tarfile.open(output, "r:gz") as archive:
        members = archive.getmembers()
        files = [member for member in members if member.isfile()]
        if not files:
            raise RuntimeError("生成的热修 TAR.GZ 为空")
        for member in members:
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RuntimeError(f"热修 TAR.GZ 包含不安全路径：{member.name}")
        root = f"{NAME}/"
        if any(not member.name.startswith(root) for member in members):
            raise RuntimeError("热修 TAR.GZ 成员不在单一顶层目录内")
        payload_files = {
            member.name.removeprefix(f"{NAME}/payload/")
            for member in files
            if member.name.startswith(f"{NAME}/payload/") and member.name != f"{NAME}/payload/SHA256SUMS.txt"
        }
        if payload_files != set(CHANGED_PATHS):
            raise RuntimeError(f"热修 TAR.GZ 运行文件范围错误：{sorted(payload_files)}")
    sidecar.write_text(f"{digest_file(output)}  {output.name}\n", encoding="ascii", newline="\n")
    print(f"TAR.GZ: {output}")
    print(f"SHA-256: {digest_file(output)}")
    print(f"Bytes: {output.stat().st_size}")


if __name__ == "__main__":
    main()
