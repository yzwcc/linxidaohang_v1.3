# Source-Backed Mapping Observations

This extension adds acquisition sources to the existing full GLIM mapper. It is
separate from the currently deployed commissioning mapper and does not retrofit
field map 1.2, change vendor software, or enable motion.

## Geometry

The private patch copies the original LIO trajectory before submapping smooths
it, and captures the scan after smoothing but before `clone_wo_points()` removes
non-keyframe raw points. Every preprocessed return retains its LiDAR XYZ and
time offset. No merged cloud or display packet is used as a visibility source.

Use the smoothed trajectory when it covers the whole scan. If a scan ends after
the next frame's timestamp, the smoothed interval can be shorter than that scan;
use the entire original, recorded LIO IMU trajectory only if it independently
covers every return. Never splice trajectories, clamp time or extrapolate. The
terminal frame similarly requires an original trajectory. The selected source
is recorded for each scan. LIO configuration must enable
`save_imu_rate_trajectory`.

For each return at acquisition time `t`, reconstruct:

```text
T_map_lidar(t) = T_map_submap * T_submap_imu_optimized *
                 inverse(T_source_imu_at_scan_start) * T_source_imu(t) * T_imu_lidar
origin = T_map_lidar(t) * [0, 0, 0, 1]
endpoint = T_map_lidar(t) * [raw_x, raw_y, raw_z, 1]
```

SciPy `Slerp` interpolates orientation and linear interpolation handles IMU
translation within measured trajectory timestamps. Both ray ends follow the
same local and global optimization corrections. Source digests exclude those
corrections; registered-geometry digests include them.

## Archive And Binding

`LXOBS002` is little-endian: a versioned header, ordered submaps with frozen
global poses, and ordered scans containing source reference/extrinsic poses,
optimized submap-local scan poses, trajectory provenance, IMU samples and raw
return XYZ/time offsets. Scan/frame order, finite rigid poses, normalized
quaternions, timestamp coverage, counts, hashes, file length and trailing bytes
are checked on reload. Current caps are 4,096 scans, 65,536 returns per scan,
16,777,216 returns total, 4,096 IMU samples per scan, 1,048,576 IMU samples
total and 768 MiB per archive.
An exceeded cap or incomplete drain does not publish an observation binding.

`ObservedMappingLibrary` decorates the existing product mapper. It exports only
after native finish/drain and validates frame/submap counts. The ordinary map
receipt remains unchanged; `observations-ready.json` is an additive binding to
its exact digest, actual map inventory, sensor/configuration provenance,
implementation digest, archive digest and reconstructed geometry digest.
Consumers must recompute the actual map inventory before `verify_observations`;
a client's claimed inventory is not evidence. Missing archives, changed maps,
changed source/poses or a different implementation do not verify.

## Reproduction And Scope

`tools/build_glim_observations.py` freezes the pinned clean GLIM source and the
existing synchronous/asynchronous adapters into a new private build directory,
applies the existing EOF patch followed by the observation patch, then builds a
separate Release-mode library. It never modifies the original pinned checkout or a deployed
library. `tools/run_observed_mapping.py` replays verified existing bags through
the real `MappingSession`, explicit save, independent-process native reload and
independent observation reconstruction, then publishes the binding.

`test_observation_archive.py` and `test_observation_binding.py` use explicitly
isolated synthetic data. `test_observation_capture_native.py` optionally compiles
and executes the real GLIM callback fixture against an explicitly selected built
library and core, including real `clone_wo_points()` retention and corrections.
The native fixture does not construct sensor subscribers or actuator interfaces.
The geometry fixtures also cover a nonidentity LiDAR-from-IMU rotation and lever
arm, including its inverse and its composition with the acquisition reference.

This extension still emits **native raw map coordinates**, not an approved
metric/ground frame. It does not establish ground support, drop-off clearance,
footprint inflation, moving localization, local avoidance or stopping semantics.
The current OctoMap `ObservationBatch` remains a separate metric contract; these
archives cannot be relabeled as metres merely by supplying a digest. ARM64
execution, live package wiring and calibrated real-map occupancy integration
remain required before a new field scan or navigation acceptance.

## Verified Offline Milestone

On 2026-10-31, the final Release x86-64 library passed 19 acquisition/archive,
binding and actual native-callback tests with no skips. Two verified existing
recordings then completed the real product save and independent-process native
map/observation reload with matching geometry digests:

| Recorded Case | Saved Scans | Preserved Returns | Submaps | Smoothed / Original LIO |
| --- | ---: | ---: | ---: | ---: |
| Static, scan-start hypothesis | 313 | 2,012,279 | 1 | 312 / 1 |
| Moving, scan-start hypothesis | 1,190 | 7,709,051 | 2 | 1,183 / 7 |

Both runs drained all accepted frames and IMU inputs with zero rejected tasks
and zero discarded inputs. Moving replay peak memory was 673.8 MiB and maximum
mapping-worker queue wait was 1.53 seconds. These are offline-host observations,
not robot latency or field accuracy acceptance. Native map reload maximum point
difference was below 0.0000011 raw units in both cases, below the existing
0.0001 round-trip tolerance. No coordinate data or archives are published.

- Native adapter SHA-256:
  `c307ebeffc357da27b52f6ba8d59ed660d1ad1bccca0207bf40d8bc948bc33e1`.
- Frozen build report SHA-256:
  `c2383f8fb34c1f17c22651271ff16369f8e8e9b828e375ee003ca5aab7c264a8`.
- Source/runner implementation digest shared by both trials:
  `f47236fc1d339099203e3af96b016206e44691e83eda131c22eebaff5626ce0b`.

Earlier incomplete-trajectory and unoptimized queue-overflow trials were kept
privately and are not counted as successful trials. Final builds retain the
pinned upstream signedness warnings; the new native capture source compiles
without warnings. The currently deployed Web and saved map 1.2 were unchanged.

## Regression Status

The broader Python run completed 1,289 tests with four failures, one error and
143 skips; it is not a passing full regression. All five failing tests compare
historical ARM64/base-pose reports with current source identities. The same
failures and hashes were reproduced without this extension in an independent
export of parent commit `5eb885597260ed39a107d848e84aee0637a57a07`, preserving
the existing checkout's unchanged tracked bytes. Historical reports were not
rewritten and their tests were not relaxed. Current-source ARM64 evidence and
historical-report version handling still need separate follow-up.
