"""Verify the signed APK and compare its packaged web assets with the build."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument("apk", type=Path)
parser.add_argument("--report", type=Path)
parser.add_argument("--java", type=Path)
parser.add_argument("--build-tools", type=Path, default=Path(".local/android-sdk/build-tools/35.0.0"))
args = parser.parse_args()
apk = args.apk.resolve(strict=True)
java = args.java or next(Path(".local/jdk21").glob("*/bin/java.exe"))
signer = args.build_tools / "lib/apksigner.jar"
aapt = args.build_tools / "aapt2.exe"


def run(*command):
    return subprocess.run([str(item) for item in command], check=True, text=True,
                          encoding="utf-8", capture_output=True).stdout


digest = hashlib.file_digest(apk.open("rb"), "sha256").hexdigest()
assert digest == Path(str(apk) + ".sha256").read_text().split()[0], "APK checksum mismatch"
signature = run(java, "-jar", signer, "verify", "--verbose", "--print-certs", apk)
assert re.search(r"Verified using v[23] scheme.*: true", signature), "Modern APK signature required"
manifest = run(aapt, "dump", "xmltree", "--file", "AndroidManifest.xml", apk)
assert "com.agibot.lingxinext" in manifest, "Unexpected application ID"
for attribute in ("allowBackup", "usesCleartextTraffic"):
    assert re.search(rf":{attribute}\([^)]*\)=false\b", manifest), attribute
assert re.search(r":screenOrientation\([^)]*\)=6\b", manifest), "Landscape lock missing"
assert not re.search(r":debuggable\([^)]*\)=true\b", manifest), "Debug APK"
resources = run(aapt, "dump", "resources", apk)
network_path = re.search(r"xml/network_security_config\s+\(\) \(file\) (\S+) type=XML", resources)
assert network_path, "Network policy resource missing"
network = run(aapt, "dump", "xmltree", "--file", network_path.group(1), apk)
assert 'src="user"' in network and 'src="system"' in network, "Local CA trust missing"
assert re.search(r"cleartextTrafficPermitted=false\b", network), "Cleartext permitted"
checked = []
with zipfile.ZipFile(apk) as archive:
    names = archive.namelist()
    assert len(names) == len(set(names)), "Duplicate APK entries"
    assert not any(re.search(r"(^|/)\.env$|\.(jks|keystore|key|pem)$", name) for name in names)
    config = json.loads(archive.read("assets/capacitor.config.json"))
    assert not config.get("server", {}).get("url"), "Development server in APK"
    for item in sorted(Path("dist").rglob("*")):
        if item.is_file():
            relative = item.relative_to("dist").as_posix()
            assert archive.read("assets/public/" + relative) == item.read_bytes(), relative
            checked.append(relative)
    packaged = {name.removeprefix("assets/public/") for name in names
                if name.startswith("assets/public/") and not name.endswith("/")}
    # Capacitor adds its own bridge scripts during cap sync.
    assert packaged - set(checked) <= {"cordova.js", "cordova_plugins.js"}, "Unexpected web assets"
report = {"apk": apk.name, "sha256": digest, "signature": "verified",
          "orientation": "sensorLandscape", "cleartextTraffic": False, "allowBackup": False,
          "webAssets": checked, "deviceAcceptance": False}
text = json.dumps(report, indent=2)
if args.report:
    args.report.write_text(text + "\n", encoding="utf-8")
print(text)
