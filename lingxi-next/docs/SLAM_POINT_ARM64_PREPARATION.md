# ARM64 Point Extension Validation Preparation

Date: 2026-10-29. A small isolated target-validation bundle is prepared from
the already cross-built point extension. **It has not been uploaded to or
executed on the robot.** No ARM64 native-contract success, recorded point
channel, live rendering or navigation is claimed by this preparation.
The product, Android APK, original SDK/services/maps and frozen GLIM code
remain unchanged.

## Prepared Inputs

`tools/prepare_glim_point_target.py` rechecks the private cross-build report
and both actual ARM64 ELF files against the independently published
`slam-native-point-arm64-build-evidence.json`. It does not rebuild the core,
extension or contract executable and never executes either ARM64 artifact.

The new private bundle has exactly five files:

- `lib/liblingxi_glim_point_output.so`, the existing Release extension.
- `bin/point_output_contract`, the existing ARM64 native contract executable.
- `tools/glim_point_target_probe.py`, the new opt-in target diagnostic.
- `tools/slam_target_probe.py`, the unchanged core-integrity helper.
- `point-manifest.json`, exact file sizes/digests, pinned source commits,
  expected existing core-library digests and the 11 allowed native scenarios.

No configuration, sensor recording, business data, credentials or core library
is included. The target uses its already verified isolated CPU core; it does
not load from or replace the vendor AIMDK directories.

Two independent preparations produce byte-identical archives:

| Field | Value |
| --- | --- |
| Archive bytes | 81,761 |
| Bundle files | 5 |
| Native scenarios prepared | 11 |
| Native contracts executed on ARM64 in this phase | 0 |

Manifest SHA256:

```text
06c021f7c12591f68b49ef4883636e3f58268d0b849b3f721bcf19663c168285
```

Archive SHA256:

```text
f177bd18e7cce77bd898e5f8ae01c5b1f2fbbe30fd6bf852bc8bb99eab79d835
```

The archive inventory was independently read and compared. Only the contract
executable has mode 0700; the library, manifest and scripts have mode 0600.
Member names are fixed relative paths; timestamps and owner metadata are
fixed for repeatability. This is a diagnostic archive, not an ARM64 product
installer or a newly delivered Android release.

## Target Diagnostic

`glim_point_target_probe.py` requires a fresh operator confirmation before
reading its inputs or loading native code. The eventual trusted launcher
must first verify the current robot identity, address and onsite stationary,
supervised, readily accessible emergency-stop conditions. Historical onsite
messages and the command-line flag alone are not proof of current conditions.

The runtime accepts only ordinary-user Linux aarch64, Python 3.10 and glibc
2.34 or newer. The extension's recorded GLIBC requirement is 2.17, but the
separate contract executable requires 2.34. It checks the exact clean
environment returned by `environment(bundle, core)` without ROS paths,
inherited credentials or library preloads.

The externally supplied manifest SHA256 must match. Each declared regular
file is checked again, both binaries must have ELF64 little-endian AArch64
headers, and undeclared bundle files are rejected. The original core manifest
and every expected core-library digest must also pass before native loading.
Existing output or output overlapping either input is rejected; results use
a new private directory below the ordinary user's cache.

In that new process, the probe:

1. Loads the original CPU odometry module and point extension, without
   creating an odometry engine.
2. Checks actual `/proc/self/maps` paths and hashes for the extension and
   all six expected core libraries.
3. Resolves five C ABI symbols and checks empty open/take/counts/close
   accounting, with no callbacks or copied points.
4. Runs each of the 11 unchanged C++ contract scenarios in its own owned
   process group at nice 10, with an eight-second limit.
5. Stops on the first failed/forced/timed-out stage, preserving that stage
   and private log rather than running later tests or retrying.
6. Checks that the original bundle and core remain unchanged at the end.

Timeout cleanup sends signals only to the owned contract group, waits and
records forced termination. A forced exit cannot count as a successful
contract. The probe uses no ROS initialization, DDS subscription, sensor
capture, robot connection, motion API, system installation or vendor service.

Even eventual success here will establish loading and isolated synthetic
native contracts only. It will not validate the target's recorded or live
point producer, transport/API composition, map geometry or navigation.

## Local Checks

Sixteen new Linux tests pass; Windows passes 15 and explicitly skips the
Linux process test. Fixtures have clearly synthetic nonexecutable ELF-like
headers, mock library calls or isolated synthetic Python processes. They are
not ARM64 execution evidence. The real Linux fixture checks success, nonzero
exit and bounded timeout cleanup without a robot or native SLAM engine.

The tests also cover external manifest identity, modified/extra files,
wrong architecture, exact scenario names, actual-library path/digest
accounting, the operator gate, empty C ABI cleanup, private output protection,
first-failure accounting and archive contents without native execution.

Final full tools regression: Linux 664 passed with the same explicit
Linux Node/bundle and clean pinned upstream override; Windows 302 passed
and 362 explicitly skipped for platform/dependency requirements. No frontend,
Java, Android or robot runtime source changed in this preparation, and those
builds were not rerun or relabelled as new device acceptance.

Reproduce preparation on the existing ordinary-user Linux build host:

```bash
rtk proxy python tools/prepare_glim_point_target.py \
  --build "$FROZEN_ARM64_POINT_BUILD" --output "$NEW_PRIVATE_PREPARATION"
```

## Next Gate

Current onsite confirmation and identity-checked target inspection remain
required before uploading and executing this diagnostic. After actual
extension loading/contracts pass, prepare and execute the separate
ARM64/Humble recorded point-channel composition. Live sensor composition,
UI rendering/latency, calibration, mapping transactions, loop closure,
relocalization, base alignment, independent navigation and product acceptance
remain outstanding.
