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

## Broad Python Regression: Not Green

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

## Still Not Accepted

`physicalMotionSent=false` and `navigationReady=false` are retained. Playback
is stubbed and cloud access is disabled; neither an audible <=2 s result nor
an acoustic wake-word result is claimed. Recorded localization plus observed
occupancy, support-preserving obstacle clearing, guarded AIMDK handoff, current
APK, and complete independent-stack install/upgrade/rollback verification remain
tracked in [the full-product workstream](OFFLINE_PRODUCT_ACCEPTANCE.md).
