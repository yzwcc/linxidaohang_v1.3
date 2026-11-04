# Installed SLAM Python

The system-service mapping and shared-SLAM owners require an explicit
`LINGXI_SLAM_PYTHON`. They no longer select `/usr/bin/python3` implicitly.
The selected venv is inherited by the owners' Python children through
`sys.executable`. This does not change the core AIMDK/action/navigation bridge
interpreters, the vendor installation or a running service.

## Offline Provisioning

For the frozen 0.1.25 commissioning release, prefer the prepared
`artifacts/lingxi-next-python-cp310-arm64-v1.tar.gz` companion kit. Its identity,
contents and actual Linux extraction checks are recorded in
[the kit verification](PYTHON_RUNTIME_KIT_20261104.md). Verify its archive SHA256
before extraction, then run `sha256sum -c SHA256SUMS` inside its extracted root.
Use the included installer and `wheelhouse/` together, as shown in its README;
all parent directories must be readable by the `lingxi-next` service UID. This
does not require rebuilding or modifying the main archive or APK.

The following download path remains available when preparing a new wheelhouse.
Reuse `tools/arm64_python_runtime.py` and the existing five-wheel lock; do not
install numerical packages globally or copy an existing venv to a new path.
Venvs contain absolute paths and must be created at their final location.

On a networked build workstation, prepare a **new** absolute directory:

```sh
python tools/arm64_python_runtime.py download --output /absolute/new/wheelhouse
```

Transfer that exact directory, including `requirements.txt`, to a fresh
`/var/lib/lingxi-next/python-wheels-v1` on the robot. Make this copy owned by
`lingxi-next:lingxi-next`, with directories mode 0700 and files mode 0600.
Do not change ownership or permissions of vendor files. The installer verifies
the exact inventory, sizes, SHA256 hashes and pinned requirements before and
after pip. Target installation uses `--no-index`, hash checking and no source
builds. The wheels total 48,246,399 bytes; the installed venv requires more space.

After the commissioning package has created the service account:

```sh
SOURCE=/opt/lingxi-next/current/independent/source
sudo install -d -m 0700 -o lingxi-next -g lingxi-next /var/lib/lingxi-next/python-runtimes
sudo -u lingxi-next -H /usr/bin/python3 -I -B "$SOURCE/tools/arm64_python_runtime.py" install \
  --wheelhouse /var/lib/lingxi-next/python-wheels-v1 \
  --output /var/lib/lingxi-next/python-runtimes/cp310-v1
```

This requires the robot's existing Linux ARM64 system Python **3.10** with
venv/ensurepip support. It refuses root execution, non-ARM64, another Python
version, overlapping inputs and an existing output. Failed partial directories
are retained and never reused automatically. Resolve the prerequisite or choose
a new versioned output; do not replace the vendor Python to satisfy this check.
The service account's configured home is `/var/lib/lingxi-next`, which satisfies
the existing ordinary-user installer's home-directory requirement without using
`/home` or `/root`. `ProtectHome=true` remains enabled.

## Selection And Startup

Set this in root-only `/etc/lingxi-next/robot.env` after checking the target:

```ini
LINGXI_SLAM_PYTHON=/var/lib/lingxi-next/python-runtimes/cp310-v1/env/bin/python
```

Use the actual new versioned path. Its directory must be direct, not a `current`
symlink. The interpreter itself may be a normal venv symlink to the existing
system binary. Do not move or overwrite an environment while an owner uses it.

Before either owner starts, the launcher sources the configured ROS/vendor
environment, disables user-site imports and runs
`tools/check_installed_slam_python.py` using this interpreter. A 30-second timeout
with a five-second termination grace bounds this preflight. It requires:

- Non-root Linux ARM64 Python 3.10 and the explicitly selected isolated venv.
- `include-system-site-packages=false` and disabled Python user-site imports.
- NumPy 1.26.4, SciPy 1.11.4, small-gicp 1.0.1, PyYAML 6.0.2 and json5 0.12.1.
- Numerical modules **and distribution metadata** located inside that venv.
- Warning-free numerical/compiled SciPy imports, plus existing Humble `rclpy`
  and `tf2_ros` imports from `/opt/ros/humble`.

The probe does not create ROS nodes, subscribe to sensors, call robot interfaces
or promote navigation readiness. Configured ROS/vendor `PYTHONPATH` remains
available; if it shadows a locked package, correct that configuration rather
than accepting a system package. Successful preflight is only import and
interpreter validation, not a geometric, native-bundle or physical acceptance.
The existing native/configuration verifiers still run in the owner.

Missing or invalid selections exit **78** before creating an owner output or
launching a worker. There is no system-Python fallback. The units already prevent
automatic retries for this configuration status. Check the selected path,
service-account access, package versions and ROS setup in the journal before
trying an operator-controlled restart.

For a standalone import check, use the same service UID, source the actual
configured ROS/vendor setup, then invoke the selected interpreter with `-s -B`:

```sh
/var/lib/lingxi-next/python-runtimes/cp310-v1/env/bin/python -s -B \
  /opt/lingxi-next/current/independent/source/tools/check_installed_slam_python.py \
  --python /var/lib/lingxi-next/python-runtimes/cp310-v1/env/bin/python
```

Running this as root is intentionally refused. A valid report contains package
versions and the runtime lock digest, never cloud credentials.

## Upgrade And Rollback

The selection is external to a release in `robot.env`. Normal install/upgrade
and code rollback preserve that file and the versioned venv under the data
directory. Existing transaction backups include both configuration and data;
allow disk space for these larger snapshots. Runtime upgrades are a separate
operator choice: provision a fresh directory, stop the owners, retain the old
selection, verify the new environment, then explicitly update the path.
Code rollback does not silently select another Python. Restore the previous
explicit path when also rolling back a runtime change. Mapping/navigation/voice
are never automatically resumed by rollback recovery.

## Verification Boundary

`tools/tests/test_installed_slam_python.py` checks selection, locked package
origins/versions, compiled-import failures, warnings and refusal contracts with
synthetic imports. `tools/test-deploy-linux.py` executes installed Bash in a
disposable chroot as the actual unprivileged service UID, with a real host venv
and synthetic preflight/owners. It checks interpreter propagation to a Python
child, no fallback, refusal before owner output, and selection/data retention
through upgrade/rollback. The host venv is **not** an ARM64 runtime test.

Offline regression on 2026-11-04: 47 related Python tests passed on ordinary-user
Linux, and both disposable-chroot deployment tests passed. On Windows, release
source-copy tests passed two cases and skipped the existing POSIX symlink case;
the WSL environment has no Node executable, so that case was not rerun there.
Bash syntax and Git whitespace checks also passed. Initial sandbox PAM/root
directory permission failures were corrected in the test setup, not hidden by
relaxing the production checks.

Actual installed-service ARM64/native execution and current-site mapping,
localization, ground calibration, navigation, acoustics and Android field
acceptance remain required. Previously built commissioning archives/APKs have
not been rewritten by this source update.
