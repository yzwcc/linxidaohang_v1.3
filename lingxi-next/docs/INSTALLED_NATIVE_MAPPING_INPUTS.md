# Installed Native Mapping Inputs

`tools/prepare_installed_mapping_inputs.py` prepares a private, versioned input
directory for the installed mapping/shared-SLAM service. It copies existing
verified ARM64 libraries, configurations and original receipts. It does not
rebuild libraries, execute GLIM, start ROS, install packages or change services.
Numerical Python imports used by the existing metadata validators are not an
ARM64 mapping-library execution test.

The supported configuration is live `arm64-humble` with an explicitly selected
observed full mapper (`fullMapping.captureObservations=true`). Recorded bags,
business maps and saved point clouds are not preparation inputs. The tool does
not silently downgrade to a historical non-observed mapper. Native terrain and
per-return acquisition selections remain separate; no old ground report is
promoted to a current-source receipt.

## Prepare At The Final Location

First provision the [isolated Python environment](INSTALLED_SLAM_PYTHON.md).
Transfer the existing native bundles and a reviewed mapping configuration into
a private staging directory readable by `lingxi-next`. This source configuration
must explicitly select the live odometry bundle, core, controlled mapping,
corrected-point extension and observed full-mapper bundle, with their existing
manifest identities. Do not invent or alter sensor calibration to pass this step.

The installer creates `/var/lib/lingxi-next/native-inputs` owned by the service
account, mode 0700. Older installations need that directory created explicitly
with the same owner/mode. Use the actual config digest and a **new** output:

```sh
SOURCE=/opt/lingxi-next/current/independent/source
PYTHON=/var/lib/lingxi-next/python-runtimes/cp310-v1/env/bin/python
sudo -u lingxi-next -H "$PYTHON" -s -B "$SOURCE/tools/prepare_installed_mapping_inputs.py" prepare \
  --config /var/lib/lingxi-next/native-staging/reviewed-mapping.json \
  --config-sha256 ACTUAL_REVIEWED_CONFIG_SHA256 \
  --output /var/lib/lingxi-next/native-inputs/version-1
```

This requires ordinary-user Linux execution. Preparation does not require
the robot to move or ROS/DDS to run, but its numerical validation dependencies
must be available. Output under `/home` or `/root`, an existing output,
overlapping frozen inputs and a nonprivate/nonowned parent are refused.
Failed partial outputs are retained without a successful completion receipt;
inspect them and select a new version rather than overwriting them.

Preparation calls the existing core, replay, controlled-configuration, point
extension and source-bound full-mapper validators. It checks the established
live/core/point evidence identities and cross-component hashes. It copies only
declared members, preserving every original manifest/report byte. Source bags
and original output/log directories are not copied, even when they are siblings
inside the staging area. Source receipts/configurations are never rewritten.

The output contains five dependency subdirectories, byte-identical
`original-config.json`, a generated `mapping.json` and
`native-inputs-manifest.json`. **Only five wrapper directory paths change**.
Timing hypothesis, maximum duration, sensor profiles, full-mapper selection,
base-pose scale/transforms and all calibration/acceptance flags remain unchanged.
Both the copies and the original input identities are rechecked before the
completion receipt is written.

Do not move a prepared directory: its wrapper contains its final absolute paths.
To select another location, run preparation again into a new directory. The
original staging paths need not remain online after successful preparation;
verification uses the copied input closure, not the old staging directories.

## Select And Verify

Use the manifest SHA256 printed by the successful command:

```ini
LINGXI_NATIVE_INPUTS_ROOT=/var/lib/lingxi-next/native-inputs/version-1
LINGXI_NATIVE_INPUTS_SHA256=ACTUAL_NATIVE_INPUT_MANIFEST_SHA256
LINGXI_MAPPING_CONFIG=/var/lib/lingxi-next/native-inputs/version-1/mapping.json
LINGXI_SLAM_CONFIG=/var/lib/lingxi-next/native-inputs/version-1/mapping.json
```

