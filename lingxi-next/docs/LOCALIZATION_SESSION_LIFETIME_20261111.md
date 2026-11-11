# Localization Session Lifetime - 2026-11-11

## Finding

On the ARM64 robot, seeded localization for map `11.11` started at 17:41:29
and the supervisor recorded `slam_process_fault:unexpected_worker_state` at
17:46:28. The health protocol showed the worker leaving `running` after its
configured 300-second passive sensor window ended. The supervisor expected an
explicit stop request before the child ended; the child therefore exited as a
failed session. The robot service and saved map stayed available, and cleanup
completed. No movement command was sent.

## Change

For live localization, the owning service now starts an absolute session timer
at child launch and requests the ordinary stop protocol 10 seconds before the
child's bounded sensor window. Loading and startup count against the product
session limit. Status polling, seeding, and reacquisition do not reset it. The
manager advances its lifecycle generation and revokes proxy access before
cleanup; shared activity locks remain held until process and monitor cleanup is
confirmed. A cleanup failure remains failed and retains its lock.

The browser shows the approximate five-minute live verification limit. The
existing port contract is unchanged: this independent console uses HTTPS 3444;
port 3000 belongs to another project.

## Verification

Focused tests cover bounded live-only configuration, automatic clean stop,
revoked proxy access during cleanup, lock retention, unconfirmed cleanup, and a
real supervised child using the stop pipe. These tests are lifecycle checks;
they do not establish localization accuracy or navigation readiness.
