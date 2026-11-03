# ARM64 Acquisition Emulation

Recorded 2026-11-04 from the existing frozen acquisition trial, after release
0.1.23. This verifies execution of the actual ARM64 synthetic contract binary
under QEMU user-mode on an ordinary-user Linux x86_64 workstation. It is not
robot execution, real sensor throughput or physical mapping/navigation evidence.
Robot SSH did not establish an authenticated session; no robot command ran.

## Result

- All **29 ARM64 native acquisition contracts passed** in the final run.
- The 840-byte native export exactly matches the previously verified host
  digest: `7fbd486080b3d054af6e3eed817bad3c67642e71b2cd527039e885e065133108`.
- Native dependencies resolved to seven frozen trial libraries and twelve
  ARM64 sysroot providers, including the loader, libc and libstdc++.
- The odometry plugin's dependency graph was resolved separately because the
  synthetic frame executable does not dynamically open that plugin. This is
  not execution of the plugin's real odometry algorithm.
- Source identities, all trial members, emulator, loader and resolved library
  hashes were checked and remained unchanged through execution.
- Emulation, both dependency listings and export comparison took 3.996 seconds.
  This workstation duration is not a robot latency or live throughput result.

The complete result, without workstation paths or connection details, is committed as
[acquisition-arm64-emulation-20261104.json](evidence/acquisition-arm64-emulation-20261104.json).
The unchanged trial manifest is
`6b8130a93f318574b365d355e6d12716db9b8cbac2ccf1ba9896d86206619be4`;
the component manifest is
`f901d001f51ffcb1a74d3c00ef9241f012fae4075ece591c49982d901ed98c3d`.
See [target preparation](ACQUISITION_ARM64_TARGET_PREPARATION_20261103.md) for
the distinct actual-robot procedure. Its target-execution gate remains open.

## Reproducible Tool

`tools/check_acquisition_arm64_emulation.py` takes explicit absolute paths,
a frozen trial manifest SHA256 and the expected QEMU binary SHA256. It rejects
root/wrong-host execution, modified inputs, undeclared dependency providers,
non-ARM64 libraries and existing/overlapping output directories. Subprocesses
use a clean environment, explicit guest libraries, eager symbol binding,
bounded thread counts and a ten-second per-command timeout. Unexpected output,
nonzero exits, export drift and source/library drift cannot produce success.

```sh
python tools/check_acquisition_arm64_emulation.py \
  --bundle "$TRIAL" --manifest-sha256 "$TRIAL_SHA256" \
  --sysroot "$ARM64_SYSROOT" --qemu "$QEMU" \
  --qemu-sha256 "$QEMU_SHA256" --output "$NEW_OUTPUT_DIRECTORY"
```

QEMU was downloaded using the configured Ubuntu package metadata and extracted
into a private cache. No system package was installed, no binfmt handler was
registered and no robot or vendor package was changed.

- Package: `qemu-user-static`, version `1:8.2.2+ds-0ubuntu1.18`, amd64.
- Package SHA256: `5bb397f66063efa349f6fd5cb3b68cd96f29edd0994e4ba5115cf0859a716bf0`.
- QEMU AArch64 binary SHA256: `e4f8d99e9ff69c3cefffab71cee358ce2af1ecba1282d04c3eeb44ef76f5a71e`.
- ARM64 loader SHA256: `1f4fe51e11324b5bcc01ef1fd3859b5dd344ec16a8674d74ce618ef65016fbaf`.

The initial runner rejected a legitimate loader self-listing without an arrow
while inspecting the shared odometry object. That failed report is retained
privately under `acquisition-emulation-20261104-a`; zero contracts had run.
The parser now explicitly recognizes that loader-only form, with a regression
test, while retaining path, architecture and hash checks. The final result is
the separate `acquisition-emulation-20261104-b` run, not a relabelled failure.

## Verification

- Actual QEMU execution: all 29 native ARM64 contracts and the exact export
  comparison passed. This is distinct from the mocked runner tests.
- Ordinary-user scientific Linux acquisition regression: 77 passed, no skips,
  6.714 seconds. This includes the twelve new emulation runner/evidence tests.
- Ordinary-user Linux historical-source tests: 15 passed, no skips.
- Windows emulation runner/evidence tests: 12 run, 9 passed, 3 POSIX-path skips.
- Windows historical-source tests: 15 run, 14 passed, 1 symlink-permission skip.
- The historical audit verified all eight frozen reports against Git. It still
  reports current-source drift; historical integrity does not grant acceptance.
- No Web/APK rebuild, full scientific-suite rerun or target installer run was
  performed for this offline tool-only update.

## Acceptance Boundaries

The broader acquisition regression initially exposed an old ground-inspection
test incorrectly requiring current sources for its immutable 2026-11-03 report.
The report itself was not changed. Its seven source hashes were verified against
its original Git revision and catalogued as historical; current ground and
occupancy source drift remains explicit. This is not a new ground replay or
physical calibration result.

Every target/physical acceptance flag remains false. The tool does not import
ROS, subscribe to sensors, issue motion, change services, calibrate geometry or
enable navigation. It does not run the actual target Python/ctypes loader path,
exercise a live mapping session or establish map quality, stopping behavior,
cloud voice latency or Android device operation.

The immutable 0.1.23 archive and APK were not rebuilt. This offline tool adds
compatibility evidence for the existing native component; native dependencies
and target configuration are still external to the commissioning archive.
The next acquisition gate is the existing ordinary-user real ARM64 target
trial when authenticated robot access is available, followed by passive live
acquisition/terrain integration. Supported-ground and physical calibration
requirements remain mandatory, including for previously recorded maps.
