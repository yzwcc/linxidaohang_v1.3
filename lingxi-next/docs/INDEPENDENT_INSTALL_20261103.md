# Independent Commissioning Installation

This package combines the paired management API, ARM64 Node/Java runtimes,
LinkSoul bridge, independent mapping/shared-SLAM assets and source-bound
GLIM/localization/terrain/Nav2 adapters. It is commissioning software, not a
field-accepted complete product. Native GLIM/terrain libraries, ROS Humble,
Nav2, robot-specific calibration and cloud credentials remain external.
The vendor AIMDK installation and vendor maps must not be replaced.

## Build And Install

```sh
npm ci
npm run build:agent
npm run release:independent-commissioning
python tools/verify-release.py artifacts/ARCHIVE.tar.gz
sudo bash deploy/install.sh --commissioning --robot-ip 192.168.0.128
```

Use the actual archive filename. The adjacent SHA256 and verification report
must match it. Existing stage/archive names are rejected; use a new version.
Installation starts only the core management API on HTTPS port 3000.
It does not start SLAM, navigation, AgentSDK or any robot motion.

## Configure The Independent Owner

Edit the root-only `/etc/lingxi-next/robot.env` after target verification:

```ini
LINGXI_INDEPENDENT_PRODUCT=1
LINGXI_ROS_SETUP=/opt/ros/humble/setup.bash
LINGXI_AIMDK_SETUP=/absolute/existing/vendor/setup.bash
LINGXI_SLAM_PYTHON=/var/lib/lingxi-next/python-runtimes/cp310-v1/env/bin/python
LINGXI_MAPPING_CONFIG=/etc/lingxi-next/independent/mapping.json
LINGXI_SLAM_CONFIG=/etc/lingxi-next/independent/slam.json
LINGXI_RUNTIME_DIR=/var/lib/lingxi-next/runtime
LINGXI_MAP_ROOT=/var/lib/lingxi-next/maps
```

These are paths, not ready-made calibration. Prepare verified native bundles
and configuration for the target ARM64/Python 3.10/ROS environment before
starting an owner. Do not invent sensor topics, scale, TF or ground extrinsics.
The `lingxi-next` service UID needs direct access to these existing inputs;
keep writable runtime/catalog paths separate from configuration and binaries.
Systemd protects home directories, so do not place deployed native inputs
under `/home` or `/root`. Do not change ownership of vendor directories.

The Python path above is an example, not a bundled environment. Follow
[Installed SLAM Python](INSTALLED_SLAM_PYTHON.md) to provision a new offline
venv under the service account and verify its locked imports. Both owners
require `LINGXI_SLAM_PYTHON`; an empty selection, system interpreter, mismatched
dependencies or failed ROS imports must stop before any owner is launched.
No automatic fallback or system-package installation is performed.

For versioned native inputs outside protected home directories, follow
[Installed Native Mapping Inputs](INSTALLED_NATIVE_MAPPING_INPUTS.md). This
preserves original receipts and only generates a new wrapper with explicit
paths. Optional `LINGXI_NATIVE_INPUTS_ROOT`/`LINGXI_NATIVE_INPUTS_SHA256` bind
installed startup to that verified closure; preparation grants no navigation
or physical calibration acceptance.

Choose one owner, never both:

```sh
sudo systemctl start lingxi-next-independent-mapping.service
# OR: shared mapping and seeded localization, with optional product workspace
sudo systemctl start lingxi-next-independent-slam.service
```

Default HTTPS ports are 3443 for mapping-only and 3444 for shared SLAM.
Import the installed robot CA into the client, then pair at that port.
Port 3000 is a separate core console, not an alias of the independent owner.
Mapping remains externally remote-controlled; no Web/APK walking control is
added. Point-cloud Z defaults remain 0.1 to 1.4 metres.

The two owner units conflict. Configuration errors detected by the installed
Bash entrypoint exit 78 without endless service retries. Native validation
errors still appear in the journal/owner report and need operator correction.
Per-start diagnostic directories are retained under `runtime`; map data stays
in the separate catalog. Review retention during maintenance, with owners
stopped. Never remove a live owner's output or the map catalog as log cleanup.

## Optional Product And Terrain Selection

The shared SLAM owner stays commissioning-only unless a product selection is
provided. See `INDEPENDENT_PRODUCT_WORKSPACE_20261103.md` for the private
configuration/token contract and the current navigation/action restrictions.
Both optional selections require a direct absolute file and an explicit
SHA256 in `robot.env`:

```ini
LINGXI_PRODUCT_CONFIG=/etc/lingxi-next/independent/product.json
LINGXI_PRODUCT_CONFIG_SHA256=REVIEWED_SHA256
LINGXI_ACQUISITION_SETTINGS=/etc/lingxi-next/independent/acquisition.json
LINGXI_ACQUISITION_SETTINGS_SHA256=REVIEWED_SHA256
```

Leave both fields of an unused selection empty. Changed bytes, incomplete
pairs or malformed digests block launch. Product state must be owned by the
service UID with mode 0700; configuration and Agent token files use mode 0600.
Model and Retriever credentials stay external and are submitted using paired
HTTPS settings, never bundled or stored in browser/APK configuration.
When the product pair is selected, the installed Agent service derives its
callback origin as `https://127.0.0.1:LINGXI_SLAM_PORT` (default 3444), reads
`stateDirectory/credentials/ai.json`, reads the private `agentTokenFile`, and
binds playback at the configuration's `agentUrl`. It ignores the unrelated core
`LINGXI_API_URL`, `LINGXI_CREDENTIALS_FILE`, `LINGXI_AGENT_INTERNAL_TOKEN` and
`LINGXI_AGENT_URL`. Custom SLAM ports must be set consistently in `robot.env`;
port 3000 and a playback/API port collision are rejected. The robot CA still
comes from `LINGXI_TLS_CA`, and `LINKSOUL_AGENT_ID` is still required.

After configuring cloud credentials through the paired independent console,
start voice explicitly with `sudo systemctl start lingxi-next-agent.service`.
Installation/upgrade does not start another Agent instance or change microphones.
Incomplete/changed selection, missing product credentials or unsafe private
paths fail before SDK startup with exit 78, without falling back to core; the
unit does not retry configuration failures. Clear both product fields to select
the existing core Agent behavior. Restart voice explicitly after changing its
configuration, Agent identity, local token or cloud credentials; no live token
rotation or cross-owner event replay is performed. Switching the owner requires
stopping the old Agent first. Do not leave a second SDK instance connected.

## Upgrade, Rollback And Acceptance

The transaction stops all six product units before replacing code. It retains
configuration and persistent data, backs up units/state, and only starts the
core API after an upgrade or rollback. Independently commissioned services
must be started explicitly after verification. Rollback does not restore
business state automatically or restart movement. Vendor services are outside
the transaction.

```sh
sudo bash /opt/lingxi-next/current/deploy/diagnose.sh
sudo bash /opt/lingxi-next/current/deploy/rollback.sh
sudo journalctl -u lingxi-next-independent-slam --since '10 minutes ago'
```

Offline installation tests use actual Bash/filesystem/certificate operations
inside a disposable Linux chroot, with synthetic systemd/health/native owners.
They do not execute ARM64 runtimes or prove sensor quality, calibration, map
scale, physical stopping, navigation, voice acoustics/P95 or Android device
acceptance. A running API, existing socket or saved map is not proof that
physical navigation is ready; live evidence gates remain mandatory.
