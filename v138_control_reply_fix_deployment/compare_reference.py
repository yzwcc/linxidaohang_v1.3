"""Run the same control wire contract against the supplied KB JAR and shipped JAR."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REFERENCE = ROOT / "tmp/v131-reply-reference/sparkrobot-lingxi-kb-1.0.2-20260902.zip"
SOURCE = (ROOT / "v138_complete_r15_deployment/candidate/source/java/src/test/java"
          "/com/agibot/aiem/nav/ReferenceControlChannelTest.java")
JAVA = Path(r"C:\Program Files\Java\jdk-17/bin")
SDK = ROOT / "v138_complete_r15_deployment/reference/java/linksoul-agentsdk-1.4.0.jar"
REFERENCE_SHA = "7f1836d23443086090c33f94fc30d98727fb22de797be552fd7ac74d834f37a3"


def run_contract(jar, classes):
    result = subprocess.run(
        [str(JAVA / "java.exe"), "-Dfile.encoding=UTF-8", "-cp",
         str(classes) + ";" + str(jar) + ";" + str(SDK),
         "com.agibot.aiem.nav.ReferenceControlChannelTest"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=90)
    return result


def main():
    reference = next(REFERENCE.rglob("robot-local-navigation-java-all-kb.jar"))
    assert hashlib.sha256(reference.read_bytes()).hexdigest() == REFERENCE_SHA
    previous = HERE / "work/previous-1.0.4.jar"
    import sys
    sys.path.insert(0, str(ROOT / "v138_complete_r15_deployment"))
    from archive_utils import read_archive
    name = "sparkrobot-lingxi-v1.3.8-control-reply-fix-1.0.4-20261018"
    previous.write_bytes(read_archive((HERE / "dist" / (name + ".tar.gz")).read_bytes(), name)[
        "payload/java/robot-local-navigation-java-all.jar"][1])
    with tempfile.TemporaryDirectory(prefix="reference-test-", dir=HERE / "work") as temp:
        classes = Path(temp)
        subprocess.run([str(JAVA / "javac.exe"), "-encoding", "UTF-8", "--release", "17",
                        "-cp", str(reference) + ";" + str(SDK), "-d", str(classes), str(SOURCE)], check=True)
        reports = {"contract_source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest()}
        for label, jar, expected in (("v131_effective_kb", reference, 0), ("previous_1_0_4", previous, 1)):
            result = run_contract(jar, classes)
            (HERE / "work" / (label + "-reference-contract.log")).write_text(
                result.stdout + result.stderr, encoding="utf-8")
            assert (result.returncode == 0) == (expected == 0), result.stdout + result.stderr
            headline = next((line for line in (result.stdout + result.stderr).splitlines()
                             if "assertions" in line or "Reference control channel failures:" in line), "")
            reports[label] = {"sha256": hashlib.sha256(jar.read_bytes()).hexdigest(),
                              "exit_code": result.returncode, "result": headline}
            print(label + ": " + headline)
        (HERE / "work/reference-comparison.json").write_text(
            json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
