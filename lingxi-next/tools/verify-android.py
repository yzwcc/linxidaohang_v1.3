"""Verify a source-bound signed commissioning APK without contacting a robot."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(*command, cwd=None):
    return subprocess.run([str(item) for item in command], check=True, text=True,
                          encoding="utf-8", capture_output=True, cwd=cwd).stdout


def check_manifest(manifest, product):
    require(re.search(r'\bpackage="com\.agibot\.lingxinext"', manifest), "Unexpected application ID")
    name = re.search(r':versionName\([^)]*\)="([^"\r\n]+)"', manifest)
    code = re.search(r':versionCode\([^)]*\)=(0x[0-9a-fA-F]+|[0-9]+)\b', manifest)
    require(name and name.group(1) == product["version"], "APK versionName differs from product")
    require(code and int(code.group(1), 0 if code.group(1).startswith("0x") else 10)
            == product["androidVersionCode"], "APK versionCode differs from product")
    for attribute in ("allowBackup", "usesCleartextTraffic"):
        require(re.search(rf':{attribute}\([^)]*\)=false\b', manifest), attribute)
    require(re.search(r':screenOrientation\([^)]*\)=6\b', manifest), "Landscape lock missing")
    require(not re.search(r':debuggable\([^)]*\)=true\b', manifest), "Debug APK")


def check_build_record(record, current):
    require(record.get("schema") == 1 and record.get("classification") == "commissioning"
            and record.get("productionAccepted") is False, "Invalid commissioning build record")
    for key in ("webIdentity", "webAssets", "nativeSource"):
        require(record.get(key) == current[key], f"Build record does not match current {key}")


def current_inputs(root, node):
    script = """
      import { assertCurrentWebBuild, assertSyncedWebAssets, androidBuildIdentity }
        from './tools/build-identity.mjs';
      console.log(JSON.stringify({webIdentity: await assertCurrentWebBuild('.'),
        webAssets: await assertSyncedWebAssets('.'), nativeSource: await androidBuildIdentity('.')}));
    """
    return json.loads(run(node, "--input-type=module", "-e", script, cwd=root))


def verify(args):
    root = Path(__file__).resolve().parent.parent
    apk = args.apk.resolve(strict=True)
    java = args.java or next((root / ".local/jdk21").glob("*/bin/java.exe"))
    build_tools = args.build_tools or root / ".local/android-sdk/build-tools/35.0.0"
    signer = build_tools / "lib/apksigner.jar"
    aapt = build_tools / "aapt2.exe"
    current = current_inputs(root, args.node)
    record = json.loads(Path(str(apk) + ".build.json").read_text(encoding="utf-8"))
    check_build_record(record, current)
    product = current["webIdentity"]["product"]
    with apk.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    require(digest == Path(str(apk) + ".sha256").read_text().split()[0], "APK checksum mismatch")
    signature = run(java, "-jar", signer, "verify", "--verbose", "--print-certs", apk)
    require(re.search(r"Verified using v[23] scheme.*: true", signature), "Modern APK signature required")
    certificate = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]+)", signature)
    require(certificate, "APK signing certificate digest missing")
    manifest = run(aapt, "dump", "xmltree", "--file", "AndroidManifest.xml", apk)
    check_manifest(manifest, product)
    resources = run(aapt, "dump", "resources", apk)
    network_path = re.search(r"xml/network_security_config\s+\(\) \(file\) (\S+) type=XML", resources)
    require(network_path, "Network policy resource missing")
    network = run(aapt, "dump", "xmltree", "--file", network_path.group(1), apk)
    require('src="user"' in network and 'src="system"' in network, "Local CA trust missing")
    require(re.search(r"cleartextTrafficPermitted=false\b", network), "Cleartext permitted")
    checked = []
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate APK entries")
        require(not any(re.search(r"(^|/)\.env$|\.(jks|keystore|key|pem)$", name) for name in names),
                "Private credential file in APK")
        config = json.loads(archive.read("assets/capacitor.config.json"))
        require(not config.get("server", {}).get("url"), "Development server in APK")
        for item in current["webAssets"]["files"]:
            relative = item["path"].removeprefix("dist/")
            data = archive.read("assets/public/" + relative)
            require(len(data) == item["bytes"] and hashlib.sha256(data).hexdigest() == item["sha256"],
                    f"APK web asset mismatch: {relative}")
            checked.append(relative)
        packaged = {name.removeprefix("assets/public/") for name in names
                    if name.startswith("assets/public/") and not name.endswith("/")}
        require(packaged - set(checked) <= {"cordova.js", "cordova_plugins.js"}, "Unexpected web assets")
        require(json.loads(archive.read("assets/public/lingxi-build.json")) == current["webIdentity"],
                "APK embedded source identity mismatch")
    # Recheck after inspecting the APK so concurrent edits cannot yield a current-source report.
    check_build_record(record, current_inputs(root, args.node))
    return {"apk": apk.name, "sha256": digest, "signature": "verified",
            "certificateSha256": certificate.group(1).lower(), "product": product,
            "webSourceSha256": current["webIdentity"]["source"]["sha256"],
            "nativeSourceSha256": current["nativeSource"]["sha256"],
            "orientation": "sensorLandscape", "cleartextTraffic": False, "allowBackup": False,
            "webAssets": checked, "classification": "commissioning",
            "productionAccepted": False, "deviceAcceptance": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("apk", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--java", type=Path)
    parser.add_argument("--node", default="node")
    parser.add_argument("--build-tools", type=Path)
    args = parser.parse_args()
    text = json.dumps(verify(args), indent=2)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
