# Installed Acquisition And Terrain Inputs

`tools/prepare_installed_acquisition_inputs.py` prepares the optional passive
per-return acquisition selection for an installed shared-SLAM owner. It reuses
the [prepared native mapping inputs](INSTALLED_NATIVE_MAPPING_INPUTS.md), the
production acquisition/settings validators and, when selected, the existing
terrain target-trial validator. It never loads mapping libraries, executes
bundled tests, creates ROS nodes, changes services or sends robot commands.

Use a source release containing this update. The immutable 0.1.24 archive/APK
predate both this helper and the GTSAM prerelease-alias compatibility fix; they
have not been rebuilt or retroactively declared to include the fix.

## Prepare At The Final Location

Provision the [isolated Python environment](INSTALLED_SLAM_PYTHON.md) and native
mapping directory first. Transfer an existing verified acquisition component
and reviewed settings into private service-readable staging. The component must
bind the current packaged validator sources and the exact selected GLIM core.
Its existing odometry configuration must already enable IMU-rate trajectories;
this tool does not edit GLIM configuration to satisfy that prerequisite.

```sh
SOURCE=/opt/lingxi-next/current/independent/source
PYTHON=/var/lib/lingxi-next/python-runtimes/cp310-v1/env/bin/python
sudo -u lingxi-next -H "$PYTHON" -s -B "$SOURCE/tools/prepare_installed_acquisition_inputs.py" prepare \
  --settings /var/lib/lingxi-next/native-staging/reviewed-acquisition.json \
  --settings-sha256 ACTUAL_REVIEWED_SETTINGS_SHA256 \
  --mapping-inputs /var/lib/lingxi-next/native-inputs/mapping-v1 \
  --mapping-manifest-sha256 ACTUAL_MAPPING_INPUT_MANIFEST_SHA256 \
  --output /var/lib/lingxi-next/native-inputs/acquisition-v1
```

Use the actual final directories and SHA256 identities. The mapping-input pin
is its preparation completion manifest, not the GLIM core manifest or an
acquisition component manifest. Preparation is ordinary-user Linux only. The
output must be new, outside `/home` and `/root`, with a private owned parent,
and separate from frozen source directories. Failed partial outputs remain for
inspection; select a new output instead of overwriting an existing directory.

The output contains an `acquisition/` component, byte-identical
`original-settings.json`, generated `acquisition.json` and
`acquisition-inputs-manifest.json`. The wrapper changes the acquisition root
only. Optional terrain also changes its trial root, external target-report
location and two native library paths. Numeric geometry, projection, ground
declaration, ROS-output modes, source/manifest identities and acceptance flags
are preserved. The full mapper's configuration is selected exactly as in the
production mapping platform, not its legacy baseline configuration.

## Optional Terrain

When `terrain` is present, it must select `targetTrial` with an existing pinned
successful ARM64 runner receipt accepted by `terrain_bundle.verify_selection`.
Host build reports are not a fallback. Preparation copies the declared trial
files, original manifest and original external runner report without rewriting
them. Only the declared relative OctoMap SONAME aliases are recreated.
The copied runner report remains external to the copied trial directory.

An absent or invalid terrain receipt stops preparation. The tool does not
create one, run the target test, omit a requested terrain section, alter ground
height/scale, fabricate ground support or grant navigation readiness. Historical
target receipts remain evidence of their recorded run, not proof of current
hardware calibration, live throughput or current-site terrain.

## Verify And Select

Run the standalone file check as the service UID:

```sh
sudo -u lingxi-next -H "$PYTHON" -s -B "$SOURCE/tools/prepare_installed_acquisition_inputs.py" verify \
  --root /var/lib/lingxi-next/native-inputs/acquisition-v1 \
  --manifest-sha256 ACTUAL_ACQUISITION_INPUT_MANIFEST_SHA256
```

It checks the exact private inventory, bounded metadata, permitted aliases,
unchanged settings/declarations, original component receipts, current sources
and the pinned mapping directory. Verification does not require the original
acquisition/terrain staging directories after preparation; the mapping directory
is retained as an explicitly pinned shared dependency. Do not move a prepared
directory: its wrapper contains final absolute paths.

After verification and an operator-controlled stop, select the generated file
using the existing shared-SLAM settings pair in root-only `robot.env`:

