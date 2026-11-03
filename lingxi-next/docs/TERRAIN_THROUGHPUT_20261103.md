# Terrain Computation And Transport Throughput

Date: 2026-11-03. Offline Linux x86_64 only. No robot connection, sensor
subscription, deployment or physical motion. APK/package remain 0.1.22.
Point-cloud display and default Z **0.1--1.4 m** are unchanged.

This report preserves the Python/transport milestone's original measurements.
The subsequent [native scan-accumulation follow-up](TERRAIN_NATIVE_THROUGHPUT_20261103.md)
has separate exact-output measurements and fresh actual ROS/recording reruns.

## Implementation

The immutable scan/batch digests are cached without changing their bytes or
dataclass/protocol fields. Native OctoMap and Patchwork++ inputs use contiguous
double buffers rather than repeated ctypes element assignments. Ground-cell
accumulation avoids unused default allocations. Classification thresholds,
ground geometry, source receipts, ray selection, work limits and expiry remain
unchanged. No unknown cell is converted to free.

The private terrain worker now uses `returns_f64le`: exact little-endian doubles,
base64 encoded, with 48 raw bytes / 64 wire characters per measured ray. The
decoder accepts either the existing JSON ray list or the packed field, never
both on one scan. Complete-scan, finite-value, origin/endpoint, length and budget
validation remain required. Public/default encoding stays JSON. This is not
lossy quantization or a general compression claim; wire size can increase.

## Actual Native Benchmark

Baseline Python sources were preserved from commit
`7c082102cd760dbef8d08e58bb88b054727c413a`. Both runs use the same benchmark,
fixture, native library and dependency hashes, four synthetic geometries, three
scan counts and five trials per case. The runner measures encoding, framing,
parsing, reconstruction and actual terrain production in one process, including
native calls. It does not include process startup, OS pipe/scheduler latency,
live sensors, registration, ROS delivery or physical stopping.

All twelve observation, clearance, support, traversability, raw-cost hashes and
native statistics match exactly between the two reports. Median total time
decreases **13.4%--29.5%** on this host:

| Fixture | Scans / Rays | Before ms | After ms |
| --- | --- | --- | --- |
| Open | 2 / 8,208 | 84.9 | 65.6 |
| Open | 4 / 24,416 | 275.4 | 232.1 |
| Open | 12 / 84,816 | 1,011.9 | 841.5 |
| Blocked | 2 / 8,208 | 77.7 | 60.0 |
| Blocked | 4 / 24,416 | 244.1 | 191.9 |
| Blocked | 12 / 84,816 | 875.1 | 689.5 |
| Detour | 2 / 8,208 | 77.2 | 66.9 |
| Detour | 4 / 24,416 | 268.8 | 225.0 |
| Detour | 12 / 84,816 | 983.5 | 802.1 |
| Unsupported shortcut | 2 / 7,824 | 75.2 | 62.6 |
| Unsupported shortcut | 4 / 23,648 | 272.2 | 220.9 |
| Unsupported shortcut | 12 / 82,404 | 1,140.9 | 804.5 |

For open ground at two scans, wire size grows from 385,786 to 525,996 bytes;
for blocked terrain at four scans it shrinks from 1,600,414 to 1,563,564 bytes.
Full samples, stage timings, identities and byte counts are retained in the
[baseline](evidence/terrain-throughput-baseline-20261103.json) and
[optimized](evidence/terrain-throughput-packed-20261103.json) reports.

The 0.2 s scan span / 0.2 s processing deadline / 0.3 s ingress TTL of saved-map
terrain are admission limits, not demonstrated throughput. Several larger
fixtures still exceed 0.2 s even on the host. Twelve-scan benchmarks deliberately
use diagnostic budgets, not the live saved-map profile.

## Checks And Reproduction

The final expanded actual-native regression collection completed 177 tests:
176 passed, one skipped (actual NavFn library unavailable), zero failures/errors,
25.238 s. This includes all eight current-source recorded replay checks, nine
registered ROS evidence checks, eight generic ROS evidence checks and four
benchmark evidence checks. The eleven new packing/digest tests also pass on
Windows. The release-source filter check passed two tests on Windows, with its
Unix symlink test skipped. The source-bound
benchmark tests verify identities, complete samples and timing aggregates;
they do not impose machine-dependent speed thresholds.

Reproduce in the isolated scientific Python environment with the pinned actual
ground/OctoMap libraries and build reports. Run first with the preserved baseline
Python tree and `--encoding json`, then the current tree and `--encoding f64le`:

```sh
rtk proxy python -B tools/benchmark_terrain_pipeline.py \
  --python-source PYTHON_TREE --encoding f64le \
  --ground-library PINNED_GROUND --octomap-library PINNED_OCTOMAP \
  --octomap-build-report PINNED_OCTOMAP_REPORT --output NEW_REPORT
rtk proxy python -B -m unittest discover -s tools/tests -p 'test_terrain_packing.py' -v
rtk proxy python -B -m unittest discover -s tools/tests -p 'test_terrain_throughput_evidence.py' -v
```

Actual isolated Humble consumer reruns are tracked in the
[registered ROS report](REGISTERED_TERRAIN_ROS_VERIFICATION_20261103.md).
Recorded data still requires calibrated ground/gravity/extrinsic/base geometry;
the inconclusive floor offset is not adopted. ARM64 throughput, supported floor,
the full physical localization/Nav2/AIMDK stop chain and field acceptance remain
open. This milestone is not complete product delivery or voice-latency evidence.
