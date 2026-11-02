# Passive Live Acquisition Wiring

Date: 2026-11-03. Offline lifecycle verification only. No robot connection,
sensor subscription, deployment, motion command or ARM64 execution in this
milestone. This is not mapping, terrain or navigation acceptance.

## Scope

The existing ARM64 per-return component can now be explicitly selected by the
live localization child. The component's cross-build and static package checks
remain described in [the earlier component record](ACQUISITION_ARM64_COMPONENT_20261103.md).
The 29 ARM64 contracts were compiled/listed, not run on the target.

`live_acquisition.py` composes the existing `NativeAcquisitionOutput` and
`LocalizedNative` on the sensor worker's single native owner thread. Accepted
acquisition packets retain their original scan receipt and localization binding.
Optional declared terrain uses `RegisteredTerrainSource`, `TerrainLane` and a
supervised `TerrainProcess`; it does not publish a ROS grid or connect to motion.

## Explicit Selection

The operator-only `--acquisition-settings` option is available on
`managed_localization.py`, `localization_service.py`, `slam_service.py`,
`managed_slam.py` and the extracted `launch_slam_bundle.py`.
The selected local strict-JSON sidecar must name the verified ARM64 component
and its manifest digest. The runtime checks the current GLIM/GTSAM core,
source identities, ELF architecture and the already-enabled
`save_imu_rate_trajectory` setting. No configuration is silently changed.

The parent passes both the sidecar path and its complete integrity identity to
the child. Missing pairs, recorded input, wrong architecture and input changes
are rejected. Each on-demand launch rechecks the selected inputs. Optional
terrain additionally requires declared geometry, projection, ground declaration,
source-bound native build reports and matching AArch64 libraries/dependencies.

The shared launcher requires the sidecar outside its extracted package and
separate from the persistent maps, run output and frozen native inputs. The
installer records its path and SHA256 in the immutable release record, verifies
its private ownership/permissions and rechecks it before preflight and execution.
Upgrades inherit the current choice unless explicitly replaced or disabled with
`--without-acquisition`; rollback restores the selected release's own choice.
No sidecar, component library or calibration is copied into the release archive.

The shared source profile now includes the acquisition module sources, navigation
Python modules, Patchwork++ lock and ground/OctoMap bridge sources needed by
runtime provenance checks. The narrower point/mapping profiles are unchanged.
No production sidecar or guessed calibration was generated. Existing release
artifacts and historical ARM64 evidence do not certify this wiring.

## Lifecycle

- Pause, stop, reseed and localization loss withdraw the prior terrain result,
  including when no new sensor callback arrives.
- Revocation hides results before waiting for a busy native terrain child.
  A previous generation cannot restore its result after retirement.
- Factory failures retain native/terrain cleanup ownership. Native close and
  counter reads remain on their original worker thread.
- Terrain owner or child failures are latched instead of being hidden by
  final retirement. Shutdown verifies owner exit and child cleanup.
- Terminal verification requires the acquisition callback count to match the
  actual estimated-pose stream, with no unconsumed output or discarded packets.
- Both managed and on-demand localization allow 30 seconds for child shutdown
  only when terrain is configured; ordinary localization keeps its existing
  default. This cleanup deadline does not delay immediate result withdrawal.

## Verification

Run from the repository root in the existing isolated ordinary-user Linux
scientific environment, with NumPy, SciPy, json5 and small-gicp available:

```sh
rtk proxy python -B -m unittest discover -s lingxi-next/tools/tests -p test_live_acquisition.py -v
rtk proxy python -B -m unittest discover -s lingxi-next/tools/tests -p 'test_*localization*.py' -q
rtk proxy python -B -m unittest discover -s lingxi-next/tools/tests -p 'test_*terrain*.py' -q
rtk proxy python -B -m unittest discover -s lingxi-next/tools/tests -p 'test_*acquisition*.py' -q
rtk proxy python -B -m unittest discover -s lingxi-next/tools/tests -q
```

- Live acquisition wiring: 25 passed, no skips, 26.192 seconds.
- Localization regression: 175 discovered, 164 passed, 11 skipped because
  the corresponding preserved private evidence was not supplied, 25.573 seconds.
