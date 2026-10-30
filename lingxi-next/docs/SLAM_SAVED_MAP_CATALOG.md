# Saved SLAM Map Management

Date: 2026-10-29. This connects the existing mapping worker's atomic ready
receipt to the paired management API and the shared web/landscape UI.
It does not connect live start/save controls or provide localization/navigation.

## Configuration

The optional `LINGXI_SAVED_SLAM_DIR` is an absolute, direct local directory.
Its immediate child directories contain the unmodified `MappingSession`
outputs: `map-ready.json`, `map/` and `private-saved-points.f64`. For the
preserved recorded worker result, select the parent of `saved-map/`.
There is no automatic search of vendor directories, no copy into AIMDK and
no migration or mutation of these map files. Without this setting, existing
management behavior is unchanged.

The catalog accepts schema-1 and schema-2 receipts only after explicit save and
independent process reload. Version 2 additionally requires exact sensor and
configuration provenance; version 1 cannot carry that field. See
[Map Sensor Provenance](SLAM_MAP_PROVENANCE.md) for the new format and limits.
It checks consistent frame/submap counts, point-file length,
all map artifact sizes and SHA-256 inventory, and the complete saved-point hash.
The inventory encoding matches the existing Python `inventory_map` routine.
Incomplete, changed, indirect and duplicate-session entries are excluded.
The directory itself being unavailable is reported separately in state;
ordinary map and management routes remain usable.

This bounded diagnostic catalog permits 32 session directories, 4,096 map
files and 64 MiB of map artifacts per entry, and two million saved points.
It is not an unrestricted production map archive. Listing is asynchronous
and simultaneous list requests share a scan.

## API And UI

Both new routes require the existing paired session:

- `GET /api/v1/slam/maps`: metadata only; no filesystem paths or point arrays.
- `GET /api/v1/slam/maps/:id/points`: fresh integrity check and deterministic
  preview of at most 20,000 points; every original coordinate is checked for
  finiteness, including coordinates omitted from the preview.

Paired state adds `savedSlamMaps` and `savedSlamCatalogStatus` only when
configured. These records have their own type and are never inserted into
the navigable `maps` database or selected as the active AIMDK map. Normal map
activation and point-creation APIs cannot use a catalog-only SLAM map.

Map management displays live/recorded/synthetic provenance and a saved
point-cloud preview with zoom, fit and pan. It does not open a live LiDAR
WebSocket for that preview, render a grid, invent a robot pose, or apply live
freshness rules to a static map. The scale is explicitly `raw`, not metres:
calibration/base alignment and physical accuracy remain unvalidated.
All localization, navigation and geometric-accuracy readiness flags remain
false, even when file integrity and serialization reload pass.

## Verification Scope

Fifteen synthetic catalog/API tests cover inventory compatibility, immutable
reads, provenance, incomplete and duplicate receipts, readiness promotion
rejection, changed/truncated artifacts, point bounds and non-finite omitted
coordinates, linked directories, authentication, database separation and a
missing optional directory.

The complete Vitest regression passes: 15 files, 170 tests. Frontend/API/runtime
type checks, the web production build and the bundled API build pass.
An isolated `agent-browser` check of the Vite UI with the paired synthetic API
passes at 1440x900 and 844x390: the saved canvas is nonblank, zoom changes its
pixels, fit and return work, neither viewport overflows horizontally, and
static preview opens no live LiDAR socket. Returning to live mapping still
receives/draws the existing simulated LaserScan stream. Screenshots and the
local check report are ignored artifacts, not physical robot evidence.

These fixtures are explicitly synthetic and are not native-loadable GLIM
maps. The prior actual native save/reload evidence is unchanged; this change
does not constitute a new ARM64, live sensor, Android APK or robot trial.
The product/APK version remains 0.1.19. Subsequent recorded lifecycle controls
are documented in `SLAM_MAPPING_CONTROL.md`. Live mapping lifecycle integration,
long-session mapping/loop closure, localization and navigation remain open.
