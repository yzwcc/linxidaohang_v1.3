# ARM64 Observation Compatibility

This is a synthetic native-callback milestone, not live mapping or navigation
acceptance. The deployed Web service, vendor software and field map 1.2 were
unchanged. No sensor subscription or motion command was created.

## Build And Target Checks

`tools/build_glim_observations_arm64.py` first verifies the existing tested
x86-64 observed-mapper build. It cross-builds an exact copy of those frozen
sources, with a separate parent CMake wrapper adding the synthetic callback
fixture. The ordinary GLIM source checkout and existing libraries are not
modified. Both outputs must be ELF64/AArch64 with Jammy-compatible symbol
requirements and the complete asynchronous/observation C ABI. The private EOF
submapping implementation remains isolated from upstream class symbols.

`tools/run_observation_target.py prepare` verifies the build again and freezes
three native files: the observed library, matching global-mapping library and
fixture executable. The manifest binds their bytes, the actual cross-build
report, runner source and known core-library hashes. `run` uses the existing
known-host SSH identity check, an ordinary user and a new private directory.
It neither starts ROS nor changes services. Password input remains memory-only.

On the actual ARM64 host, a standard-library Python probe verifies core and
uploaded file identities, loads the observation library, and checks its mapped
libraries through `/proc/self/maps`. Each fixture runs in its own bounded child;
the child's loader diagnostics must resolve the same expected native/core
libraries. Child logs and synthetic archives are retained privately.

The seven target scenarios passed on 2026-10-31:

| Scenario | Expected Result |
| --- | --- |
| Base callback capture | Preserve moving per-return origins and endpoints |
| Corrected global submap | Transform both ray ends; preserve source identity |
| Short smoothed interval | Use the entire bracketed original LIO trajectory |
| Later fixed-lag frame pose | Normalize using the acquisition reference |
| Rotated LiDAR/IMU lever arm | Retain the inverse extrinsic and composition order |
| Return outside trajectory | Reject; create no archive |
| Missing trajectory | Reject; create no archive |

`tools/check_observation_target_geometry.py` independently reconstructed all
five positive target archives on the numerical host. Every archive contained
one submap, one scan and three synthetic returns at revision two. Origins and
endpoints matched the analytically declared geometry within `1e-12` raw units.
This is a synthetic arithmetic tolerance, not physical accuracy.
The acquisition/cross-build/target-manifest regression passed 30 tests with no
skips on the isolated numerical host. Mock build fixtures and synthetic ELF
headers in unit tests are explicitly not counted as target execution evidence.
The combined observation, asynchronous mapper and MappingSession regression
passed 65 tests with no skips.

## Identities

- ARM64 observation library SHA-256:
  `2fb38e311bffd2d6f756a1e63db6dc9f1deef01c29d5022fa76f3ecbb5afcf68`.
- ARM64 fixture SHA-256:
  `2a104b7b2f8e2cac8da52216243175004c3658175cb2763e7b83273ae100b2d4`.
- Private cross-build report SHA-256:
  `7a688ffa40100c0fb6a767f3d9a3751197bd60a8174061947855936a0203da35`.
- Prepared target manifest SHA-256:
  `be72c948204f6058460f8294fef6085a5a65e3c4b00f11a632d32e1115bb54de`.
- Actual ARM64 native report SHA-256:
  `d662b62a8337c801eff861c489dff0c74512a681ee6e1c07e9d2cec101a18d5b`.
- Independent source-bound geometry verification report SHA-256:
  `83508a5654d7f8c9e91ce0a9239b6e5dafbb04763549611727a68426c9eee0d9`.

The library requires at most GLIBC 2.32 and GLIBCXX 3.4.30; the fixture requires
GLIBC 2.34. These are inspected artifact requirements; successful execution is
the separate actual-target evidence above.

## Remaining Integration

The observed library has not replaced the product's active mapper. Full ARM64
recorded save/drain/reload, product manifest/configuration admission, observation
binding during normal Web saves and restart/reload checks remain required.
Acquisition sources are still raw GLIM coordinates. Measured scale, timing,
ground registration, current-site association and actual occupancy integration
are not accepted by these synthetic checks. Do not request another field scan
until the real observation-producing product runtime is ready.

The earlier five full-regression source/report identity failures remain recorded
in [SLAM_OBSERVATION_ARCHIVE.md](SLAM_OBSERVATION_ARCHIVE.md). No historical
report was rewritten and no assertion was weakened to hide those failures.
