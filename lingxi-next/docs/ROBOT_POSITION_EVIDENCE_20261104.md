# Robot Position Evidence

Status: offline product correction, not physical localization acceptance.

## Product Behavior

Spoken current robot coordinates require a connected, non-demo adapter, finite
coordinates, a ready odometry check, and producer evidence no more than two
seconds old. Future timestamps, synthetic navigation inputs, unavailable
evidence, and declared-but-unsurveyed units cannot produce a spoken metre claim.
Questions such as `你在哪` follow the same local evidence path, without a model
or knowledge retrieval fallback. Snapshot polling time is not sensor time.

`RobotState.poseEvidence` accompanies an available pose:

- `source`: `aimdk-map-odometry` or `independent-localization`.
- `observedAtMs`: producer observation time in Unix milliseconds.
- `coordinateUnits`: `metres` or `declared-metres`.

The native AIMDK callback validates map-frame odometry and preserves the ROS
message's age when converting to Unix time. Old or future messages are not made
current by their arrival. Pending map relocalization and backward wall-clock
steps suppress the native pose and its evidence. The actions-only bridge has
neither pose nor pose evidence.

The independent bridge exposes optional `poseObservedAtMs` on protocol 1 state.
It is derived from the observation's monotonic age, not the state request time.
Default, expired, future, closed, or localization/TF-invalid observations have
no current pose evidence. A map handoff clears the observation. Older bridge
responses without the new field remain readable but cannot expose a current
product pose.

The physical adapter never falls back to delegate coordinates after bridge
failure. Uncertain bridge sessions, stale observations, and invalid localization
or TF suppress the product pose and withdraw odometry readiness. Terrain and
other navigation gates do not by themselves invalidate a current localization;
they continue to block navigation independently.

Current independent geometry is explicitly `declared-metres`. It can be used
for labeled diagnostics, but this change does not certify physical scale or
enable spoken metre coordinates. A future measured calibration admission must
provide its own verified metric provenance; changing this display label is not
a calibration procedure.

## Verification

Offline tests exercise voice refusal and fresh native answers, streamed refusal,
native HTTP-to-voice propagation, product API evidence forwarding, bridge loss
and uncertainty, observation age, future timestamps, actions-only isolation, and
independent localization without terrain admission. Linux bridge tests exercise
the actual same-user Unix-socket path using synthetic observations.

No robot command, sensor subscription, installation, or physical-motion test was
performed for this correction. Map scale, supporting-ground calibration, ARM64
target checks, and field voice/localization acceptance remain separate pending
work. Existing point-cloud rendering and the default Z band of 0.1-1.4 m are
unchanged.
