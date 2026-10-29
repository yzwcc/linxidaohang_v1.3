# Mapping Input And Same-Engine Points

Date: 2026-10-29. This is a local integration milestone, not a robot deployment,
live DDS acceptance, localization or independent navigation.
The web assets and Android APK were not changed or rebuilt.

## Implemented

- `SensorAdmission` closes the callback gate atomically with any in-progress
  message snapshot. Late callbacks cannot enter a retired session. Accepted
  callbacks drain within 0.5 seconds; the existing worker/stream watchdogs and
  native ownership remain unchanged.
- `MappingInput` shares that gate between recorded and passive input. Only
  an explicit requested stop may save. Input failure, cancellation, expired
  sessions and stop-before-start discard instead. This helper does not
  authenticate commands; the existing paired API and process owner still do.
- The recorded management child uses the shared lifecycle rather than a
  second hand-written queue-drain loop.
- `run_passive` optionally accepts a matching admission and completion callback.
  It closes admission, attempts all ROS cleanup, then finalizes mapping.
  Cleanup failure reaches the callback as `input_failed`; the worker is also
  stopped when the callback raises. The ordinary passive-only path retains
  its native-before-ROS shutdown order and minimal bundle dependencies.
- An injected `PointOutputNative` decorates the same odometry instance already
  owned by `MappingWorker`. Odometry still initializes before the mapper.
  Point output closes before map flush; fresh-process reload and the atomic
  ready receipt remain owned by the existing mapping session.

## Native Validation

`tools/run_mapping_input_trial.py` uses preserved CDR callbacks, a deliberately
synthetic subscription boundary and the actual unchanged Linux x86-64 GLIM
libraries. It does not load installed ROS, join DDS or contact a robot.
Its controller and encoded points both remain labeled `recorded`.
It is not a simulation being presented as a live sensor trial.

The final source-bound save and discard runs each completed:

| Observation | Count |
| --- | ---: |
| Accepted and completed callbacks | 4,144 |
| Processed scans | 196 |
| Estimated poses | 165 |
| Corrected point packets | 164 |
| Native creation / close | 1 / 1 |

The first initialization pose has no corrected point packet, as required by
the existing extension contract. Each packet passes the existing session-,
mode-, frame- and freshness-checked encoder/decoder. Only two point packets are
retained by the diagnostic summary. No asynchronous pipe, API connection,
browser canvas or sensor-to-display latency is claimed by this trial.

The save contains 165 frames, one submap and 2,655 points. Separate-process
reload compares every saved point within the existing `1e-4` raw-unit
serialization tolerance. Discard creates no ready map. Both runs retire their
native instance once on a single non-main thread, without rejected/discarded
callbacks or a surviving worker thread. Aggregate results and current source
hashes are in `slam-mapping-input-evidence.json`; recordings, coordinates,
session IDs and private logs stay outside Git.

The management service/API save-and-discard trial was also repeated against
the new shared admission code. Its aggregate remains in
`slam-mapping-control-evidence.json`. Historical ARM64 evidence is unchanged
and is not proof that this revision ran on ARM64.

## Checks

- Final Linux tools regression: 820 tests, 816 passed and four explicit skips.
  The 33 focused admission/mapping/passive/aggregate tests all pass.
- Existing frontend/API Vitest regression: 187 passed.
- Actual unchanged GLIM libraries passed both same-engine save/discard runs
  and the repeated private management service/API trial. The service exited
  zero, cleaned its processes and removed its Unix socket.
- The first full Linux run rejected the default optional upstream checkout
  because it was already modified. Nothing in that checkout was reset or
  edited. Setting `LINGXI_GLIM_UPSTREAM_ROOT` to the existing pinned Linux
  cache passed its 15 focused tests and the subsequent complete regression.

## Remaining Mainline

The persistent management API still needs a session-switching private point
transport; its existing inherited point FD is single-session. The paired
recorded mapping service does not yet expose this trial's point stream.
A fresh ARM64 composition and onsite live validation are also outstanding.

This does not remove the diagnostic native mapper's short-sequence/loop-travel
restriction. Base-frame alignment, calibration/timing validation, long-session
mapping/loop closure, localization, independent navigation, voice concurrency
and final robot/APK delivery remain unaccepted. Serialization success must
never set localization or navigation readiness.
