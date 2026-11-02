# ARM64 Per-Return Acquisition Component

Date: 2026-11-03. Ordinary-user isolated Linux x86_64 cross-build only.
No robot connection, sensor subscription, deployment, native target execution,
motion, Web/APK change or navigation acceptance.

## Purpose And Implementation

The continuous registered-terrain replay already uses real per-return ray
origins and endpoints, but its acquisition extension had only been built and
run on x86_64. A production ARM64 localization owner cannot load that binary.
This change supplies the target component and its explicit source/core-bound
selection format. It does not claim that the live factory is wired yet.

`build_glim_acquisition_arm64.py` reuses the existing verified Jammy cross
toolchain and pinned GLIM/GTSAM core. It requires the current host acquisition
build's 29 passing contracts, copies those exact native sources, compiles the
module and contract executable, checks ELF architecture/ABI requirements and
requires exactly the five public C functions. CTest is invoked with
`--show-only=json-v1`: it lists and checks all 29 target commands without
running them. Source snapshots, core libraries, artifacts and inputs are
verified again after the build and during independent verification.

`prepare_acquisition_bundle.py` creates a separate private component directory
containing the module, build report and `acquisition-manifest.json`.
`robot-runtime/python/acquisition_bundle.py` verifies its explicit manifest,
source identities, ELF machine, build report and core-library identities before
any native load. Versioned SONAMEs and bundled aliases are accepted only when
their bytes agree. The component does not bundle or replace AIMDK, GLIM core,
robot maps, settings or credentials.

## Actual Build And Package

Private build: `acquisition-arm64-build-20261103-a`.
Private component: `acquisition-arm64-bundle-20261103-a`.
Both are under the existing user's `.cache/lingxi-next` in WSL. No generated
binary or archive is committed. The committed
[build aggregate](evidence/acquisition-arm64-build-20261103.json) contains
technical counts and hashes, not robot business data or credentials.

| Artifact | Architecture | Maximum GLIBC Requirement |
| --- | --- | --- |
| `liblingxi_glim_acquisition_output.so` | ELF64 AArch64 | 2.32 |
| `acquisition_contract` | ELF64 AArch64 | 2.34 |

Module SHA-256:
`5eb4f079d9f753642eac64aef83b5454022ee9878cd57357567e15c5b75e6f0d`.
Component manifest SHA-256:
`f901d001f51ffcb1a74d3c00ef9241f012fae4075ece591c49982d901ed98c3d`.

The component was prepared from an independently reverified build. Its
runtime verifier accepted the actual component against the exact compiled
core inventory. This is static host-side verification, not an ARM64 loader,
target contract, throughput or live-sensor result.

## Checks And Next Step

- Scientific Linux: 47 acquisition-related Python tests passed, no skips or
  failures, 4.734 seconds. These include 14 new builder/bundle tests.
- Actual existing x86_64 native acquisition binary: all 29 C++ contracts
  rerun and passed, 0.32 seconds. These are not ARM64 executions.
- Windows: all seven pure-Python component-verification tests passed.
- Target contracts: 29 compiled/listed, **zero executed**.
- `git diff --check -- lingxi-next` passed. No new full-product, UI, Java,
  APK or physical acceptance result is claimed.

Next wire explicit module selection and same-owner acquisition consumption
into the live localization factory, including startup-failure and terminal
cleanup. That work must preserve original scan receipts and generations,
withdraw old terrain on pause/stop/reseed/loss, and keep navigation unavailable
without calibrated metric/gravity/ground/base alignment and field acceptance.
The existing point-cloud display and default Z 0.1--1.4 m are unchanged.
