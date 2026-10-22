#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
TARGET = HERE / "target"
BASE_JAR = TARGET / "robot-local-navigation-java-0.1.0-all-kb-20260902.jar"
OUTPUT_JAR = TARGET / "robot-local-navigation-java-0.1.0-all-kb-voice-latency-20261023.jar"
SDK_JAR = HERE / "linksoul-agentsdk-1.4.0-SNAPSHOT.jar"
MAIN_SOURCE = HERE / "src" / "main" / "java" / "com" / "agibot" / "aiem" / "nav"
TEST_SOURCE = HERE / "src" / "test" / "java" / "com" / "agibot" / "aiem" / "nav"
MAIN_FILES = (
    "AppConfig.java",
    "LingxiNavAgentApp.java",
    "LocalNavigationCommand.java",
    "VoiceGate.java",
    "WakeSessionGate.java",
    "SpeechInputPolicy.java",
    "Soc2WakeEvidenceProvider.java",
    "VoiceMetadataDiagnostics.java",
    "NavigationIntent.java",
    "PresetMotionCatalog.java",
    "NavBridgeClient.java",
    "DoubaoArkClient.java",
    "RetrieverClient.java",
    "KnowledgeAnswerPolicy.java",
)
TEST_FILES = (
    "VoiceGateTest.java",
    "WakeSessionGateTest.java",
    "Soc2WakeEvidenceProviderTest.java",
    "SpeechInputPolicyTest.java",
    "InteractionGateRegression.java",
    "PresetMotionCatalogTest.java",
    "KnowledgeAnswerPolicyTest.java",
    "NavBridgeClientTest.java",
    "RetrieverClientTest.java",
    "LingxiChatFlowTest.java",
    "LocalNavigationCommandTest.java",
)
TEST_MAINS = (
    "VoiceGateTest",
    "WakeSessionGateTest",
    "Soc2WakeEvidenceProviderTest",
    "SpeechInputPolicyTest",
    "InteractionGateRegression",
    "PresetMotionCatalogTest",
    "KnowledgeAnswerPolicyTest",
    "NavBridgeClientTest",
    "RetrieverClientTest",
    "LingxiChatFlowTest",
    "LocalNavigationCommandTest",
)
REQUIRED_CLASSES = {
    "com/agibot/aiem/nav/AppConfig.class",
    "com/agibot/aiem/nav/LingxiNavAgentApp.class",
    "com/agibot/aiem/nav/LocalNavigationCommand.class",
    "com/agibot/aiem/nav/VoiceGate.class",
    "com/agibot/aiem/nav/WakeSessionGate.class",
    "com/agibot/aiem/nav/SpeechInputPolicy.class",
    "com/agibot/aiem/nav/Soc2WakeEvidenceProvider.class",
    "com/agibot/aiem/nav/VoiceMetadataDiagnostics.class",
    "com/agibot/aiem/nav/NavigationIntent.class",
    "com/agibot/aiem/nav/PresetMotionCatalog.class",
    "com/agibot/aiem/nav/PresetMotionCatalog$Motion.class",
    "com/agibot/aiem/nav/PresetMotionCatalog$Selection.class",
    "com/agibot/aiem/nav/NavBridgeClient.class",
    "com/agibot/aiem/nav/DoubaoArkClient.class",
    "com/agibot/aiem/nav/RetrieverClient.class",
    "com/agibot/aiem/nav/KnowledgeAnswerPolicy.class",
}
MANIFEST = "META-INF/MANIFEST.MF"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def executable(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"JDK tool not found in PATH: {name}")
    return path


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=HERE, check=True)


def main() -> int:
    for path in (BASE_JAR, SDK_JAR):
        if not path.is_file():
            raise RuntimeError(f"Missing pinned Java build input: {path}")
    for name in MAIN_FILES:
        if not (MAIN_SOURCE / name).is_file():
            raise RuntimeError(f"Missing Java source: {MAIN_SOURCE / name}")
    for name in TEST_FILES:
        if not (TEST_SOURCE / name).is_file():
            raise RuntimeError(f"Missing regression source: {TEST_SOURCE / name}")

    javac = executable("javac")
    java = executable("java")
    jar = executable("jar")
    production_classpath = os.pathsep.join((str(BASE_JAR), str(SDK_JAR)))

    with tempfile.TemporaryDirectory(prefix=".voice-gate-build-", dir=TARGET) as temp:
        build = Path(temp)
        classes = build / "classes"
        tests = build / "tests"
        classes.mkdir()
        tests.mkdir()
        run([
            javac, "-encoding", "UTF-8", "-classpath", production_classpath,
            "-d", str(classes), *(str(MAIN_SOURCE / name) for name in MAIN_FILES),
        ])
        test_classpath = os.pathsep.join(
            (str(classes), str(BASE_JAR), str(SDK_JAR))
        )
        run([
            javac, "-encoding", "UTF-8", "-classpath", test_classpath,
            "-d", str(tests), *(str(TEST_SOURCE / name) for name in TEST_FILES),
        ])
        runtime_classpath = os.pathsep.join(
            (str(tests), str(classes), str(BASE_JAR), str(SDK_JAR))
        )
        for name in TEST_MAINS:
            run([java, "-classpath", runtime_classpath, f"com.agibot.aiem.nav.{name}"])

        with zipfile.ZipFile(BASE_JAR) as archive:
            if MANIFEST not in archive.namelist():
                raise RuntimeError("Pinned base JAR has no manifest")
            original_manifest = archive.read(MANIFEST)
        shutil.copy2(BASE_JAR, OUTPUT_JAR)
        run([jar, "uf", str(OUTPUT_JAR), "-C", str(classes), "."])

    with zipfile.ZipFile(OUTPUT_JAR) as archive:
        names = set(archive.namelist())
        missing = sorted(REQUIRED_CLASSES - names)
        if missing:
            raise RuntimeError(f"Patched Agent JAR is missing classes: {missing}")
        if any("/tests/" in name or name.endswith("Test.class") for name in names):
            raise RuntimeError("Regression classes leaked into the runtime Agent JAR")
        if archive.read(MANIFEST) != original_manifest:
            raise RuntimeError("Patched Agent JAR changed its entry-point manifest")

    print(f"KB Java Agent: {OUTPUT_JAR}")
    print(f"SHA-256: {digest(OUTPUT_JAR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
