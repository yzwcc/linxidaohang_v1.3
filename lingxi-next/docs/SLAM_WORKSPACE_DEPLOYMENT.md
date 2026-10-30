# Shared SLAM Workspace Deployment

Date: 2026-10-31. This commissioning package combines the existing independent
point-cloud mapping and on-demand saved-map localization workspace. It does not
install vendor SLAM, change AIMDK, provide walking commands, or replace the
existing port-3000 console. It is not a final product release or APK update.

本包用于独立点云建图与保存地图定位的集中联调，包含安装、升级、回滚工具。
地图与配对信息独立保留，不覆盖 AIMDK，不接管旧控制台，也不包含行走指令。
这是可核验的阶段包，不是最终产品交付或 Android 更新。

## Package Boundary

- `build-slam.mjs` builds the dedicated shared API and common web application.
- `prepare_slam_bundle.py` prepares, verifies and extracts
  `shared-slam-runtime-arm64.tar.gz`. Its purpose, manifest and archive prefix
  differ from the earlier mapping-only and read-only point-stream packages.
- The archive contains source, compiled API/web assets, pinned ARM64 Node and
  production JavaScript dependencies. It excludes configuration, TLS keys,
  pairing credentials, recordings and business maps.
- Existing isolated GLIM/native libraries, point adapter and verified sensor
  configuration remain external requirements. The target must be ordinary-user
  Linux AArch64 with the matching Humble Python 3.10 environment.
- The launcher verifies the entire archive inventory and refuses developer
  source, alternate web assets, port 3000 and environment data-directory overrides.
  `--check-only` validates inputs without starting workers or creating run output.

See [workspace behavior](SLAM_WORKSPACE.md),
[native mapping configuration](SLAM_MAPPING_CONTROL.md), and
[the package verification evidence](slam-workspace-preparation-evidence.json).

The final generated archive is
`artifacts/lingxi-next-shared-slam-20261031-r2-arm64.tar.gz`, with an adjacent
`.sha256` file. These generated artifacts are intentionally excluded from Git.
The earlier non-r2 development archive is superseded.

| Item | Final preparation |
| --- | --- |
| Archive bytes | 54,218,809 |
| Source files | 203 |
| Package files including manifest | 7,779 |
| Pinned production packages | 95 |
| ARM64 Node prepared, not executed | 22.23.4 |
| Archive SHA256 | `fcd6759ed73e8fa4f7c2ca80fce1aa43057b355a629c6401defd36d6b5a5b419` |
| Manifest SHA256 | `b3725cd4cd48784a043720f6df16582bc67b835252d95f13906fb8ca959cd31c` |

Preparation, independent verification and extraction checked every archive
member. Use a new matching manifest after any rebuild; these are not universal
hashes for future packages.

## Build and Extract

Run the asset builder in the project; the output must be a new directory:

```sh
node robot-runtime/build-slam.mjs .local/NEW_SHARED_BUILD
```

On an ordinary-user Linux x64 preparation host, use the actual build manifest
SHA256 and the existing verified ARM64 dependency consumer:

```sh
python3 -I -B robot-runtime/python/prepare_slam_bundle.py prepare \
  --build /absolute/NEW_SHARED_BUILD \
  --build-manifest-sha256 REPLACE_WITH_BUILD_SHA256 \
  --consumer /absolute/VERIFIED_ARM64_CONSUMER \
  --consumer-manifest-sha256 e35d411cf7cde7b8bca4b76717accaf2a137ca5512c2f1fe8fcf3f6f1a106521 \
  --output /private/NEW_PREPARATION
python3 -I -B robot-runtime/python/prepare_slam_bundle.py verify \
  --preparation /private/NEW_PREPARATION --output /private/NEW_EVIDENCE.json
```

Extract to a new directory, with the hashes published alongside that archive:

```sh
python3 -I -B robot-runtime/python/prepare_slam_bundle.py extract \
  --archive /private/shared-slam-runtime-arm64.tar.gz \
  --archive-sha256 REPLACE_WITH_ARCHIVE_SHA256 \
  --manifest-sha256 REPLACE_WITH_MANIFEST_SHA256 \
  --output /home/agi/slam-runtime
```

The extracted `source/robot-runtime/python/prepare_slam_bundle.py` supports the
same verification and extraction commands without the original development tree.
These commands never subscribe to sensors or execute the packaged ARM64 Node.

## Install and Start

Use a short, direct absolute root such as `/home/agi/lx`. All managed directories
are private and owned by the current ordinary user. The installer checks the
complete nested localization socket path as well as mapping point sockets before
creating the root. Spaces, symlinked roots and overlong paths are rejected.

Provide a verified external configuration, existing Python environment and TLS
certificate/key. These must not live in the installation or extracted bundle.
The certificate must cover the actual LAN address and be trusted by clients.
Use the current address when commissioning, not a historical IP hardcoded in
source. The installation below is a template, not evidence of robot deployment:

