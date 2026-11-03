# ARM64 Acquisition Target Trial Preparation

Follow-up on 2026-11-04: installed integration corrected the production GTSAM
prerelease-alias verifier. A new current-source candidate,
`acquisition-target-trial-20261104-installed-a`, was prepared from the unchanged
verified native build and new component `5f0464ab...`. Its trial manifest is
`b57ae103494445fadf415b2cc29d326b4319fe5050658cc227f7c2ef9a0816b1`.
All nine members and 14 current source identities were independently checked;
29 contracts are configured and zero executed. The old candidate and QEMU
record below remain preserved historical evidence, not the current Python
validator candidate. See [installed inputs](INSTALLED_ACQUISITION_INPUTS.md).

Date: 2026-11-03. Prepared and checked offline; **zero ARM64 acquisition
target executions**. SSH did not complete a banner/host-key exchange, so no
authenticated robot connection, upload, remote command, sensor subscription,
service change or motion occurred in this milestone.

## Scope

The [cross-built component](ACQUISITION_ARM64_COMPONENT_20261103.md) and
[passive runtime wiring](LIVE_ACQUISITION_WIRING_20261103.md) require an actual
target loader check before passive live integration. The new
`tools/run_acquisition_target_trial.py` prepares a separate source-bound bundle
and runs `tools/acquisition_target_probe.py` in a new ordinary-user directory.
It does not install the product, change vendor services or connect to ROS.

Preparation independently verifies the existing cross-build, component, pinned
core and host synthetic export. It copies only the module, compiled synthetic
contract executable, six core libraries and the standalone Python probe.
Every member, the exact 29-test inventory and the source identities are hashed.
Unknown members, source drift, changed native bytes, path escapes and reports
inside the frozen input bundle are rejected before connection.

The target command uses a clean environment, isolated library path, bounded
execution and isolated Python without bytecode writes. The probe checks Linux
AArch64 and ordinary-user execution before native loading, then checks actual
loaded providers in `/proc/self/maps`, exercises the native module lifecycle,
runs all 29 synthetic contracts and compares the 840-byte export with the
verified host digest. Target results must match the exact bundle identities;
partial, wrong-architecture and mismatched results cannot pass.

SSH retains the existing strict known-robot identity policy and prompts for the
password in memory only. Reports are new private files outside the inputs;
failed connections preserve a failure record rather than a passing result.
Existing reports and remote trial directories are never overwritten.

## Prepared Bundle

Current private directory: `acquisition-target-trial-20261103-c`, under the
existing WSL user's `.cache/lingxi-next`. Earlier preparations remain preserved
but are not the current-source candidate. Generated binaries, manifests and
private reports are not committed or included in the product delivery.

| Item | Verified preparation |
| --- | --- |
| Members excluding manifest | 9 |
| Total member bytes | 23,066,002 |
| Bound source files | 14 |
| Isolated core libraries | 6 |
| Configured synthetic contracts | 29 |
| ARM64 contracts executed | **0** |

Trial manifest SHA-256:
`6b8130a93f318574b365d355e6d12716db9b8cbac2ccf1ba9896d86206619be4`.
Component manifest SHA-256:
`f901d001f51ffcb1a74d3c00ef9241f012fae4075ece591c49982d901ed98c3d`.
Expected synthetic export SHA-256:
`7fbd486080b3d054af6e3eed817bad3c67642e71b2cd527039e885e065133108`.

An independent process reverified all prepared members and their current
source identities after preparation. This verifies the input package, not
ARM64 execution or successful remote upload.

## Completed Checks

- Linux scientific environment: 65 acquisition target/component-related
  Python tests passed, no skips, 7.098 seconds. Discovery pattern:
  `test_acquisition*.py` in `tools/tests`.
- Windows: all 18 new target-runner/probe tests passed, no skips,
  0.306 seconds. These overlap the Linux suite and use synthetic files/peers.
- Actual x86_64 native library: the new probe's lifecycle helper passed;
  all 29 compiled native contracts exited successfully with no output;
  the export helper decoded and matched the existing host export digest.
  The original host libraries were used, not ARM64 emulation or execution.
- No new full-product, Web, Android, voice or physical acceptance result is
  claimed for this narrowly scoped tool-only milestone.

The host native helper requires both the pinned `prefix/lib` and
`sysroot/usr/lib/x86_64-linux-gnu` in the process-start library path. An initial
check without the latter failed before loading `libspdlog`; the subsequent
complete-path check passed. That failure was not an ARM64 target result.

## Remaining Gate

Direct LAN access is currently unresolved: the workstation WLAN was on a
different subnet, the selected route went through a TUN interface, and the
apparent TCP connection closed before SSH identity exchange. Explicit direct
WLAN and WSL attempts timed out. Routing/proxy settings were not changed and
no alternative robot IP was assumed. No fresh scan is needed for this gate.

After direct access returns, run the current-source bundle against the known
robot identity with `run ... --host <current-ip> --user <ordinary-user>
--known-identity <existing-known-host> --directory
lingxi-next-validation-arm64-acquisition-<new-id> --bundle <prepared-directory>
--manifest-sha256 <verified-digest> --output <new-private-report>`. Check the
complete validated target result before passive shared-runtime integration.

Even a passing target synthetic trial does not validate live throughput,
metric scale, gravity/ground/base alignment, terrain support or navigation.
These remain false in every trial result, including `navigationReady` and
`deploymentApproved`. The existing point-cloud UI and display Z interval
0.1--1.4 m are unchanged; display filtering is not traversable-ground evidence.
