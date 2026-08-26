#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


OWN_PREFIX = "com/agibot/aiem/nav/"
MAIN_CLASS = "com.agibot.aiem.nav.LingxiNavAgentApp"


def run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def quote_proguard(path: Path) -> str:
    return "'" + path.resolve().as_posix().replace("'", "\\'") + "'"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def merge_fat_jar(fat_jar: Path, obfuscated_jar: Path, output: Path, mapping: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    marker = {
        "format": "sparkrobot-java-protection-v1",
        "obfuscated": True,
        "tool": "ProGuard",
        "mapping_sha256": sha256(mapping),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    seen: set[str] = set()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        with zipfile.ZipFile(fat_jar) as source:
            for item in source.infolist():
                upper = item.filename.upper()
                if item.filename.startswith(OWN_PREFIX):
                    continue
                if upper.startswith("META-INF/") and upper.endswith((".SF", ".RSA", ".DSA")):
                    continue
                if item.filename == "META-INF/sparkrobot-protection.json" or item.filename in seen:
                    continue
                target.writestr(item, source.read(item.filename))
                seen.add(item.filename)
        with zipfile.ZipFile(obfuscated_jar) as source:
            for item in source.infolist():
                if item.is_dir() or item.filename.startswith("META-INF/") or item.filename in seen:
                    continue
                target.writestr(item, source.read(item.filename))
                seen.add(item.filename)
        target.writestr(
            "META-INF/sparkrobot-protection.json",
            json.dumps(marker, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
        )
    if "com/agibot/aiem/nav/LingxiNavAgentApp.class" not in seen:
        raise RuntimeError("混淆后 JAR 缺少固定启动入口")


def verify_class_loading(java: str, javac: str, output: Path, sdk_jar: Path, work: Path) -> None:
    source = work / "VerifyProtectedJar.java"
    source.write_text(
        "public final class VerifyProtectedJar {"
        " public static void main(String[] args) throws Exception {"
        f"  Class.forName(\"{MAIN_CLASS}\", false, VerifyProtectedJar.class.getClassLoader());"
        "  System.out.println(\"Java protected JAR class-load check passed\");"
        " }"
        "}\n",
        encoding="ascii",
    )
    run([javac, str(source)], cwd=work)
    run([java, "-Xverify:all", "-cp", os.pathsep.join([str(output), str(sdk_jar), str(work)]), "VerifyProtectedJar"], cwd=work)


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 ProGuard 保护灵犀导航 Java 自有实现")
    parser.add_argument("--app-jar", required=True, help="未打包依赖的自有代码 JAR")
    parser.add_argument("--fat-jar", required=True, help="包含运行依赖的原始 shaded JAR")
    parser.add_argument("--sdk-jar", required=True)
    parser.add_argument("--proguard-jar", required=True)
    parser.add_argument("--library-jar", action="append", default=[])
    parser.add_argument("--output", required=True)
    parser.add_argument("--mapping-output", help="厂商离线保存的映射文件")
    parser.add_argument("--java", default=shutil.which("java") or "java")
    parser.add_argument("--javac", default=shutil.which("javac") or "javac")
    args = parser.parse_args()

    app_jar = Path(args.app_jar).resolve()
    fat_jar = Path(args.fat_jar).resolve()
    sdk_jar = Path(args.sdk_jar).resolve()
    proguard_jar = Path(args.proguard_jar).resolve()
    output = Path(args.output).resolve()
    for path in (app_jar, fat_jar, sdk_jar, proguard_jar):
        if not path.is_file():
            raise RuntimeError(f"输入文件不存在：{path}")

    java_home = Path(os.environ.get("JAVA_HOME") or Path(args.java).resolve().parent.parent)
    java_base = java_home / "jmods" / "java.base.jmod"
    if not java_base.is_file():
        raise RuntimeError(f"找不到 Java 17 模块：{java_base}")

    with tempfile.TemporaryDirectory(prefix="sparkrobot-proguard-") as temp_name:
        work = Path(temp_name)
        obfuscated = work / "obfuscated.jar"
        mapping = work / "mapping.txt"
        config = work / "proguard.pro"
        lines = [
            f"-injars {quote_proguard(app_jar)}",
            f"-outjars {quote_proguard(obfuscated)}",
            f"-libraryjars {quote_proguard(java_base)}(!**.jar;!module-info.class)",
            f"-libraryjars {quote_proguard(sdk_jar)}",
            f"-libraryjars {quote_proguard(fat_jar)}(!{OWN_PREFIX}**)",
        ]
        lines.extend(f"-libraryjars {quote_proguard(Path(item))}" for item in args.library_jar)
        lines.extend([
            "-dontshrink",
            "-dontoptimize",
            "-dontnote",
            "-dontwarn",
            "-keepattributes Exceptions,InnerClasses,EnclosingMethod,Signature,*Annotation*",
            "-adaptclassstrings",
            f"-keepnames class {MAIN_CLASS}",
            f"-keep public class {MAIN_CLASS} {{ public static void main(java.lang.String[]); }}",
            f"-printmapping {quote_proguard(mapping)}",
        ])
        config.write_text("\n".join(lines) + "\n", encoding="utf-8")
        run([args.java, "-jar", str(proguard_jar), f"@{config}"])
        merge_fat_jar(fat_jar, obfuscated, output, mapping)
        verify_class_loading(args.java, args.javac, output, sdk_jar, work)
        if args.mapping_output:
            mapping_output = Path(args.mapping_output).resolve()
            mapping_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(mapping, mapping_output)
            try:
                mapping_output.chmod(0o600)
            except OSError:
                pass
            print(f"厂商映射文件：{mapping_output}")
    print(f"受保护 Java JAR：{output}")
    print(f"SHA-256：{sha256(output)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f"Java JAR 保护失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
