# Offline Workspace Verification: 2026-11-01

Scope: current product API and Web console connected to actual isolated ROS
Humble Nav2, using synthetic planar inputs. No robot connection, sensor capture,
physical command transport, deployment or field acceptance occurred in this run.

## Passed Checks

| Check | Result | Boundary |
| --- | --- | --- |
| `npm run build` | TypeScript and Vite build passed | Not an Android APK build |
| `npm test` | 479 passed, 16 skipped | Environment-dependent tests are not counted as passes |
| Opt-in `offline-nav2-integration.test.ts` | 2 passed, 57.39 s | Actual isolated Nav2, not a physical robot |
| `test_offline_nav2_service.py` | 5 passed | Protocol and lifecycle unit tests, without ROS |
| `test_nav2_evidence.py` | 1 passed | Current source hashes and new four-scene receipt verified |
| Web workflows | Pair, create points, navigate by detour, pause, resume and stop passed | Synthetic point cloud and pose only |
| Web layouts | 1280x720 desktop and 960x540 landscape inspected | Nonblank canvas, no horizontal overflow or overlapping text observed |

The actual Nav2 product integration covered a frozen paused pose, output
retirement, a new task epoch on resume, authenticated wake/recognition command
dispatch, stop, evidence-insufficient Q&A without cancelling navigation, and a
two-point tour that did not automatically return home. A separate integration
completed a detour while polling scene/state concurrently as the Web does.

The four controller scenes (open, blocked, detour, appearing wall) were rerun
after disabling proportional cross-axis scaling in the velocity smoother.
Per-axis limits and strict runtime validation were retained. Exact source and
report hashes are in [the new receipt](evidence/nav2-offline-rpp-20261101.json).
The 2026-10-31 receipt was not rewritten.

Browser screenshots are local ignored artifacts named
`offline-nav2-desktop-20261101.png` and
`offline-nav2-landscape-20261101.png`. Canvas checks observed 1645 changed pixels
in the 309x410 desktop canvas and 2068 in the 670x313 landscape canvas. These
checks establish rendering, not point-cloud geometry or field-map accuracy.

## Earlier Broad Python Regression: Not Green

The ordinary-user WSL scientific runtime ran `unittest discover -s tools/tests
-q`: 1376 tests, 4 failures, 2 errors, 150 skipped, 169.938 s. The six outstanding
checks are:

- `test_arm64_python_runtime`: historical checked-source digest differs for
  `seeded_localization.py`.
- `test_shared_slam_live_package`: historical package source digest differs.
- `test_slam_base_pose_evidence`: historical `seeded_localization.py` digest
  differs.
- `test_slam_point_composition`: historical `process_protocol.py` digest differs.
- `test_base_pose_tf2_target`: historical source-bound TF2 report is rejected.
- `test_slam_glim_config`: the local pinned upstream cache is reported dirty by
  WSL Git, so the clean-source precondition rejects it.

An earlier baseline exercise reproduced five historical receipt failures;
this run does not establish baseline provenance for all six current failures.
No historical evidence digest was changed to make a test pass. The point
composition test now reads this milestone's changed `jobs.ts` from its already
frozen historical revision, like its other previously changed product files;
that does not establish a new hardware result. Source/package evidence needs
its own reconciliation or fresh bounded verification before release.

## Historical Reconciliation and Current Regression

A subsequent ordinary-user WSL run used
`/home/admin1/.cache/lingxi-next/continuous-localization-env-20261030-a/bin/python`
with the same `unittest discover -s tools/tests -q` command. Result: **1423 tests,
1372 passed, 51 skipped, zero failures/errors**, 211.191 s. This scientific
environment enables more optional tests than the earlier environment; skipped
tests are not passes. Eleven new isolated-Git tests exercise the history audit,
including tampering, missing history, byte transformations, symlinks, source
deletion and strict-CLI refusal.

The same workspace also reran `npm test` (502 passed, 16 skipped) and
`npm run build` (Web, API and runtime TypeScript checks plus Vite passed).
Rollup reported two nonfatal upstream Zod comment-annotation warnings.

The five historical reports now have full immutable Git revisions and canonical
JSON identities in `evidence/historical-source-bindings.json`. The audit checks
the reports against those revisions and verifies all **93** recorded source
associations against exact Git blob bytes. One documented LF-to-CRLF source
transformation is explicit in the catalog; no generic normalization or report
rewriting occurs. Current source bytes are checked separately. Fifteen distinct
current sources differ from their historical records; none has gained new ARM64
acceptance from this change. Matching a history binding is not a runtime test.

Run from `lingxi-next/` with full project Git history available:

```sh
python tools/audit_historical_evidence.py
python tools/audit_historical_evidence.py --require-current
```

The first command verifies historical integrity and reports current drift. The
second was actually executed and returned **2**, blocking current-source identity
approval. Missing Git history and altered historical reports also fail closed.
All five acceptance/contact/deployment flags remain false. The existing package
builder still refuses non-commissioning releases regardless of this audit.

The sixth earlier failure came from a shared Windows/WSL GLIM cache checkout:
Windows had CRLF working bytes but WSL Git did not apply the checkout's EOL
policy. Setting that ignored cache's local `core.autocrlf=true` made the checked
`config` and `LICENSE` paths clean. No cached source bytes were rewritten and the
production clean-source guard was not relaxed.

## Still Not Accepted

`physicalMotionSent=false` and `navigationReady=false` are retained. Playback
is stubbed and cloud access is disabled; neither an audible <=2 s result nor
an acoustic wake-word result is claimed. Recorded localization plus observed
occupancy, support-preserving obstacle clearing, guarded AIMDK handoff, current
APK device acceptance, and complete independent-stack install/upgrade/rollback verification remain
tracked in [the full-product workstream](OFFLINE_PRODUCT_ACCEPTANCE.md).
