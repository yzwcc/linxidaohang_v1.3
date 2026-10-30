# Map Sensor Provenance

Date: 2026-10-30. Newly controlled mapping sessions bind saved maps to the
effective sensor profile, scan timing hypothesis and complete configuration
file inventory. This is byte-level association, not physical calibration,
current-site identity, localization accuracy or navigation acceptance.
The product and APK version remain 0.1.19.

## Contract

The atomic `map-ready.json` receipt now uses schema version 2 when the mapper
has a provenance verifier. Its additional `sensorProvenance` object contains:

- `schemaVersion`: exactly 1 for the provenance object.
- `profileSha256`: SHA-256 of the actual input profile bytes.
- `timingHypothesis`: explicitly `header_is_scan_start` or `header_is_scan_end`.
- `configurationSha256`: SHA-256 for each file in the effective configuration
  directory, with 1-32 direct files and bounded portable filenames.

The mapping session checks these inputs at initialization, before native save
and after independent-process reload. The reload must report the same
provenance. A mismatch prevents publication of the ready receipt; it does not
silently adopt changed settings. The verifier and provenance must be supplied
together. The existing stop/discard and worker ownership rules are unchanged.

The catalog strictly validates both receipt versions and exposes version-2
provenance as metadata. Controlled, managed, recorded and live localization
entry points compare version-2 provenance against the current platform inputs.
Missing provenance, changed configuration, a different timing hypothesis or a
different profile is rejected. The recorded profile digest must also match the
actual verified profile file, even if caller and receipt agree with one another.

Version-1 receipts remain available for historical diagnostic use. They are not
rewritten, backfilled or upgraded, and a version-1 receipt carrying provenance
is rejected. Older replay tools may still create version-1 diagnostic receipts.
All readiness and `mapProfileAssociationVerified` flags remain false: neither
format proves sensor-to-base extrinsics, physical units or the current site.

## Native Recorded Verification

The new result is [the aggregate evidence](slam-map-provenance-evidence.json).
The test uses existing captured input on isolated Linux x86-64 with the actual
unchanged GLIM native libraries. It does not contact the robot or send motion
commands. Private maps, input paths, profile/configuration digests and logs are
excluded from the public aggregate.

| Check | Result |
| --- | --- |
| Save and discard sessions | Each completed 4,144 messages, 196 scans and 165 poses |
| Saved map | 165 frames, 1 submap, 2,655 points; exactly one ready receipt |
| Fresh native reload | Matching version-2 provenance; maximum point displacement about 4.92e-7 raw units, below the predeclared 1e-4 tolerance |
| Localization loading | All 2,655 points loaded with matching current provenance |
| Negative checks | Missing provenance, changed hypothesis, configuration and profile all rejected |
| Cleanup | Native create/close once per child, service exit 0, control socket removed |

The initial trial completed native save/reload but failed the newly added final
check because it tried to construct a native platform in the test coordinator,
outside the required child library environment. The coordinator now independently
hashes the explicit recorded inputs without loading native libraries; production
environment checks are unchanged. That failed run is retained privately. A new
output directory was used for the complete passing rerun.

All 145 related Linux Python tests pass (76 focused tests and 69 wider mapping
regressions), covering initialization/save/reload changes, invalid
provenance, actual file changes, loader/profile mismatches and map management
contracts. An initial wider-test invocation had a sibling fixture import error;
the corrected test-directory invocation passed all 69 tests without code changes.
Frontend/API/runtime type checks, the production web build and all
three API/mapping/localization bundles pass. Vitest: 260 passed, 9 platform-only
tests skipped. The upstream Zod pure-annotation build warnings remain non-fatal.

This is not a new ARM64 or live-sensor trial. Previous ARM64 live-localization
evidence retains its original source identity. Next hardware work should group
ARM64 version-2 save/reload, sensor/base-frame checks and a known-seed tracking
trial instead of repeating already completed passive input checks.
