# Nav2 Service Diagnostic Follow-Up

Date: 2026-11-11. Isolated x86-64 Humble rootfs, loopback-only domain 174.
This is offline diagnostic evidence, not robot or production acceptance.

Three independent runs completed 36 consecutive synthetic Nav2 generations.
Each report contains 96 correlated request/response pairs (288 total); all
responses succeeded and every generation reached running, then stopped and
retired. No `MAYBE` response-send timeout was reproduced. The pinned packages
were `rmw_fastrtps_shared_cpp 6.2.10-1jammy.20260907.205244`,
`fastdds 2.6.12-1jammy.20260723.233918`, and
`nav2_lifecycle_manager 1.1.20-1jammy.20260915.225403`.

The prior failure report at
`/home/admin1/.cache/lingxi-next/nav2-humble-20261031/rootfs/service-diag-check-20261104-a/report.json`
remains intact. These passing runs do not explain why that generation's
server-side reader match callback was late; root cause remains unestablished.
No timeout, DDS setting, RMW package, or middleware behavior was changed.
No robot, sensor, vendor endpoint, or physical motion was involved; each report
records `physicalMotionSent=false` and `navigationReady=false`.

The three reports remain in the same isolated rootfs:

| Output | SHA-256 of `report.json` |
| --- | --- |
| `service-diag-20261111-a` | `6a436f33e413a2c560a358cc7f3f6df5bfd4fb6964d5a5ce608325079fd9f07b` |
| `service-diag-20261111-b` | `8be99c2fca4f3ae0e3d6953b4767ff9e7f70136289f5be91e8d1ad5845bf2c3a` |
| `service-diag-20261111-c` | `fad6906b2644ada9652b2721b961c6722bb1b8c86eb3a415fbd9638cf396692b` |

The result is a clean comparison set, not a reliability claim. Field navigation
and lifecycle timeout acceptance remain pending.
