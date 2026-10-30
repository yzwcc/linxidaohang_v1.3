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

That x86-64 run is historical; its original source identity is retained.

## ARM64 Save, Reload And Recorded Seed Check

The subsequent [ARM64 aggregate](slam-map-provenance-arm64-evidence.json) uses
the actual robot CPU and preserved input at 1x speed. SSH identity was checked
against the previously known robot. Existing native libraries, recordings,
Node dependencies and the isolated localization environment were reused.
No vendor installation, system service or old evidence bundle was replaced.
There were no live subscriptions or movement commands in this trial.

| Check | ARM64 Result |
| --- | --- |
| Save and discard | Each completed 4,144 messages, 196 scans and 165 poses |
| Fresh native reload | 165 frames, 1 submap, 2,655 points; matching schema-v2 provenance |
| Round trip | Maximum displacement 4.9177143e-7 raw units, below the existing 1e-4 tolerance |
| Point delivery | Paired WebSocket, two reconnects, no demo fallback, cleared after stop |
| Localization input | The same newly saved ARM64 map, matching profile/configuration and preserved diagnostic seed |
| HTTPS lifecycle | Seed at reference frame 1, tracking observed at frame 4, explicit pause/stop, stale generation rejected |
| Cleanup | 557 stopped-state queries; native and API exit 0 without forced termination |

The recorded seed is derived from the same input, not an independent surveyed
pose. The check pauses after observing tracking; it is not a long-duration
tracking or accuracy benchmark. The raw-unit round-trip tolerance measures map
serialization only, not metres of physical error. Readiness flags remain false.

### Stop-Time Fix And Preserved Failures

The first complete ARM64 input run reached stop-save but was rejected. The
private worker report confirmed `sensor_arrival_deadline_exceeded`: full
binary/journal hashing ran after input stopped but before the active worker
was closed. The fix drains admitted input and stops the sensor worker first.
Its single native owner then verifies the complete frozen inputs before save
and again after fresh-process reload, before publishing the receipt. The final
terminal check remains. No freshness, provenance or timeout gate was relaxed.

Earlier staging attempts lacked the ARM64 point evidence file, and one
diagnostic change to the frozen worker was rejected by its source-integrity
guard. The staging file is now retained and the worker source is unchanged;
diagnostics live only in the outer mapping components. Exception messages and
input values are excluded from those diagnostic lines.

The first localization collection stopped before execution because a downloaded
receipt snapshot had different JSON whitespace from the remote file. The
collector compares its structured contents and binds the actual unchanged
remote byte digest for localization. All failed outputs were preserved and
passing reruns used new directories.

The final regression run covers 203 Linux Python tests, including slow
post-stop verification and changed-input rejection at both save boundaries.
Eight collector tests also pass on Windows. The localization runtime bundle
builds and runtime TypeScript checking passes. The 29 Linux-only Node owner
cases were skipped by their platform gate on Windows, not counted as passes.
No frontend or APK source changed and no new APK was produced.
An initial wider test invocation used an incorrect module name; the corrected
invocation passed. Physical sensor/base calibration, site association,
continuous live tracking, navigation and voice acceptance remain pending.

### Reusing This Verification

`tools/run_mapping_arm64_trial.py` accepts an explicit known robot identity,
an existing recorded configuration directory, a compiled paired API probe and
new source/result directories. It does not install native dependencies.
`tools/run_saved_map_arm64_localization.py` then accepts that completed private
mapping collection, a current localization API bundle and the existing
isolated localization dependency directory. It reuses the actual saved map
in place without copying or rewriting it. Both tools require explicit host
arguments, prompt for SSH credentials in memory, preserve existing outputs
and collect only private diagnostics under `.local/`. They are commissioning
tools, not installation commands or authorization for movement.