- Terrain regression: 111 discovered, 100 passed, 11 skipped for optional
  native/private integration prerequisites, 20.390 seconds.
- Acquisition regression: 89 passed, no skips, 33.027 seconds.
- Full scientific Python regression: 1,706 discovered, 1,637 passed,
  69 skipped, zero failures/errors, 413.646 seconds. Skipped checks are not
  acceptance evidence. The focused suites above overlap this full run.
- Web/API regression (`rtk npm test` from `lingxi-next`): 511 passed,
  16 skipped, 26.68 seconds. These are automated tests, not field acceptance.
- Production build (`rtk npm run build`): frontend, API and runtime type checks
  and Vite build passed. Rollup reported non-fatal annotation warnings in zod.

The new wiring tests use synthetic ELF/ctypes, sensors and terrain processes.
They verify lifecycle and admission, not ARM64 loading or real-time throughput.

## Shared Package Selection Follow-up

The shared-launcher and deployment follow-up was checked separately from the
earlier wiring results above:

- Live acquisition admission/lifecycle suite: 28 passed.
- Shared bundle contracts: 10 passed and one explicitly skipped in the initial
  host run. The all-profile JavaScript/Python inventory parity check subsequently
  passed separately on Windows and Linux with an explicit host Node available.
- Deployment transactions: 14 passed, including inherited/replaced/disabled
  selection, unchanged rollback inputs, failed activation and immutable records.
  Service-manager calls in these tests are stubbed; no target unit was installed.
- Source inventory checks (`node --test tools/check-live-build.mjs`): 6 passed.
- Full scientific Python regression: 1,718 discovered, 1,650 passed, 68
  explicitly skipped, zero failures/errors, 459.222 seconds. Optional private
  evidence/native integration prerequisites were not supplied; skips do not
  establish acceptance. The focused suites overlap this regression.
- Extracted-package workspace suite: 11 passed, no skips, 22.877 seconds.
  Production Python modules, API, web and JavaScript dependencies came from the
  new extracted tree. The test driver/native workers/map geometry were synthetic;
  the explicitly supplied Linux x64 Node was not the bundled ARM64 executable.
- A separate isolated `python -I` test imported nine real production modules
  from a freshly extracted test package without the development Python path.
  Native libraries were not loaded by this import check.
- Web/API tests: 511 passed and 16 skipped; production type checks/Vite build
  passed with the existing nonfatal zod annotation warnings.

The real shared archive was prepared, independently verified, extracted to a
fresh external directory and reverified using its own extracted preparation
tool. No ARM64 executable or native component was run during these checks.

| Item | Current offline preparation |
| --- | --- |
| Archive bytes | 54,496,544 |
| Source files | 323 |
| Package files including manifest | 7,901 |
| Pinned production packages | 95 |
| Build manifest SHA256 | `10b5135da3b8b80e07efd827c6045e4670916fb88afc1ffec4f1aeb991f818f2` |
| Archive SHA256 | `982e37e01c8b74fa190b7092c0a46a6921233d45e5ccf98adde42374ab5b2095` |
| Package manifest SHA256 | `6e0f6a74912e6abe142ee37b734afdc474e388d581e03b313571fec6ae7a1eb4` |

Generated preparation/evidence directories remain private, ignored local
artifacts, not source-control payloads or a production delivery. The archive
does not contain a production acquisition sidecar, terrain calibration or
external native libraries. Its successful preparation is not field acceptance.

## Remaining Gates

Actual ARM64 module loading, target contracts, passive sensor throughput,
live shared-package composition on the robot and field acceptance remain outstanding.
Metric scale, gravity/ground/base alignment and ground support are not validated.
Runtime reports keep `physicalScaleValidated`, `groundSupportValidated` and
`navigationReady` false. No previous private trial is relabeled as a current
source result, and no fresh robot scan is requested at this stage.

Web/APK point-cloud presentation and voice behavior were not changed here.
The default display Z interval remains 0.1--1.4 m; that display filter is not
used as proof of traversable ground or clearance.
