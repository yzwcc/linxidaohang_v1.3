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

Physical actions intentionally remain unadmitted. The operation lease described
below adds cross-process exclusion for expressions, not generation-bound physical
action completion. Preset/LinkCraft delegation still requires independent
completion evidence. Do not select `full` mode to bypass this restriction.
The independent Web/Android product can now explicitly select this adapter for
allowlisted expressions and operator idle confirmation only. No action client
is constructed without the pinned optional `actionsBridgeEndpoint` selection.
The API checks bridge mode, epoch, sequence and observation time. Requests and
acknowledgements bind the same process epoch and a unique request ID; a lost or
invalid expression acknowledgement preserves local uncertainty until an
operator confirms idle. Startup never sends an expression. Physical motions and
LinkCraft resources remain rejected at both product and bridge boundaries.

The paired HTTPS `POST /api/v1/actions/expression` route accepts only
`{"name":"<configured expression>"}` with no extra fields. It is available only
with the explicitly selected independent actions backend. Its HTTP 202 response
is `{"accepted":true,"completed":false}`, never a completion claim. Unknown
expressions, mapping, active/paused navigation, unstable or unconfirmed localization,
missing pairing and plaintext requests do not dispatch an expression. A stable,
current-generation localization tracker may coexist with a face expression at a
stopped tour point. This is a management API;
no new Web control or Android build is included in this increment.

Tour expressions now carry both job identity and interaction revision through
preflight and the final write boundary. Only a typed local pre-write revocation
can wait for resume/quiet and be attempted again; remote errors and invalid
acknowledgements never qualify. See the separate
[tour-expression verification](TOUR_EXPRESSION_VERIFICATION_20261103.md).
These revocation checks remain in force with the face-display exception below.

Product mutations share fail-fast API ownership, not a queue. Localization
start/seed/pause participate in that ownership and cannot overlap a pending
expression or active mapping/navigation. Mapping and map activation still require
localization cleanup. Expressions may coexist only with a current runtime
whose selected map matches a validated worker-reported `tracking` state. The
same runtime identity is required before and after reading the worker; its
process generation is independent of the worker's reset generation. Starting,
stopping, lost, paused, mismatched or unreadable localization blocks expressions;
terminal runtimes permit expressions only after confirmed cleanup. Active localization is not
reported as a physical action and does not veto the separately guarded Nav2
navigation path. Stop/shutdown remain recovery paths.

## Cross-Process Operation Lease

The product entry points now use a second stable lock file at
`LINGXI_MAP_ROOT/.robot-operation.lock`. The existing `.slam-activity.lock`
continues to exclude concurrent mapping and localization sensor owners.

| Operation | Operation Lease | Release |
| --- | --- | --- |
| Mapping | Exclusive | Confirmed native process/monitor cleanup |
| Localization | Shared | Confirmed native process/monitor cleanup |
| Nav2 preparation and active generation | Shared | Generation cleanup plus output retirement proof |
| Allowlisted face expression dispatch | Shared | Service acknowledgement or bounded dispatch failure |

All acquisitions are nonblocking. Mapping keeps the exclusive lease; localization,
Nav2 and allowlisted face expressions use shared leases. Thus expression dispatch
can run alongside the saved-map localization sensor process, while mapping still
blocks it before any AIMDK write. The product API separately refuses expressions
while Nav2 is requested, running, paused or not confirmed quiescent. Missing or
invalid catalog configuration or an unavailable lease component returns 503,
with no fallback. No old request is queued. An expression acknowledgement is
not physical completion; the existing persistent uncertain-dispatch interlock
remains in force.