```ini
LINGXI_ACQUISITION_SETTINGS=/var/lib/lingxi-next/native-inputs/acquisition-v1/acquisition.json
LINGXI_ACQUISITION_SETTINGS_SHA256=ACTUAL_GENERATED_SETTINGS_SHA256
```

The second value is the printed **settingsSha256**, not the preparation
completion-manifest hash. No new environment variables or automatic service
starts are introduced. This helper's completion verification is an explicit
operator preflight; the installed owner independently performs the existing
pinned settings, component, core, target-report and geometry checks. Its actual
ARM64/DDS/loaded-library/calibration admission checks are unchanged.

## Actual Offline Result On 2026-11-04

The first real composition exposed a production compatibility error: the
verified core inventory includes `libgtsam.so.4.3a0` and
`libgtsam_unstable.so.4.3a0`, while `core_matches` accepted numeric-only suffixes.
It now accepts bounded digit-leading alphanumeric version segments. All aliases
of a library must still resolve to identical hashes; path-like names and
conflicting aliases remain invalid. The fix is in the production verifier,
not a preparation-only filtering workaround.

A new component was prepared through the existing build verifier from the
unchanged compiled artifacts. Its manifest is
`5f0464ab5625b0bcbc4cec80c4518e9147209842d5f124a61491939c8ba09b45`.
The native library remains
`5eb4f079d9f753642eac64aef83b5454022ee9878cd57357567e15c5b75e6f0d`;
its original build-report bytes also match. The earlier `f901d001...` component
manifest and QEMU result were retained unchanged. The historical catalog now
checks that QEMU report's original trial sources against `a8229c3`, explicitly
exposing the changed Python verifier instead of calling the old run current.

Actual installation-input preparation produced five files, with
`localizationRosOutput=private_saved_map_observations` and **no terrain**.
Completion-manifest SHA256:
`3dbb3093d6e7040039e849e11827520068f9a677f736063bf82518bd1184c9c0`.
Generated-settings SHA256:
`673cb523b9a2acc1f53237875920a51b08c0da950b6e230d0a6eda2dcf37698e`.
It uses the existing prepared mapping directory pinned by
`31f42ae31c43cb2d6508a6420359884197a41b7ce3c510ed6b225af34f12ca16`.

A fresh isolated Linux Python process reverified those actual files and all
20 core library names, using the full-mapper configuration. Mapping
`ctypes.CDLL` loads and child creation were forbidden; `rclpy` was not imported.
The earlier component manifest, library and original build report were checked
again. This is real file-composition evidence, not ARM64 execution, current
robot deployment or successful live terrain. Optional terrain relocation was
tested using explicitly synthetic isolated receipts, not relabeled as real.

A fresh target-trial candidate was also prepared from that new component,
without rebuilding or executing native code. Its manifest is
`b57ae103494445fadf415b2cc29d326b4319fe5050658cc227f7c2ef9a0816b1`:
nine member files, 14 current bound source files and 29 configured contracts,
zero executed. An independent file-only process verified all members/current
source hashes with native loads and children forbidden. This replaces the old
candidate for the next actual robot trial, not the old QEMU evidence.

No scientific native rebuild, QEMU contract rerun, robot connection, service
installation, microphone or sensor IO occurred. Current ground support remains
zero supported free cells; physical scale/gravity/base calibration and field
mapping/localization/navigation/voice/Android acceptance remain pending.

## Regression Coverage

- 76 Linux tests passed across installed acquisition preparation, live
  acquisition, target-terrain selection and installed mapping inputs, including
  both explicitly selected real private input checks.
- 78 acquisition-related Python tests passed, including prerelease aliases,
  conflicting alias hashes and preserved historical QEMU provenance.
- Windows historical-provenance tests: 16 run, 15 passed, one platform skip.
- Final Linux installed-input, historical-provenance and shared-SLAM bundle
  regression: 41 run, 40 passed, one unavailable-host-Node skip. The real private
  acquisition check passed; the added file-adapter test requires the selected
  mapping completion pin and full-mapper config, not a legacy config shortcut.
- Release scope: nine tests passed; source-copy filtering: two passed, one
  Windows symlink skip. The new helper is mandatory in future independent
  release source payloads; this does not modify an old archive.

The initial terrain test fixture used a flat matrix where the base-pose
contract requires 4x4; the fixture was corrected, not the rigid-pose check.
The first real component attempt stopped at the GTSAM name incompatibility
before creating an output. Neither attempt is counted as a successful result.
Old native/component/field evidence was not rewritten to satisfy new code.