Set these in root-only `/etc/lingxi-next/robot.env`. Stop the owners before an
operator-controlled selection change. The preparation tool never edits that
file or starts a service. A standalone read-only check uses the same service UID:

```sh
sudo -u lingxi-next -H "$PYTHON" -s -B "$SOURCE/tools/prepare_installed_mapping_inputs.py" verify \
  --root /var/lib/lingxi-next/native-inputs/version-1 \
  --manifest-sha256 ACTUAL_NATIVE_INPUT_MANIFEST_SHA256
```

When either native-input setting is present, the installed launcher requires
both, pins the completion receipt and requires the selected mapping/SLAM config
to be that directory's `mapping.json`. It runs file-only verification with a
90-second timeout and five-second termination grace. Failure exits 78 before
an owner output/worker is created, without selecting another input directory.
Leaving **both** empty preserves the existing explicit manually verified native
configuration path; it does not disable that owner's own native checks.

The original ARM64/ROS/DDS, loaded-library, calibration and navigation admission
checks still run in the owner. File preparation does not validate the robot's
current DDS file, Linux ABI providers, sensor timing, physical geometry,
supporting ground, localization accuracy or navigation. No readiness flag is
granted by a copied manifest.

## Retention And Delivery

Treat a successfully prepared input directory as immutable. Verification checks
the exact inventory as well as the declared hashes, so even an extra backup
file inside the directory invalidates it. Keep reviewed configuration edits,
environment backups, diagnostics and temporary files in separate private
staging/backup directories. To change a capture limit or other setting, edit a
reviewed source configuration outside the closure, compute its actual SHA256,
and prepare a new versioned output at its final location using the service UID.
Do not edit `mapping.json` or its completion receipt in place. Stop the active
owner only after the operator's capture/save has finished, back up `robot.env`
outside the closure, select the new root and manifest together, and verify the
installed owner's effective selection before inviting another capture.

Upgrade/code rollback retain the external selection and private data directory.
They do not automatically start mapping/navigation/voice. Keep old versioned
inputs until an operator has checked the replacement; do not edit frozen
receipts to accommodate changed source code or libraries. Runtime/source
changes may require rebuilding and validating a new native bundle.

These directories contain private sensor metadata and historical private paths.
They are not source-control or public delivery contents. Keep them outside the
release directory and allow disk space for the deployment tool's data backups.
The earlier 0.1.23 commissioning archive/APK are unchanged; these new helpers and
launcher behavior require a newly built source release before robot deployment.

## Offline Verification

`tools/tests/test_installed_mapping_inputs.py` covers byte-preserving preparation,
unchanged nonpath settings, missing/modified inputs, retained partial failures,
exact inventory, private paths and nonpromotion of acceptance flags. Synthetic
fixtures replace only the file validators and are not ARM64 execution evidence.
The existing disposable-chroot deployment tests run the installed launcher as
the real unprivileged service UID, including paired selection, matching config,
failure-before-owner behavior and the unchanged manual-input path.

For an explicitly prepared real private input directory, set
`LINGXI_INSTALLED_NATIVE_INPUTS_ROOT` and
`LINGXI_INSTALLED_NATIVE_INPUTS_SHA256` when running that test module. Its extra
test reruns the actual receipt validators while forbidding `ctypes.CDLL` and
child process creation. Without those explicit private inputs, that case skips.

On 2026-11-04, the existing observed ARM64 build was prepared through the
original source/build verifiers, then a real five-component input closure was
materialized: 142 files, with original receipt bytes retained. A fresh Linux
process reverified that closure with mapping-library loads and child creation
forbidden. See the [source-bound offline summary](installed-native-inputs-offline-evidence.json).
The earlier missing temporary-parent trial and the superseded preclarification
receipt remain private; neither was relabeled as the current successful run.
This is actual file-level verification of ARM64 artifacts, not ARM64 execution
or current-robot acceptance.

For the subsequently deployed 300-second input selection and actual live Web
capture/save/reload result, see the
[November 9 target record](TARGET_MAPPING_SAVE_RELOAD_20261109.md). Its passing
functional checks do not grant geometric or navigation acceptance.
