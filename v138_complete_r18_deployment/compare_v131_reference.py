"""Compare the supplied V1.3.1 effective JAR with the immutable r18 delivery.

Only local HTTP/SDK test peers are used. No installer or robot command is run.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REFERENCE_ZIP_SHA = "3466f019863770100e13b8290122d7f5593a21480cb32e17f7ff3bddd896e393"
REFERENCE_JAR_SHA = "7f1836d23443086090c33f94fc30d98727fb22de797be552fd7ac74d834f37a3"
DELIVERY_SHA = "8ab670f6cbea1175a56984cbc8bcd230eb9ade40f9b1309debeae91512081aa4"
RUNTIME_SHA = "3140d03c2d81623ef2efb362400699c9e9dae7d4c09409a7431fff42bdb62372"
CONTRACT_COMMIT = "749af1b7a1a8267346f839261900a77ac88d3038"
CONTRACT_PATH = ("v138_complete_r15_deployment/candidate/source/java/src/test/java/"
                 "com/agibot/aiem/nav/ReferenceControlChannelTest.java")
CONTRACT_SHA = "252278d44bc951abf4de54d6dd6fcfa75f0c608bef368773482bbf596bddab82"
TESTS = (
    "ReferenceControlChannelTest", "ControlReplyTextWireTest",
    "SilentStartControlTest", "NavigationTourRoutingTest",
    "TtsAcknowledgementTest", "ControlOwnershipFaultTest",
    "ModelRecoveryTest", "FieldVoiceReplayTest",
)
NATIVE_KEYS = ("skills.json", "start_fixed_line_intro", "exhibit_commentary",
               "disable_native", "native_tour")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def execute(args, *, expected=0):
    result = subprocess.run(list(map(str, args)), cwd=ROOT, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=150)
    text = result.stdout + result.stderr
    if expected is not None:
        assert result.returncode == expected, text[-12000:]
    return result.returncode, text


def members(data, is_zip):
    if is_zip:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.infolist():
                if not entry.is_dir():
                    yield entry.filename, archive.read(entry)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for entry in archive:
                if entry.isfile():
                    yield entry.name, archive.extractfile(entry).read()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def inspect_reference(path, work):
    assert sha(path.read_bytes()) == REFERENCE_ZIP_SHA
    scripts, jar, sdk = {}, None, None
    native_hits, scanned = [], 0
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.endswith("-r7-deployment.tar.gz")]
        assert len(names) == 1
        with archive.open(names[0]) as stream, tarfile.open(fileobj=stream, mode="r|gz") as outer:
            for entry in outer:
                if not entry.isfile():
                    continue
                if entry.name.endswith("/install_all.sh"):
                    scripts["install_all.sh"] = outer.extractfile(entry).read()
                if ("/components/" not in entry.name
                        or not entry.name.endswith((".zip", ".tar.gz"))):
                    continue
                data = outer.extractfile(entry).read()
                for name, body in members(data, entry.name.endswith(".zip")):
                    if name.endswith("agent/robot-local-navigation-java-all-kb.jar"):
                        assert jar is None
                        jar = body
                    if ("nav-internal" in entry.name
                            and name.endswith("/payload/java/linksoul-agentsdk-1.4.0.jar")):
                        assert sdk is None
                        sdk = body
                    if name.endswith("install_voice_agent.sh") and "kb-" in entry.name:
                        scripts["install_voice_agent.sh"] = body
                    if name.endswith((".sh", ".py", ".json", ".properties", ".template", ".md")):
                        scanned += 1
                        hits = [key for key in NATIVE_KEYS if key.encode() in body]
                        if hits:
                            native_hits.append({"component": entry.name, "path": name, "keys": hits})
    assert jar is not None and sdk is not None
    assert sha(jar) == REFERENCE_JAR_SHA
    install = scripts["install_all.sh"].decode("utf-8")
    assert install.index('"${KB_ROOT}/install_voice_agent.sh"') > install.index(
        '"${KB_ROOT}/install.sh"')
    voice = scripts["install_voice_agent.sh"].decode("utf-8")
    assert "SOURCE=" in voice and "agent/robot-local-navigation-java-all-kb.jar" in voice
    assert "TARGET=/opt/sparkrobot/lingxi-nav/current/java/robot-local-navigation-java-all.jar" in voice
    assert 'install -o run -g run -m 0440 "${INPUT}" "${TMP}"' in voice
    assert 'mv -f -- "${TMP}" "${TARGET}"' in voice
    return (write(work / "reference.jar", jar), write(work / "reference-sdk.jar", sdk),
            {"zip_sha256": REFERENCE_ZIP_SHA, "effective_jar_sha256": sha(jar),
             "effective_jar_source": "KB addon install_voice_agent.sh",
             "direct_component_text_files_scanned": scanned,
             "native_mapping_references": native_hits,
             "installer_sha256": {name: sha(data) for name, data in scripts.items()}})


def inspect_delivery(path, work):
    raw = path.read_bytes()
    assert sha(raw) == DELIVERY_SHA
    component = None
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for entry in archive:
            if entry.isfile() and entry.name.endswith(
                    "/components/sparkrobot-lingxi-nav-internal-1.0.19-20261019-r18.tar.gz"):
                assert component is None
                component = archive.extractfile(entry).read()
    assert component is not None
    files = {}
    for name, body in members(component, False):
        relative = name.split("/", 1)[1]
        if (relative.startswith("source/java/src/") and relative.endswith(".java")
                or relative in {"payload/java/robot-local-navigation-java-all.jar",
                                "payload/java/linksoul-agentsdk-1.4.0.jar"}):
            target = work / "delivery" / relative
            assert target.resolve().is_relative_to((work / "delivery").resolve())
            files[relative] = write(target, body)
    jar = files["payload/java/robot-local-navigation-java-all.jar"]
    sdk = files["payload/java/linksoul-agentsdk-1.4.0.jar"]
    assert sha(jar.read_bytes()) == RUNTIME_SHA
    return jar, sdk, work / "delivery/source/java/src"


def run_trace(java, classes, jar, sdk, output, label):
    code, text = execute([java / "java.exe", "-Dfile.encoding=UTF-8", "-cp",
                          os.pathsep.join(map(str, (classes, jar, sdk))),
                          "com.agibot.aiem.nav.V131ControlTrace"], expected=None)
    write(output / (label + ".log"), text.encode("utf-8"))
    traces = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("TRACE ")]
    assert len(traces) == 24, text[-4000:]
    return code, text, traces


def normalized_trace(value):
    result = dict(value)
    frames = [json.loads(item) for item in value["sdkMessages"]]
    for frame in frames:
        if re.fullmatch(r"item_[0-9a-f]{32}", frame.get("itemId", "")):
            frame["itemId"] = "generated-item-id"
    result["sdkMessages"] = frames
    return result


def audit(args):
    work = args.output.resolve()
    work.mkdir(parents=True, exist_ok=True)
    java = args.java.resolve() / "bin"
    reference, reference_sdk, evidence = inspect_reference(args.reference, work)
    jar, sdk, sources = inspect_delivery(args.delivery, work)
    assert reference_sdk.read_bytes() == sdk.read_bytes()
    print("Effective V1.3.1 KB JAR and r18 runtime verified", flush=True)

    code, original = execute(["git", "show", CONTRACT_COMMIT + ":" + CONTRACT_PATH])
    assert code == 0 and sha(original.encode("utf-8")) == CONTRACT_SHA
    # Add observation only: retain every assertion from the historical contract.
    observed = original.replace("ReferenceControlChannelTest", "V131ControlTrace")
    marker = '                    String label = mode + "/" + current.intent;'
    assert observed.count(marker) == 1
    observed = observed.replace(marker, '''                    System.out.println("TRACE " + JSON.toJSONString(Map.of(
                            "mode", mode, "intent", current.intent, "trace", new ArrayList<>(trace),
                            "sdkMessages", new ArrayList<>(client.messages), "ttsCount", speech.size())));
''' + marker)
    trace_source = write(work / "V131ControlTrace.java", observed.encode("utf-8"))
    classes = work / "reference-classes"
    classes.mkdir(exist_ok=True)
    execute([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp",
             os.pathsep.join(map(str, (reference, reference_sdk))), "-d", classes, trace_source])
    old_code, old_log, old_traces = run_trace(java, classes, reference, reference_sdk, work, "v131")
    new_code, new_log, new_traces = run_trace(java, classes, jar, sdk, work, "r18-historical-contract")
    assert old_code == 0 and "passed: 204 assertions" in old_log
    assert new_code != 0
    errors = re.findall(r"^(?:no SDK interrupt|single speech owner|one local speech request|"
                        r"SDK completion before local TTS) (?:asr2llm|asr2tts|audio2llm|audio2tts)"
                        r"/(?:navigate|start_task)$", new_log, re.M)
    failure = re.search(r"Reference control channel failures: (\d+)/204", new_log)
    assert failure is not None and int(failure.group(1)) == len(errors) == 24, new_log[-8000:]
    for old, new in zip(old_traces, new_traces):
        assert (old["mode"], old["intent"]) == (new["mode"], new["intent"])
        for value in (old, new):
            dispatch = [item for item in value["trace"] if item.startswith("dispatch:")]
            assert len(dispatch) == 1
            assert not any("error" in item or "skill" in item for item in value["trace"])
        if old["intent"] not in ("navigate", "start_task"):
            assert normalized_trace(old) == normalized_trace(new), (old, new)
        else:
            assert old["ttsCount"] == 1 and new["ttsCount"] == 0
            frames = [json.loads(item) for item in new["sdkMessages"]]
            text = [item["text"] for item in frames if item["type"] == "agentsdk.llm_response.item.delta"]
            assert text == []
            assert sum("interrupt" in item["type"] for item in frames) == 1
    print("Original contract: V1.3.1 204/204; r18 has 24 expected start-silencing differences", flush=True)

    test_classes = work / "delivery-tests"
    test_classes.mkdir(exist_ok=True)
    cp = os.pathsep.join(map(str, (jar, sdk)))
    execute([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp",
             cp, "-d", test_classes, *sorted((sources / "test/java").rglob("*.java"))])
    results = {}
    for name in TESTS:
        _, text = execute([java / "java.exe", "-Dfile.encoding=UTF-8", "-cp",
                           str(test_classes) + os.pathsep + cp, "com.agibot.aiem.nav." + name])
        write(work / (name + ".log"), text.encode("utf-8"))
        results[name] = [line for line in text.splitlines() if "passed" in line.lower()]
        assert results[name], name
        print(name + " passed", flush=True)
    report = {
        "status": "comparison_completed", "reference": evidence,
        "delivery_sha256": DELIVERY_SHA, "delivery_jar_sha256": RUNTIME_SHA,
        "sdk_identical": True, "historical_contract_source_commit": CONTRACT_COMMIT,
        "historical_contract_source_sha256": CONTRACT_SHA,
        "historical_assertions_v131": 204, "historical_contract_differences_r18": 24,
        "intentional_difference_counts": dict(Counter(error.rsplit(" ", 1)[0] for error in errors)),
        "unchanged_trace_scenarios": 16, "intentional_start_trace_changes": 8,
        "r18_regression": results, "runtime_modified": False,
        "physical_robot": "not_run", "native_speech_suppression": "field_acceptance_pending",
    }
    write(work / "comparison.json", (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--delivery", type=Path, default=HERE / "dist/lingxi-v1.3.8-complete-20261019-r18-deployment.tar.gz")
    parser.add_argument("--output", type=Path, default=HERE / "qa/v131-comparison")
    parser.add_argument("--java", type=Path, default=Path(r"C:\Program Files\Java\jdk-17"))
    audit(parser.parse_args())
