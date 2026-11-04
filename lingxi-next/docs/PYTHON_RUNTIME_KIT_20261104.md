# Offline Python Runtime Dependency Kit

Recorded 2026-11-04. Companion to the frozen 0.1.25 commissioning release,
not another main-package or APK rebuild and not installed ARM64 acceptance.

## Artifact

Local file: `artifacts/lingxi-next-python-cp310-arm64-v1.tar.gz`, with adjacent
`.sha256` and `.verification.json`. Binary artifacts are excluded from Git.

| Property | Value |
| --- | --- |
| Archive bytes | `47675225` |
| SHA256 | `4eb8c2dca4f4873546bd7dd3694a60bd759da9eb6570a66dc6274cc90dc1d412` |
| Manifest SHA256 | `6265b7442dd58bbb3d0ec7d8ea54b559e214e614d1e8aa8875a154ca1435036a` |
| Extracted root | `lingxi-next-python-cp310-arm64-v1` |
| Verified files | 11, including the checksum list |
| Original wheel bytes | `48246399` |

The five unchanged ARM64 CPython 3.10 wheels are NumPy 1.26.4, SciPy 1.11.4,
small-gicp 1.0.1, PyYAML 6.0.2 and json5 0.12.1. Their 12 embedded
license/notice files remain inside the original wheels; the manifest records
their individual SHA256 identities. The kit also contains the existing ordinary-
user installer, lock, pinned requirements and installation README. Its installer
and lock were compared byte-for-byte with the actual frozen 0.1.25 archive.

## Verification

- Windows and ordinary-user WSL each passed all 23 runtime/kit tests.
- The final verifier checked the real archive hash and every member against
  the original locked wheelhouse. Synthetic fixtures also establish deterministic
  packaging and refusal to overwrite an archive or an existing completion file.
- Ordinary-user Linux extracted the actual archive into
  `/var/tmp/lingxi-python-kit-v1-BvAFdg/lingxi-next-python-cp310-arm64-v1`.
  `sha256sum -c SHA256SUMS` verified all ten listed members.
- The extracted installer's `--help` ran successfully, and that same installer
  verified all five extracted wheels, totaling 48,246,399 bytes, against its lock.
- No ARM64 wheel was executed or installed. No robot was connected, vendor
  package changed, service started or sensor/motion interface invoked.

To recheck without rebuilding, from the project root:

```sh
python -B tools/python_runtime_kit.py \
  --wheelhouse /absolute/original/arm64-python-wheelhouse-20261031-a \
  --output /absolute/artifacts/lingxi-next-python-cp310-arm64-v1.tar.gz \
  --verify-sha256 4eb8c2dca4f4873546bd7dd3694a60bd759da9eb6570a66dc6274cc90dc1d412
```

See [installed Python](INSTALLED_SLAM_PYTHON.md) for service-UID installation at
its final versioned path. System Python 3.10, venv/ensurepip, ROS Humble and the
verified native input bundle remain prerequisites; this kit supplies only the
locked Python dependencies. It never installs globally or updates `robot.env`.

## Handoff Priority

The next work is target commissioning, not another cosmetic offline iteration:

1. Confirm the powered-on robot's current address and inspect its ARM64 Python,
   ROS and native runtime prerequisites before changing the installation.
2. Install in isolated versioned paths, verify imports/native loading as the
   service UID, and check passive live sensor/TF timing without movement.
3. Establish measured metric/gravity/ground/base calibration. Existing replay
   still has zero supported free cells; unknown space must not become free and
   the unverified 0.83-m ground offset must not be installed as calibration.
4. Validate visible point-cloud mapping, save/reload and localization before
   supervised navigation, stopping and obstacle trials. Then measure real voice
   wake/rejection/first-audio latency and Android device behavior.

No target connectivity, geometry, physical navigation, cloud/audio performance
or full-product result is implied by these offline package checks. The overall
product remains open; the main archive and APK retain their existing identities
and commissioning status.
