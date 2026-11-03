# ARM64 Terrain Target Trial Preparation

Updated 2026-11-03. This prepares direct target loading and synthetic algorithm
checks for the existing Patchwork++ ground and OctoMap clearance adapters.
It does not install a robot service, subscribe to sensors, move the robot,
validate physical ground support or approve navigation.

## Prepared Trial

`tools/run_terrain_target_trial.py prepare` rechecks the real cross-build,
its frozen inputs and the previous actual host-native verification before
creating a new private trial directory. Existing trials and reports are never
overwritten. The bundle has exactly 13 regular files plus two private relative
SONAME links and one manifest: production Python ground/occupancy code, its
NavFn dependency, the existing 21 ground/fusion contracts, a standalone probe,
two AArch64 adapters, two pinned OctoMap libraries and original license notices.
No maps, recordings, credentials or business data are included.

Actual preparation completed in the ordinary WSL user cache under
`terrain-target-trial-20261103-a`, from `terrain-arm64-build-20261103-c`.
The manifest SHA256 is:

```text
eaaa91328e628470b3c18e1700491eb1272f77ca31cfdaffb5324edb6ca7c9ec
```

The adapter identities remain those recorded in
[the ARM64 component build](TERRAIN_ARM64_BUILD_20261103.md).
Preparation checks exact inventory, byte counts, SHA256, ELF64 AArch64 headers,
relative dependency aliases and current source identities. Additional files,
empty directories, altered sources or libraries, and x86 replacement libraries
are rejected. The real preparation also exercises the builder report actual
`file` field rather than an assumed artifact path.

## Execution Contract

`run` requires an explicit host, ordinary user, previously known robot identity,
new remote validation directory, expected manifest digest and new private local
report. It reuses strict known-host verification, uploads only the declared
bundle and uses a clean environment with isolated Python (`-I -B`). The target
process has a 120-second timeout; transport waits and output volume are bounded.
There is no package installation, ROS startup or service switch.

The probe verifies the bundle before and after execution, loads both real
adapters and checks all four expected library identities in `/proc/self/maps`.
It sets the actual-library inputs for the existing tests, then requires all
21 exact successful test IDs, zero skips and zero errors. The runner validates
the result, current local bundle and current source identities again. Failed
connections, target failures and timeouts preserve private failure reports;
remote directories remain available for diagnosis.

Use the tool from the `lingxi-next` directory. `prepare` accepts explicit
`--build`, `--cross-work`, `--native-work`, `--ground-host`, `--octomap-host`,
`--octomap-arm64`, `--host-check` and new `--output` paths. `run` accepts
`--bundle`, `--manifest-sha256`, `--host`, `--user`, `--known-identity`,
`--directory` and new `--output`. Password input is memory-only through the
existing connector; do not place credentials in arguments or files.

## Verification And Limits

- Linux: all 20 new preparation, integrity, result, mocked SSH lifecycle,
  timeout/failure and source-mutation tests passed, zero skips.
- The combined Linux regression for this trial, terrain cross-build and existing
  acquisition/general SLAM target tools passed all 61 tests, zero skips.
- Windows: four process/helper checks passed; 16 bundle lifecycle checks were
  skipped because the workstation cannot create the Linux SONAME symlink fixture.
  The full Linux run covers those checks.
- The new exact-result helper ran all 21 existing contracts against the real
  pinned **x86_64** Patchwork++ and OctoMap libraries, zero skips, in approximately
  0.35 seconds. This is synthetic host algorithm evidence, not target timing.
- The real AArch64 bundle preparation succeeded; no ARM64 executable or library
  was executed during preparation. All admission boundary fields remain false.
- The current LAN recheck used the Meta interface and returned EOF before an
  SSH banner. No authentication, upload, robot command, deployment or network
  configuration change occurred. No additional scan is needed for this trial.

Even a successful target trial will not manufacture the adjacent successful
runtime build/load reports required by `live_acquisition.terrain_settings()`.
Live sensor timing, calibrated ground geometry, supported free cells and the
guarded physical navigation chain remain separate integration work.

The existing immutable 0.1.21 commissioning archive is unchanged. This tooling
does not rebuild the Web app or APK; point-cloud presentation and default Z
limits of 0.1--1.4 m are unchanged. See
[the complete product workstream](OFFLINE_PRODUCT_ACCEPTANCE.md) for open gates.
