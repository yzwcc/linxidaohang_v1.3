# Isolated AIMDK Action Compatibility Mode

Updated 2026-11-03. This is an offline-verified compatibility milestone, not
physical action acceptance or a robot deployment.

## Scope

`robot-api/src/aimdk_ros_bridge.py --mode actions-only` constructs only the
available AIMDK action service clients. It does not create:

- `/integrated_command` or `/relocalization_pose` publishers;
- a `GetStoredMapByName` client;
- PNC request publishers or navigation/state subscriptions;
- LiDAR, map or odometry subscriptions, a TF buffer or a TF listener.

The action service types are discovered individually. Missing vendor SLAM,
navigation message types, standard mapping message packages or an unrelated
action service do not prevent an available `PlayEmoji` service from loading.
The default `full` mode preserves the existing native adapter; its ROS mapping
message packages remain required. A missing map service is reported unavailable
instead of crashing a status query.

## Readiness And Admission

The loopback health and authenticated state responses identify `bridgeMode`
and `physicalActionsEnabled`. State also reports each action service's
availability separately from permission to execute it.

In `actions-only` mode:

- State and preset completion queries remain available; LinkCraft catalog
  refreshes are read-only and bounded by the existing freshness rules.
- Allowlisted expressions retain the existing result checks and persistent
  uncertainty/busy interlock. No expression is sent during startup.
- Preset motions, motion-source registration and LinkCraft execution return
  HTTP 409 without sending a service request. Selectable motion/resource lists
  remain empty even when the corresponding service is online.
- Vendor map, mapping, LiDAR and navigation routes return HTTP 404 before
  reading the request body, accessing a map catalog or mutating operation state.
  Direct adapter entry points also reject these operations.
- Restart does not clear a persisted action uncertainty or pending LinkCraft
  completion. The existing explicit operator-confirmation rules still apply.

Physical actions intentionally remain unadmitted: this isolated service cannot
infer whether the independent GLIM/Nav2 owner is mapping, localizing, navigating
or still retiring a prior operation. That owner must supply a coherent,
generation-bound lifecycle interlock before the product delegates real motion.
Do not use `full` mode to circumvent this missing independent integration.
The independent Web/Android product does not select this adapter yet.

## Deployment Preparation

`deploy/run.sh actions` explicitly selects `--mode actions-only` after checking
the independent-product flag, installed sources and the configured ROS setup.
It uses the existing internal token, loopback port and private interlock file;
no credentials are added to source, browser storage or a release archive.

The optional `lingxi-next-actions.service` conflicts with the full AIMDK bridge
to avoid two controllers claiming the same port/interlock. Installation copies
the unit but does not enable or start it. The upgrade/rollback transaction
includes the new unit and never automatically resumes it after a failure.
Diagnostics include its service state. No existing 0.1.22 archive or APK was
rebuilt or relabeled for this milestone.

## Offline Verification

- `python -B -m unittest robot-api/tests/test_aimdk_bridge.py -q`:
  52 tests, including 13 action-mode tests, passed with synthetic ROS types and
  service responses. No ROS runtime, sensor or robot is contacted.
- `python3 tools/test-deploy-linux.py`: two real installed-entrypoint and
  lifecycle tests passed in a WSL private mount namespace and disposable
  chroot. File operations, checksums, certificates and backups are real;
  systemd, ROS/Python bridge execution and hardware responses are substitutes.
- `python -B -m unittest discover -s tools/tests -p test_release_scope.py -q`:
  seven release-scope checks passed.
- Full Vitest: 645 passed, 24 skipped. TypeScript frontend/API/runtime checks
  and the production Vite build passed.

These results do not establish actual AIMDK service presence, ARM64 execution,
safe preset completion, cloud/acoustic performance or product acceptance.
