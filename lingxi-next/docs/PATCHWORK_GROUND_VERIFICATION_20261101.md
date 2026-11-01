# Recorded Ground Support Verification

Date: 2026-11-01. Offline host: WSL Ubuntu x86_64, Python 3.12.3,
g++ 13.3. No robot connection, sensor subscription, deployment or motion.
The [machine-readable receipt](PATCHWORK_GROUND_VERIFICATION_20261101.json)
contains current source, native library, dependency and input identities.

## Implementation

- Use [Patchwork++](https://github.com/url-kaist/patchwork-plusplus), pinned to
  `3e6903a1d5537a4cc2ace897b0bbb98a92d6014c` (BSD-2-Clause), as a ground
  candidate algorithm. Its Eigen-only core fits the existing bounded native
  offline workers without adding another ROS runtime.
- Hash-check the pinned source files and adapter, compile from local cached
  sources, verify a fresh-process load, and retain the exact compiled copies.
  XYZ-only inputs do not fabricate intensity: reflected-noise removal is off.
  Temporal reversion is off and each scan gets a new estimator, so adaptive
  history cannot cross map boundaries. Native calls serialize upstream stdout.
- Explicitly require a declared gravity-aligned Z-up frame and sensor height.
  Check every scan before native projection/segmentation. Reject sensor-height
  error above the declared tolerance (default 0.08 m), or scan-origin span above
  0.35 m. A declaration is not physical calibration.
- Ground support needs at least three near-level returns across two scans per
  cell, with height spread at most 0.04 m. Steps, drops, conflicting labels,
  sparse observations and unobserved cells do not become free floor. There is
  no interpolation across holes. A free fused cell requires both independent
  support and observed OctoMap air clearance.
- Keep point-cloud display/clearance defaults at Z 0.1--1.4 m. Ground evidence
  uses preserved raw returns, including floor returns outside that display
  slice. It does not rewrite or discard the saved point cloud.
- Candidate and fused results are explicitly offline types, not metric
  `CostmapSnapshot` inputs. All physical-calibration, ground-support and
  navigation-ready acceptance flags remain false.

## Actual Results

The current ground library is
`0f0f3b74dee2acccb520e6f7a95a668c268fe22a53f165821a5b8427dd4955a6`.
The native fixture run executed **21 tests, all passed, zero skips**, using
actual Patchwork++ and OctoMap libraries. It covers supported floor, steps,
drops, rough/unknown ground, invalid frames, source identity, native accounting
and conservative clearance fusion. This is synthetic algorithm verification,
not a physical navigation test.

The preserved static recording was replayed twice with identical source,
return-accounting, clearance, ground and fusion identities:

| Measurement | Result |
| --- | --- |
| Scans | 313 |
| Original / selected / omitted returns | 2,012,279 / 130,834 / 1,881,445 |
| OctoMap free / occupied / unknown cells | 1,722 / 231 / 14,431 |
| Ground / nonground / unclassified labels | 432 / 130,402 / 0 |
| Ground-candidate free / occupied / unknown cells | 0 / 1,653 / 14,731 |
| Fused free / occupied / unknown cells | 0 / 1,653 / 14,731 |
| Total worker time, first / repeat | 11.506 / 11.378 seconds |
| Peak RSS, first / repeat | 133,768 / 135,236 KiB |

The explicit replay geometry uses an uncalibrated +0.6 Z translation,
declared sensor height 0.6 m and ground Z 0. It is not a measured robot
transform. **Zero supported free cells means this recording/declaration does
not supply sufficient traversable-ground evidence.** It does not establish
that the physical floor is blocked. Do not loosen thresholds or promote the
1,722 air-clear cells to make a navigation test pass.

The moving recording failed the frame precheck at scan 563: height error
0.093196 m exceeded 0.08 m; scan-origin span was 0.043026 m. The verifier
confirmed that no accepted report or partial clearance/ground/fusion binary
was published. The declaration was not automatically adjusted.

## Reproduction

All tools operate on explicit local paths; no robot address is used. Private
recordings and build outputs stay outside Git and release packages.

```bash
python tools/build_ground_offline.py --source "$PATCHWORK_SOURCE" \
  --eigen "$EIGEN_HEADERS" --output "$NEW_GROUND_BUILD"
python tools/check_ground_native.py --library "$GROUND_LIBRARY" \
  --sha256 "$GROUND_SHA256" --build-report "$GROUND_BUILD_REPORT" \
  --octomap-library "$OCTOMAP_LIBRARY" --octomap-sha256 "$OCTOMAP_SHA256" \
  --octomap-build-report "$OCTOMAP_BUILD_REPORT" --output "$NEW_NATIVE_RECEIPT"
python tools/verify_recorded_ground.py --static-config "$STATIC_CONFIG" \
  --moving-config "$MOVING_CONFIG" --library "$GROUND_LIBRARY" \
  --sha256 "$GROUND_SHA256" --build-report "$GROUND_BUILD_REPORT" \
  --native-receipt "$NATIVE_RECEIPT" --output "$NEW_REPLAY_DIRECTORY"
```

The recording verifier checks current build/native-test identities, changes
only the library path/hash in copies of existing declarations, checks repeated
output identities and the explicit moving-frame rejection, then exports a
sanitized receipt. Timing may differ; result identity must not.

## Remaining Work

Ground support is now implemented and tested as an offline candidate producer.
It is not yet an accepted live metric costmap producer. Required next work is
recorded mapping/localization/support composition with validated geometry,
bounded incremental updates and the existing isolated Nav2/API workspace.
Robot-height/gravity/base transforms, moving trajectories, coverage, target
ARM64 resource limits and physical navigation require separate acceptance.
Continue the complete-product offline workstreams in
[OFFLINE_PRODUCT_ACCEPTANCE.md](OFFLINE_PRODUCT_ACCEPTANCE.md); this milestone
does not replace the voice, tour, Android or complete-stack packaging gates.
