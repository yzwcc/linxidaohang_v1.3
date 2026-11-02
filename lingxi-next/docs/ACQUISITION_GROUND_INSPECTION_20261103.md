# Read-Only Ground Declaration Inspection

Date: 2026-11-03. Isolated ordinary-user Linux x86_64 Python environment,
preserved acquisition snapshots and real pinned Patchwork++. No robot IO,
sensor subscription, ROS node, deployment, motion, Web/APK change or new scan.

## Why This Check Exists

The actual registered replay completed its paced default-budget diagnostic but
observed zero supported free cells. Ground support and air clearance are
independent requirements; zero fused free cells alone does not identify which
assumption failed. This check narrows the ground-evidence question without
turning a fitted plane into a calibration or changing any navigation behavior.

`ground_inspection.inspect_ground_labels` checks complete source-bound native
labels, the declared floor-height band, above-sensor labels and lower-surface
geometry. Only below-sensor, in-range native ground candidates seed a robust
SciPy `least_squares` hint. Sparse or line-like geometry supplies no plane.
Converged fits still need bounded tilt, offset and residual consistency; the
current 0.04 declared-metre ground-spread limit is the diagnostic P95 threshold.
A good fit could still describe a platform, not the physical floor. All
physical floor/scale/support, configuration-applied and navigation flags remain
false, even for a perfectly flat synthetic example.

`inspect_acquisition_ground.py` accepts only the bounded six-field numeric NPZ
format emitted by the native acquisition trial. It pins the receipt, samples,
configuration, native build and source files; rejects object arrays, unexpected
members, nonfinite data and invalid metadata; and rechecks identities after
native work. Two independent children run with 10-second CPU, 2-GiB address
space, 1-MiB per-file output and 30-second wall limits. Their complete reports must match.
Root, ARM64 and nonisolated/non-Linux CLI execution are rejected before input/native IO.

## Actual Preserved Samples

Final output: private `acquisition-ground-inspection-20261103-b`. Two independent
native runs produced identical aggregate reports. The committed
[evidence](evidence/acquisition-ground-inspection-20261103.json) contains counts,
fit diagnostics and identities, not point coordinates, sensor/base poses, map
identities, private log text or credentials. These are native-odom LiDAR end
samples, **not registered multi-scan navigation windows**. The acquisition and
earlier registered replay journals, sensor profiles and effective configuration
hash maps were separately checked equal.

The unchanged offline declaration uses raw scale 1, +0.6 Z translation, assumed
sensor height 0.6 and ground Z 0. These values are not physical measurements.
The diagnostics below use this declared metric/Z-up frame, not validated metres.

| Measurement | First / Latest |
| --- | --- |
| Original native frame | 1 / 164 |
| Complete measured returns | 7,606 / 7,602 |
| Native ground labels | 627 / 728 |
| Native ground labels above the declared sensor plane | 231 / 296 |
| Native ground labels within the declared floor band | **0 / 0** |
| In-range below-sensor candidates | 396 / 432 |
| Candidate vertical offset | 0.830056 / 0.828080 declared metres |
| Candidate tilt | 2.560206 / 2.479213 degrees |
| Plane residual P95 | 0.071039 / 0.070182 declared metres |
| Solver converged / geometric consistency | true / **false**, for both samples |

This confirms no native ground support in the assumed height band for these
two samples. It does not establish that 0.83 is the real sensor height, that
the fitted tilt is a calibration, that every registered window has the same
failure, or that the floor is physically blocked. Above-sensor native labels
also show why a native ground label alone cannot authorize traversability.
The existing source-bound fusion keeps its independent height/support checks.

## Reproduction And Remaining Work

Final scientific Linux focused regression: **258 passed, zero skips/failures,
27.908 seconds**, with actual native terrain libraries enabled. It includes
twelve new inspection/import/host-boundary/evidence tests and the previous
246-test registered/localization/terrain/ROS suite. The Windows inspection
invocation skipped seven optional-numerical-dependency cases; it does not
validate the inspector or native worker on Windows. No new full-suite, UI,
Java, APK or deployment build result is claimed.

Use the existing isolated scientific Python environment and pinned native build:

```sh
rtk proxy python -B tools/inspect_acquisition_ground.py \
  --trial PRESERVED_ACQUISITION_TRIAL --receipt-sha256 EXPLICIT_RECEIPT_SHA256 \
  --config EXISTING_DECLARED_TERRAIN_CONFIG --output NEW_PRIVATE_OUTPUT
```

Exit 0 means the repeated inspection completed, **not that calibration passed**.
Neither the private declaration nor any robot/runtime setting is written.
Default display/clearance Z remains 0.1--1.4 m; budgets and map output are unchanged.

Next use recorded factory/TF/IMU evidence and measured sensor height/base
alignment in the relevant posture, then inspect complete corrected multi-scan
windows for both support and air clearance. No automatically guessed transform,
interpolated free space, independent-accuracy result or field navigation permit
is supplied by this tool. Current ARM64 and full-product acceptance remain open.
