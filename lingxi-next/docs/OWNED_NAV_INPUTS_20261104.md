# Owned Localization Inputs for Nav2

Status: offline commissioning integration, not robot acceptance. No AIMDK motion is
sent by these tests. Map scale, ground support, navigation readiness, sensor
calibration, and safe physical velocity limits remain unvalidated.

## Selection

`LINGXI_NAVIGATION_INPUTS=explicit` remains the default and keeps the existing
explicit topic/frame configuration. To use the paired live saved-map output,
set `LINGXI_NAVIGATION_INPUTS=owned-localization`. In that mode clear all eight
manual values: `LINGXI_MAP_FRAME`, `LINGXI_BASE_FRAME`, `LINGXI_SENSOR_FRAME`,
`LINGXI_POINTCLOUD_TOPIC`, `LINGXI_ODOMETRY_TOPIC`, `LINGXI_TERRAIN_TOPIC`,
`LINGXI_TF_TOPIC`, and `LINGXI_TF_STATIC_TOPIC`. The process rejects a mixed
configuration instead of silently choosing either source.

The selected localization manager must have its verified acquisition sidecar
configured for paired `private_saved_map_observations` and
`private_saved_map_terrain`. No live producer is created just because the
navigation bridge starts. A current tracking snapshot and an explicit map
activation are still required before a Nav2 generation can be prepared.

## Session Binding

The worker exposes a private `navigation_snapshot` over its existing same-user
Unix socket. This combines the normal localization state with a read-only,
strict-schema descriptor. The public `state` response is unchanged. The manager
also checks its service/run identity and lifecycle before returning the paired
snapshot.

Both saved-map publishers must be running, not revoked, and share the same
random `lingxi_offline_<32 lowercase hex>` namespace. Only fixed suffixes are
derived from that namespace:

- `<namespace>/points`
- `<namespace>/odometry`
- `<namespace>/tf`
- `<namespace>/tf_static`
- `<namespace>/traversable_map`

Frames stay fixed at `lingxi_saved_map`, `base_link`, and `lidar_chest_front`.
Neither the client nor the descriptor can select arbitrary ROS names, velocity
topics, or motion interfaces. Session, worker-generation, map, or namespace
replacement withdraws the current localization authorization; a replacement
requires explicit map activation before a new producer binds to it.

These inputs are connectivity, not calibration: all three descriptor
admission flags (`physicalScaleValidated`, `groundSupportValidated`, and
`navigationReady`) must remain false. Existing exact-time sensor checks and
freshness limits are unchanged. Missing or stale inputs keep Nav2 unprepared;
the bridge does not fall back to manually guessed topics.

## Verification

Offline checks cover descriptor validation, acquisition-owner pairing, worker
and manager Unix-socket contracts, map-bound profile construction, namespace
replacement, and the existing production Nav2 launch using synthetic publishers
inside an isolated loopback-only Humble root filesystem. The owned-topic Nav2
integration test passed once in that isolated Humble environment on 2026-11-04.
This does not replace
ARM64 installation checks or robot-side topic, TF, calibration, stop-receipt,
short-distance, obstacle, or operator acceptance.
