# Isolated ARM64 Python Runtime

Date: 2026-10-31. The previously verified recorded/live commissioning environment
used NumPy 1.26.1 with inherited system SciPy 1.8.0, which emitted a compatibility
warning. Do not update the robot's system Python, AIMDK or ROS packages to fix it.
This optional, separately installed runtime removes system-site inheritance and
has passed the synthetic numerical/Humble checks below on the actual robot.

This is not a replacement for the shared-workspace r7 acceptance record. The
installed shared service has not been switched to this environment. The initial
numerical-only check was followed by the separate live trial below; navigation
has not been exercised or accepted.

## Locked Dependencies

`robot-slam/runtime-arm64-cp310.lock.json` pins Python 3.10 AArch64 wheel URLs,
sizes and SHA256 hashes. The five wheels total 48,246,399 bytes:

| Package | Version |
| --- | --- |
| NumPy | 1.26.4 |
| SciPy | 1.11.4 |
| small-gicp | 1.0.1 |
| PyYAML | 6.0.2 |
| json5 | 0.12.1 |

The tooling and lock are included by both JavaScript and Python source-inventory
builders for future runtime archives. Wheel bytes are intentionally external:
neither they nor a configured environment are committed or silently added to
the previously verified r7 archive.

## Prepare And Install

On an Internet-connected preparation computer, run from `lingxi-next/` with a
new absolute output path whose parent exists:

```sh
python3 tools/arm64_python_runtime.py download --output /private/new-arm64-wheels
```

Transfer the complete resulting directory, the installer, checker and their
required source files to a private directory owned by the ordinary robot user.
Do not transfer credentials or recordings. Preserve the source directory layout;
the checker uses `tools/check_base_pose_tf2.py` and its declared `SOURCES`, plus
`tools/arm64_python_runtime.py`, `tools/check_arm64_python_runtime.py` and the lock.

On ordinary-user AArch64 Linux with system Python 3.10 and `venv`/`ensurepip`,
install to a NEW absolute directory under that user's home:

```sh
/usr/bin/python3 -I -B tools/arm64_python_runtime.py install \
  --wheelhouse /home/run/new-arm64-wheels --output /home/run/new-slam-python
```

Installation uses a clean environment, an exact wheel inventory, offline pip,
hash checking, no dependency resolution and `pip check`. It never installs into
system Python or an existing directory. `include-system-site-packages` must be
false. Failed output is retained for diagnosis, never reused as a success.
`install-report.json` proves installation only, not runtime compatibility.

## Verify The Actual Runtime

Run the checker with the new Python, only the explicit Humble package paths,
and bounded thread counts. This command does not create a ROS context/node,
subscribe to sensors, contact the cloud or invoke motion:

```sh
env -i PATH=/usr/bin:/bin HOME=/home/run LANG=C.UTF-8 \
  PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  AMENT_PREFIX_PATH=/opt/ros/humble \
  PYTHONPATH=/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages \
  LD_LIBRARY_PATH=/opt/ros/humble/lib/aarch64-linux-gnu:/opt/ros/humble/lib \
  OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  /home/run/new-slam-python/env/bin/python -B tools/check_arm64_python_runtime.py
```

Do not add `/usr/lib/python3/dist-packages`, enable system-site inheritance or
suppress warnings to make a failure pass. A genuinely required extra dependency
must be assessed and pinned explicitly. The current five-package lock passed
the actual installed Humble synthetic TF check without such additions.

The sanitized [ARM64 evidence](slam-python-runtime-arm64-evidence.json) binds
the checked source hashes and retained private reports. Observed Python was
3.10.12, all five packages came from the venv, and numerical import warnings
were zero. Synthetic GICP recovered a perturbed known pose with maximum matrix
element error 0.000140492, and refused a seed displaced by 100 map units.
SciPy tree/rotation checks and exact-time Humble TF composition also passed;
the latter's maximum matrix element error was 5.55112e-16. These are synthetic
algebra checks, not a physical position/angle accuracy measurement.

## Live Lifecycle Check

The separate [live ARM64 evidence](slam-python-runtime-live-arm64-evidence.json)
records two consecutive fresh-process trials with the isolated Python. Both
received real corrected point frames, saved a small static map, independently
reloaded it in a new process, and used that exact schema-2 map for unseeded live
localization. Each saved 90 frames and received 89 visual point frames. The maps
contained 2,753 and 2,761 points. These are static lifecycle samples, not room-scale
map coverage or geometric accuracy results.

Localization processed 157 and 156 scans. Both saw at least five increasing
reference frames; paired loopback HTTPS pause/stop, stale-generation rejection
and over 200 stopped-state queries passed. Native/API processes exited normally,
without forced termination. Timed TF input was closed and its caches cleared;
the configured scale of 1.0 remains an unmeasured diagnostic assumption.
Two successful short sessions are not a long-duration stability or cold-boot
reliability acceptance campaign.

Earlier failures are retained, including a successful trial followed by a cold
startup failure. DDS discovery now has its own bounded phase before native
startup. ROS callbacks are admitted only while the worker is running, so native
loading cannot accumulate a stale input queue. Runtime silence, queue-age,
ordering and shutdown limits are unchanged. Installed Humble uses the verified
`Node.count_publishers(topic)` API. Private worker errors report a code location
and fixed stream reason, not exception text or raw sensor values.

`tools/check_arm64_live_runtime.py` runs this check with explicit `--config`,
`--output`, `--node`, `--node-sha256`, `--api-bundle` and `--api-sha256`. Use the
isolated Python and the reviewed Humble/native environment. The output must be
a new, short, private absolute directory. The checker owns the mandatory visual
point consumer, requires bounded scan/pose progress, uses the manager-owned map
directory (not the process session UUID), and never submits a localization seed
or motion command. Source files and preserved native inputs must remain fixed
throughout the trial. Private reports/maps are not delivery contents.

The old four-condition recorded worker evidence still describes its original
source hashes. Current worker diagnostics and live startup are verified separately;
those historical records and the NumPy reference guard were not rewritten.

Focused regression ran 111 tests: 104 passed and seven ordinary-user Linux-only
checks were skipped on Windows. A broader historical exporter suite encountered
20 Windows `resource` import errors; those cases still require Linux and are not
reported as passing. The historical worker hash assertion now checks the old
identity separately from this explicitly recorded live successor.

## Integration Boundary

The separate [r8 shared-package live check](SLAM_WORKSPACE_LIVE_ARM64.md) now
verifies the extracted management package with this Python, including live save,
catalog retention across restart and unseeded localization. It did not install,
upgrade or switch the existing shared service. Keep that service stopped while
preparing an isolated live trial.
The deployment tool's `--python /absolute/new-slam-python/env/bin/python`
selects an environment for a separately verified install; it is not an implicit
upgrade of a running release. Recheck the installed native libraries, private
sensor profile, DDS settings and fresh live lifecycle before using it for
mapping/localization. No service pointer, unit, port or vendor package was
changed by the dependency trial.

Historical recorded-input verification in `glim_supervised_target.inputs()`
intentionally requires the NumPy version recorded in its reference, 1.26.1.
The new runtime uses 1.26.4, so that old path rejects it. This guard was not
loosened and existing evidence was not rewritten. The live trial above has its
own input contract and evidence; it must not be presented as a repeat of
recorded commissioning.

Physical scale, gravity/direction, scan-time interpretation, physical verification
of dynamic base-to-lidar TF, current-site map binding and seeded live positioning remain commissioning
work. Walking, avoidance, voice latency, Android operation and product delivery
are not accepted by this dependency check.