`PlayEmoji` is the AIMDK face-screen expression service, not a joint, preset-body
motion or base-navigation service. See the [official X2 screen-control
documentation](https://x2-aimdk.agibot.com/zh-cn/dev/Interface/interactor/screen.html).
Its priority can replace other face/video display content, so this exception
only authorizes the configured face expressions; it does not authorize preset
motions, LinkCraft resources or movement. The existing tour-generation and
interaction-revision checks still apply at the write boundary.

`ProcessSupervisor` passes both lease descriptors only to the owned native child;
the fresh map-reload subprocess preserves both. Closing a parent's descriptor
does not unlock an inherited descriptor. Files are owned private single-link
regular files, and are never deleted or replaced by cleanup or deployment.

Each production Nav2 generation acquires its shared lease before creating its
ROS client or launching Nav2. The launch guard inherits the descriptor and watches
a bridge-owned lifetime pipe. Bridge exit closes that pipe; the guard terminates
its same-process-group launch children, including TERM-ignoring descendants,
before releasing its descriptor. Normal retirement keeps the parent's lease
until both process cleanup and output retirement are observed. A failed output
fence retains the lease even when process cleanup has finished.

This coordinates cooperating product services running as the same ordinary
Linux user against the same private catalog. It is not a robot-wide motion lock,
an emergency stop, or exclusion of vendor/standalone processes using another
directory. The same-process-group lifetime contract must still be verified with
the actual target ROS launch tree. No physical preset or LinkCraft action was
enabled by this change.

## Deployment Preparation

`deploy/run.sh actions` explicitly selects `--mode actions-only` after checking
the independent-product flag, installed sources, configured ROS setup and map
catalog. It loads the installed `robot-slam` Python package for the shared lease.
It uses the existing internal token, loopback port and private interlock file;
no credentials are added to source, browser storage or a release archive.

The optional `lingxi-next-actions.service` conflicts with the full AIMDK bridge
to avoid two controllers claiming the same port/interlock. Installation copies
the unit but does not enable or start it. The upgrade/rollback transaction
includes the new unit and never automatically resumes it after a failure.
Diagnostics include its service state. No existing 0.1.22 archive or APK was
rebuilt or relabeled for this milestone.

## Cross-Process Lease Verification (2026-11-03)

This lease change was validated offline without connecting to the robot:

- Linux ordinary-user `test_slam_activity_lease.py`: 11 tests passed, including
  cross-process shared/exclusive contention, inherited SLAM + operation FDs,
  Nav2 guard parent death and TERM-ignoring descendant cleanup.
- Linux ordinary-user focused process/runtime batch before the final shutdown
  proof case: 185 tests total, 175 passed and 10 skipped. This snapshot included lease,
  supervisor, mapping/localization managers, Nav2, workspace, release scope and
  live-acquisition tests. The Nav2 producer suite subsequently passed again on
  Windows with 25 tests after adding shutdown-retirement coverage. The final
  Linux lease/supervisor/mapping/localization subset passed all 91 tests.
- Python AIMDK bridge on Windows: 54 total, 53 passed and 1 Linux-only case skipped. This
  includes expression lock contention with zero service dispatch and confirmed
  lease release after the service callback.
- Vitest API/product/navigation/config subset: 76 passed, 7 skipped. Runtime
  TypeScript checks and the compiled SLAM API entrypoint build passed.
- Linux isolated deployment install/upgrade/rollback: 2 passed. Release scope:
  7 passed. Shell/Python diff whitespace checks passed.

The additional live paired-workspace run used the compiled API and WSL against
the repository on the Windows-mounted `/mnt/e` filesystem. It did **not** pass:
two workspace assertions failed, one reporting a listener deadline and another
reporting no startup readiness although service logs were empty. A separate Nav2
test's initial failure was attributed to PID reuse without confirming that cause.
Its intermediate comparison was also too weak: a live process changing scheduler
state could pass. The corrected assertion is described below. The earlier run with an
incorrect WSL bundle path had 10 module-not-found failures and is superseded.
These mounted-filesystem results are not evidence of successful end-to-end
integration; the native-filesystem rerun is recorded below. No sensor, ROS hardware, robot or
motion command was used. Actual Humble Nav2 launch-tree behavior, ARM64 service
startup and robot output-retirement feedback remain unverified.

## Native Workspace Recheck And Single Expressions (2026-11-03)

The current tracked baseline was extracted to WSL's native Linux filesystem,
with its own lockfile-installed dependencies. Vite and the SLAM API compiled,
and all 15 existing workspace tests passed without changing startup deadlines.
After this increment, the rebuilt API and updated source passed all 16 workspace
tests. The new case uses an explicitly synthetic loopback actions service and the
real product configuration, HTTPS API, Unix SLAM services and owned child processes.
It covers configured single-expression dispatch, unique request/epoch binding,
zero dispatch during mapping or localization, invalid acknowledgement interlock,
no queued retry and explicit operator recovery. Physical actions remain disabled.
Two initial new-fixture attempts failed because its independent bridge token was
missing and then mismatched; both fixture issues were corrected before the final
full rerun. No production authentication fallback was added.

Additional checks in this increment:

- Owner lifecycle: 7 tests, including 19 real synthetic owner runs, passed on
  ordinary-user Linux. Fixed diagnostic codes/phases are additive to the existing
  private report. Raw exception text is excluded. An adopted-child cleanup error
  no longer skips API cleanup, and primary failure diagnostics survive a second
  cleanup error. Forced cleanup remains a failed result.
- Lease lifetime: 11 tests passed. Workload identities compare `/proc` start time
  independently of scheduler state; workloads must be retired when the exclusive
  lease becomes available. The guard, which closes its final FD immediately
  before interpreter exit, has a separate one-second exit check. A deterministic
  assertion test covers scheduler-state changes and PID reuse.
- Existing compiled mapping entrypoint: 10 process/HTTPS/stream tests passed.
- Windows full Vitest: 661 passed, 24 skipped. Frontend, API and runtime TypeScript
  checks, production build and 7 release-scope checks passed.

Native-filesystem success does not establish why the earlier Windows-mounted run
failed, and the historical failure is retained. This increment did not rerun the
unrestricted Python suite, ROS/Humble launch tree, Java, Android, ARM64 deployment,
hardware expressions, real sensors, cloud or acoustic acceptance.

## Previous Integration Verification

- `python -B -m unittest robot-api/tests/test_aimdk_bridge.py -q`:
  52 tests, including 13 action-mode tests, passed with synthetic ROS types and
  service responses. No ROS runtime, sensor or robot is contacted.
- `python3 tools/test-deploy-linux.py`: two real installed-entrypoint and
  lifecycle tests passed in a WSL private mount namespace and disposable
  chroot. File operations, checksums, certificates and backups are real;
  systemd, ROS/Python bridge execution and hardware responses are substitutes.
- `python -B -m unittest discover -s tools/tests -p test_release_scope.py -q`:
  seven release-scope checks passed.
- Full Vitest after product integration: 660 passed, 24 skipped. TypeScript frontend/API/runtime checks
  and the production Vite build passed.
- Product configuration: 14 Python checks passed on ordinary-user Linux with
  no skips; Windows ran 14 with five Linux-only skips. TypeScript configuration
  checks and actual loopback HTTPS product pairing/state/confirmation passed.
- Pinned AgentSDK Maven verification: 32 tests, no failures/errors, one
  Linux-permissions case skipped on Windows. This built the current Java JAR,
  not an SDK/cloud session or a new deployment archive.

The unrestricted Windows Python tool-suite attempt was **not green**: 2,013
discovered tests, three failures, 21 errors and 1,071 skips. Missing `json5`
prevented full-mapping fixture setup; other errors include existing point
composition evidence fixtures. Three existing Nav2/terrain evidence checks
reported source-hash mismatches outside this update. Those prior evidence
records and unrelated sources were not changed or relabeled as current.
This integration's targeted Python/configuration/release/deployment suites
passed separately; they do not convert the full-suite attempt into a pass.

These results do not establish actual AIMDK service presence, ARM64 execution,
safe preset completion, cloud/acoustic performance or product acceptance.