```sh
python3 -I -B /home/agi/slam-runtime/source/robot-runtime/python/slam_deploy.py install \
  --bundle /home/agi/slam-runtime \
  --manifest-sha256 REPLACE_WITH_MANIFEST_SHA256 \
  --root /home/agi/lx \
  --config /absolute/private-config/slam.json \
  --python /absolute/verified-humble-environment/bin/python \
  --tls-key /absolute/private-tls/server.key \
  --tls-cert /absolute/private-tls/server.crt \
  --host 0.0.0.0 --port 3443
```

Installation performs a nonstarting preflight and links the dedicated user unit,
but defaults to stopped. Add `--start` only for an explicit activation. It does
not enable startup at boot, configure user lingering, install native packages,
disable other services or stop processes occupying the chosen port.

The pairing code is generated once in `ROOT/pairing-code.txt` (0600). Read it
locally for pairing; do not distribute it with the archive or put it in logs.
The API receives only its SHA256. Browser access uses `https://ROBOT_IP:3443`.

`lingxi-next-slam.service` uses `Type=notify`, no automatic restart, private umask,
bounded start/stop deadlines and control-group cleanup. Readiness is sent over
the actual systemd Unix datagram socket only after the owner verifies the API's
listening event. Merely spawning a process is not success.
The 240-second stop ceiling covers the existing owner's bounded native and
adopted-child cleanup budgets; systemctl itself allows 300 seconds. These are
failure ceilings, not a mandatory delay on normal shutdown.

## Upgrade and Roll Back

Install a separately extracted, verified new archive with the same root. Releases
are retained under their manifest SHA256; configuration records are immutable
for that release. Changed external configuration hashes stop preflight before
service changes. Pairing is retained, and maps remain under `ROOT/maps`.

After a successful switch, `previous` references the prior release. To return
to it, use a verified extracted deployment tool:

```sh
python3 -I -B /home/agi/slam-runtime/source/robot-runtime/python/slam_deploy.py rollback \
  --root /home/agi/lx
```

Rollback also defaults to stopped; `--start` is explicit. An activation failure
attempts to stop the attempted service and restores the old `current` pointer,
including when the cleanup stop itself fails. It never resumes previous work.
If systemd cannot confirm stop, inspect process/service state before retrying;
pointer restoration alone is not proof that a failed process has terminated.
Both releases and maps are retained for diagnosis.

Each service start allocates a fresh 12-character run directory under `ROOT/runs`.
Native sessions retain their existing time limits and are not replayed after
restart. Logs and `owner-report.json` are private in the respective run directory.
Inspect both these reports and `systemctl --user status lingxi-next-slam.service`.
Releases and logs are not automatically pruned yet; monitor disk use manually.

Only independent map storage persists here. Legacy point/tour migration,
business configuration persistence, voice/LLM integration, navigation and local
avoidance, APK integration and production installation remain separate work.
The older `release:commissioning` entry does not invoke this installer.

## Reproduce Extracted-Composition Tests

From the development source tree, use Linux x64 Node and Python with scientific
test dependencies. All production modules, API, web assets and JavaScript
dependencies below come from the extracted package; native workers and map
geometry are explicitly synthetic test fixtures:

```sh
LINGXI_SLAM_TEST_SOURCE=/private/extracted/source \
LINGXI_SLAM_TEST_WEB=/private/extracted/web \
LINGXI_SLAM_TEST_BUNDLE=/private/extracted/api/main.mjs \
LINGXI_SLAM_TEST_NODE=/absolute/linux-x64-node/bin/node \
/absolute/test-environment/bin/python -B -m unittest discover \
  -s tools/tests -p test_slam_workspace.py -v
```

Deployment transaction tests use real Linux files, symlinks and locks with a
stubbed service manager. Two separate opt-in tests use the actual WSL Linux user
systemd with uniquely named ephemeral synthetic units to check ready/no-ready
behavior and explicit stop. They do not install the generated target unit or
establish ARM64 acceptance.
The extracted composition test likewise cannot establish physical accuracy,
sensor calibration, live mapping, relocalization or navigation performance.

## Verification Record

- Full Linux regression: 1,096 tests run, 1,081 passed and 15 explicitly skipped.
  The final stop-budget regression was added afterward and passed in the
  eight-test deployment suite. The two systemd tests were run separately.
- Asset inventory: five tests passed. Windows package contracts: six shared,
  eight mapping-only and fourteen read-only package tests passed.
- The final r2 extracted-package workspace suite passed eleven tests, including
  actual HTTPS static assets, mapping/save/localization/restart and child cleanup.
  Production Python modules and JavaScript assets/dependencies came from the
  extracted tree; the Linux x64 test Node and synthetic native fixtures did not.
- TypeScript frontend/API/runtime checks and Vite build passed. Vitest: 346
  passed and fourteen explicitly skipped.
- An initial full run selected an old local GLIM checkout and rejected its
  configuration cleanliness. Repeating with the established clean pinned Linux
  checkout passed; no upstream files were reset or edited.
- A concurrent development-tree workspace run hit a shutdown-observation
  timeout while accessing dependencies on the Windows-mounted filesystem.
  The full regression and extracted composition passed. This development-host
  observation is retained here; it is not evidence of long-run reliability.

Readiness flags in the package evidence remain false. No robot was contacted,
deployed to, or moved during this package milestone.
