# Offline DDS Publication Freshness: 2026-11-01

Scope: isolated local ROS Humble/FastDDS, with loopback-only networking and
synthetic navigation inputs. No robot contact, deployment, sensor capture or
physical command transport occurred. This is not field acceptance.

## Implemented Behavior

- The dedicated unspun Twist reader retains the DDS source timestamp returned by
  the installed Humble `subscription.handle.take_message(Twist, False)` API.
  Ordinary executor callbacks in the verified installation discard this metadata.
- Each generation uses a bounded wall/monotonic clock mapping. Missing, malformed,
  replayed, future, pre-generation and more-than-150-ms-old publication timestamps
  are rejected. Clock discontinuities also invalidate the source generation.
- A rejected source cannot revive on a later fresh sample. Runtime faults retire
  that generation; stale tickets cannot fault a newer generation. Runtime output
  expiry retains publication age instead of resetting freshness on callback arrival.
- The API backend and isolated six-scene runner now use this reader. The existing
  output-retirement and generation ownership barriers remain in force.

The API is private and specific to the verified Humble/FastDDS installation.
It is restricted to the isolated offline path, not promoted to a production
motion interface. A different rclpy/RMW version needs its own compatibility test.

## Actual ROS Checks

The [native DDS probe](evidence/humble-publication-probe-20261101.json) accepted a
fresh publication, rejected an actual publication deliberately left unread for
250 ms, kept the rejected generation invalid, made a closed reader inert and
accepted a fresh publication in a new generation. The receipt binds source
hashes and the installed executor hash; repository tests verify its source identity.

All six scenes were rerun using the real isolated NavFn/RPP/velocity-smoother
processes. The [current receipt](evidence/nav2-publication-age-20261101.json)
regrades raw samples and binds source/report hashes. No scene sends physical IO.

| Scene | Accepted commands | Outcome |
| --- | ---: | --- |
| Open | 235 | Goal reached |
| Blocked | 0 | No motion, blocked path |
| Detour | 576 | Goal reached around obstacle |
| Appearing wall | 41 | Stopped before new wall |
| Disappearing obstacle | 494 | Goal reached after explicit supported-map revision |
| Unsupported shortcut | 841 | Supported route taken, unknown ground avoided |

The blocked scene legitimately has zero publications and commands. Other scenes
require positive verified publication counts; all recorded maximum publication
ages are <=150 ms. The last two scenes retain explicit synthetic support/occupancy
oracles, not real recorded OctoMap-to-localization integration.

The opt-in product API integration also passed both actual ROS tests in 59.49 s:
pause/resume epoch retirement, authenticated voice routing, insufficient-evidence
Q&A without navigation cancellation, two-point tour/no automatic return, and a
detour while scene/state are polled concurrently.

## Regression and Remaining Boundaries

Final scientific WSL Python discovery, including both source-bound receipts,
passed 1390 tests, with 51 skips (1441 total), zero failures/errors, in 176.034 s.
The earlier run before the receipt test passed 1389 with the same 51 skips.
Web/API/runtime TypeScript tests passed 502 tests with 16 skips. `npm run build`
passed; only the two existing upstream Zod annotation warnings were reported.

DDS write time is the velocity smoother's publication time, not proof of the
controller's computation age, sensor-to-command latency, physical transport age
or actuator response. `productionCommandAgeProven=false`,
`physicalMotionSent=false` and `navigationReady=false` remain unchanged. Installed
Nav2 binary equivalence to the pinned source has not been verified.

Full-product priorities remain measured ground-support production, metric
recorded localization/occupancy integration, a guarded AIMDK command adapter and
complete independent-stack packaging. Live cloud/acoustic behavior, current
ARM64 execution and Android device operation still need separate acceptance.
