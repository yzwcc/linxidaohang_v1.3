"""Package unchanged 1.0.8 runtime plus the native firmware mapping repair."""
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
spec = importlib.util.spec_from_file_location("immutable_silent_builder", ROOT / "v138_silent_control_fix_deployment/build.py")
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
BASE, PREFIX, PY_FILES = prior.BASE, prior.PREFIX, prior.PY_FILES
command, digest, read_archive = prior.command, prior.digest, prior.read_archive
put, normalize_text, write_sums = prior.put, prior.normalize_text, prior.write_sums
verify_sums, tar_data, json_data = prior.verify_sums, prior.tar_data, prior.json_data
NAME = "sparkrobot-lingxi-v1.3.8-native-tour-quiet-1.0.10-20261019"
DOCS = ("部署说明.md", "修复与验证说明.md")
JAVA_TESTS = prior.JAVA_TESTS
VOICE_BRIDGE_CONTROL_SHA256 = (
    "94786521c4227900cd5b8f1c6a9e77c684c16f52e09c20b61e5758712edd1d9c"
)


def main():
    raw = (ROOT / "v138_silent_control_fix_deployment/dist" / (prior.NAME + ".tar.gz")).read_bytes()
    assert digest(raw) == "6a7b596ee572ddf4741635a50f21e8564585f697beae3f9990654a01e36fb07e"
    old = read_archive(raw, prior.NAME)
    verify_sums(old, "SHA256SUMS")
    old_meta = json.loads(old["release.json"][1])
    failed_path = ROOT / "v138_tour_start_reply_fix_deployment/dist/sparkrobot-lingxi-v1.3.8-tour-start-reply-fix-1.0.9-20261019.tar.gz"
    failed_raw = failed_path.read_bytes()
    assert digest(failed_raw) == "0f986edb06e3cfd2bfce8809893f85a41684ce9114a01fd0f7d2ef50dcef138e"
    failed = read_archive(failed_raw, "sparkrobot-lingxi-v1.3.8-tour-start-reply-fix-1.0.9-20261019")
    failed_meta = json.loads(failed["release.json"][1])
    meta = {**old_meta, "package": NAME, "version": "1.0.10-20261019",
        "superseded_jar_sha256": list(dict.fromkeys([
            *old_meta["superseded_jar_sha256"], old_meta["patched_jar_sha256"],
            failed_meta["patched_jar_sha256"]])),
        "cumulative_through": "silent-control-fix-1.0.8-20261019",
        "runtime_identical_to_1_0_8": True,
        "legacy_native_tour_mapping_repair": True,
        "voice_bridge_control_sha256": VOICE_BRIDGE_CONTROL_SHA256,
        "native_mapping_requires_field_acceptance": True,
        "hardware_validation": False,
        "independent_native_speech_interception_verified": False,
        "field_feedback": "1.0.8 navigation accepted; tour failure speech remained; 1.0.9 rolled back"}
    files = {}
    for name, item in old.items():
        if name not in {"SHA256SUMS", "release.json", "install.py", "install.sh", *DOCS}:
            put(files, name, item[1], item[0].mode)
    for name in ("install.py", "install.sh", "native_tour_quiet.py",
                 "test_native_tour_quiet.py", "test_native_transaction.py", *DOCS):
        put(files, name, normalize_text(name, (HERE / name).read_bytes()),
            0o755 if name.endswith(".sh") else 0o644)
    put(files, "release.json", json_data(meta), 0o644)
    write_sums(files, "SHA256SUMS")
    verify_sums(files, "SHA256SUMS")
    for name in old_meta["payload_sha256"]:
        assert files["payload/" + name][1] == old["payload/" + name][1]
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / (NAME + ".tar.gz")
    output.write_bytes(tar_data(files, NAME))
    output.with_name(output.name + ".sha256").write_bytes(
        (digest(output.read_bytes()) + "  " + output.name + "\n").encode("ascii"))
    for name in DOCS:
        (dist / name).write_bytes(files[name][1])
    print(output, flush=True)


if __name__ == "__main__":
    main()
