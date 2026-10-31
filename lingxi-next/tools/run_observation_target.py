"""Prepare and execute isolated ARM64 acquisition fixtures, never mapping or motion."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil

from run_glim_point_target import connect, execute, private_directory, remote_digest, require_absent

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("base", "corrected", "fallback", "reference-shift", "extrinsic", "unbracketed", "missing-trajectory")
LIBRARY = "liblingxi_glim_full_mapping.so"
FIXTURE = "lingxi_observation_contract"
MEMBERS = ("lib/" + LIBRARY, "lib/libglobal_mapping.so", "bin/" + FIXTURE)
PURPOSE = "arm64_observation_synthetic_contracts_not_live_mapping"
RESULT = "arm64_observation_native_contracts_passed_not_live_mapping_acceptance"
BOUNDARIES = ("sensorsSubscribed", "motionCommandsSent", "servicesChanged", "vendorFilesChanged",
              "recordedDataProcessed", "liveMappingValidated", "physicalScaleValidated", "navigationReady")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def direct_output(path):
    path = path.absolute()
    if (path.exists() or path.is_symlink() or path.resolve() != path
            or not path.is_relative_to((ROOT / ".local").resolve())):
        raise ValueError("new_direct_private_observation_output_required")
    return path


def prepare(args):
    import build_glim_observations_arm64 as build
    report = build.verify(args)
    target = direct_output(args.target)
    target.mkdir(mode=0o700)
    (target / "lib").mkdir()
    (target / "bin").mkdir()
    files = {"lib/" + LIBRARY: args.output / "build" / LIBRARY,
             "bin/" + FIXTURE: args.output / "build" / FIXTURE,
             "lib/libglobal_mapping.so": args.cross_work / "prefix/lib/libglobal_mapping.so"}
    manifest = {"schemaVersion": 1, "purpose": PURPOSE,
                "buildReportSha256": digest(args.output / "build-report.json"),
                "runnerSha256": digest(__file__), "scenarios": list(SCENARIOS),
                "expectedCoreLibrariesSha256": report["inputs"]["crossCore"]["referenceCoreLibrariesSha256"],
                "files": {}}
    for name, original in files.items():
        shutil.copyfile(original, target / name)
        manifest["files"][name] = {"sha256": digest(original), "bytes": original.stat().st_size}
    if build.verify(args) != report:
        raise ValueError("unchanged_observation_cross_build_required")
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"result": "isolated_observation_target_prepared", "manifestSha256": digest(target / "manifest.json")}


def verify_source(source, expected):
    if (source.is_symlink() or source.resolve(strict=True) != source
            or re.fullmatch("[a-f0-9]{64}", expected) is None
            or digest(source / "manifest.json") != expected):
        raise ValueError("unchanged_observation_target_manifest_required")
    manifest = json.loads((source / "manifest.json").read_bytes())
    if (manifest.get("schemaVersion") != 1 or manifest.get("purpose") != PURPOSE
            or manifest.get("runnerSha256") != digest(__file__) or manifest.get("scenarios") != list(SCENARIOS)
            or set(manifest.get("files", {})) != set(MEMBERS)
            or not manifest.get("expectedCoreLibrariesSha256")
            or any(re.fullmatch("[a-zA-Z0-9_.-]+", name) is None or re.fullmatch("[a-f0-9]{64}", value) is None
                   for name, value in manifest["expectedCoreLibrariesSha256"].items())):
        raise ValueError("complete_source_bound_synthetic_observation_target_required")
    if {path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file() or path.is_symlink()} != set(MEMBERS) | {"manifest.json"}:
        raise ValueError("exact_observation_target_members_required")
    for name, entry in manifest["files"].items():
        path = source / name
        if (path.is_symlink() or not path.is_file() or not 0 < entry["bytes"] <= 16 * 1024**2
                or path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]):
            raise ValueError("identical_observation_target_files_required")
        with path.open("rb") as stream:
            header = stream.read(20)
        if header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != 183:
            raise ValueError("actual_aarch64_observation_artifact_required")
    return manifest


PROBE = r'''
import ctypes,hashlib,json,os,platform,subprocess,sys
from pathlib import Path
stage,core=map(Path,sys.argv[1:])
assert platform.machine()=='aarch64' and os.geteuid()>0
m=json.loads((stage/'manifest.json').read_bytes())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
expected={**{str(core/'lib'/n):d for n,d in m['expectedCoreLibrariesSha256'].items()},
          **{str(stage/n):e['sha256'] for n,e in m['files'].items() if n.startswith('lib/')}}
assert all(sha(Path(n))==d for n,d in expected.items())
handle=ctypes.CDLL(str(stage/'lib/liblingxi_glim_full_mapping.so'))
mapped={line.split(None,5)[-1] for line in Path('/proc/self/maps').read_text().splitlines() if len(line.split(None,5))==6}
required={n:d for n,d in expected.items() if not n.endswith('libodometry_estimation_cpu.so')}
assert set(required)<=mapped
rows=[]
for mode in m['scenarios']:
    output=stage/(mode+'.bin')
    assert not output.exists()
    child=subprocess.run([str(stage/'bin/lingxi_observation_contract'),mode,str(output)],
                         capture_output=True,timeout=15,env={**os.environ,'LD_DEBUG':'files'})
    log=child.stdout+child.stderr
    assert len(log)<1048576
    (stage/('private-'+mode+'.log')).write_bytes(log)
    assert child.returncode==0
    initialized={line.split('calling init:',1)[1].strip() for line in child.stderr.decode().splitlines() if 'calling init:' in line}
    assert set(required)<=initialized
    invalid=mode in ('unbracketed','missing-trajectory')
    assert output.exists() is not invalid
    row={'scenario':mode,'exitCode':child.returncode,'loadedLibrariesMatched':True,'archiveCreated':not invalid}
    if not invalid: row.update(archiveSha256=sha(output),archiveBytes=output.stat().st_size)
    rows.append(row)
assert all(sha(Path(n))==d for n,d in expected.items())
report={'result':'arm64_observation_native_contracts_passed_not_live_mapping_acceptance',
        'machine':platform.machine(),'manifestSha256':sha(stage/'manifest.json'),
        'loadedLibrariesSha256':required,'scenarios':rows,
        'boundaries':dict.fromkeys(('sensorsSubscribed','motionCommandsSent','servicesChanged','vendorFilesChanged',
          'recordedDataProcessed','liveMappingValidated','physicalScaleValidated','navigationReady'),False)}
(stage/'report.json').write_text(json.dumps(report,indent=2)+'\n')
'''


def run(args):
    source = args.source.absolute()
    manifest = verify_source(source, args.manifest_sha256)
    output = direct_output(args.output)
    if args.user == "root" or re.fullmatch("observations-[a-z0-9-]{1,48}", args.name) is None:
        raise ValueError("ordinary_user_and_explicit_observation_trial_name_required")
    output.mkdir(mode=0o700)
    client = connect(args.host, args.user, args.known_identity)
    try:
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(30)
            home = PurePosixPath(sftp.normalize("."))
            parent = home / ".cache/lingxi-next"
            uid = sftp.stat(str(home)).st_uid
            core = PurePosixPath(args.core_root)
            if uid <= 0 or home not in core.parents or ".." in core.parts:
                raise ValueError("ordinary_user_home_scoped_core_required")
            private_directory(sftp, parent, uid)
            for name, expected in manifest["expectedCoreLibrariesSha256"].items():
                if remote_digest(sftp, core / "lib" / name, maximum=64 * 1024**2)["sha256"] != expected:
                    raise ValueError("same_verified_robot_core_libraries_required")
            stage = parent / args.name
            require_absent(sftp, stage)
            sftp.mkdir(str(stage), mode=0o700)
            for folder in ("lib", "bin"):
                sftp.mkdir(str(stage / folder), mode=0o700)
            for name in (*MEMBERS, "manifest.json"):
                sftp.put(str(source / name), str(stage / name), confirm=True)
                sftp.chmod(str(stage / name), 0o700 if name.startswith("bin/") else 0o600)
                if remote_digest(sftp, stage / name, maximum=16 * 1024**2)["sha256"] != digest(source / name):
                    raise ValueError("identical_uploaded_observation_file_required")
            command = ["timeout", "--signal=TERM", "--kill-after=5s", "140s", "/usr/bin/nice", "-n", "10",
                "env", "-i", "PATH=/usr/bin:/bin", "HOME=" + str(home), "LANG=C.UTF-8",
                "PYTHONDONTWRITEBYTECODE=1", "OMP_NUM_THREADS=1", "OPENBLAS_NUM_THREADS=1",
                "LD_LIBRARY_PATH=" + str(stage / "lib") + ":" + str(core / "lib"),
                "/usr/bin/python3", "-I", "-B", "-c", PROBE, str(stage), str(core)]
            status, log = execute(client, shlex.join(command))
            (output / "private-command.log").write_bytes(log)
            if status:
                raise RuntimeError("observation_target_contract_failed_see_private_log")
            sftp.get(str(stage / "report.json"), str(output / "report.json"))
            report = json.loads((output / "report.json").read_bytes())
            if (report.get("result") != RESULT or report.get("machine") != "aarch64"
                    or report.get("manifestSha256") != args.manifest_sha256
                    or report.get("boundaries") != dict.fromkeys(BOUNDARIES, False)
                    or [row["scenario"] for row in report["scenarios"]] != list(SCENARIOS)):
                raise ValueError("complete_actual_arm64_observation_result_required")
            for row in report["scenarios"]:
                if type(row["exitCode"]) is not int or row["exitCode"] != 0 or row["loadedLibrariesMatched"] is not True:
                    raise ValueError("all_actual_native_contracts_must_pass")
                mode = row["scenario"]
                sftp.get(str(stage / ("private-" + mode + ".log")), str(output / ("private-" + mode + ".log")))
                if row["archiveCreated"]:
                    path = output / (mode + ".bin")
                    if not 0 < row["archiveBytes"] <= 16384:
                        raise ValueError("bounded_synthetic_observation_archive_required")
                    sftp.get(str(stage / path.name), str(path))
                    if digest(path) != row["archiveSha256"] or path.stat().st_size != row["archiveBytes"]:
                        raise ValueError("identical_collected_synthetic_observations_required")
            verify_source(source, args.manifest_sha256)
            collection = {"result": RESULT, "remoteWork": str(stage), "manifestSha256": args.manifest_sha256,
                          "reportSha256": digest(output / "report.json"), "hostIdentityVerified": True,
                          "syntheticOnly": True}
            (output / "collection.json").write_text(json.dumps(collection, indent=2) + "\n", encoding="utf-8")
            return {"result": RESULT, "scenarios": len(report["scenarios"])}
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare")
    for name in ("cross-work", "native-work", "upstream", "native-adapter", "output", "target"):
        preparation.add_argument("--" + name, type=Path, required=True)
    trial = commands.add_parser("run")
    for name in ("host", "user", "known-identity", "core-root", "name", "manifest-sha256"):
        trial.add_argument("--" + name, required=True)
    for name in ("source", "output"):
        trial.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps({"prepare": prepare, "run": run}[args.command](args)))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
