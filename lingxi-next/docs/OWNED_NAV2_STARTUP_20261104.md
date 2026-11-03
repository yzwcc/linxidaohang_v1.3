# Controlled Production Nav2 Startup

Date: 2026-11-04. Production-path code with synthetic sensors and in-memory
AIMDK output, not physical robot or complete-product acceptance.

## Implementation

The owned Nav2 producer now uses the confirmed official-manager startup policy
first verified in the [offline workspace](NAV2_LIFECYCLE_STARTUP_20261104.md).
Both paths import the same `lingxi_navigation.lifecycle_startup` implementation.
Only the owned producer explicitly opts its action driver into managed startup;
existing externally managed/passive action clients remain passive.

The ticket-bound physical launch disables automatic lifecycle activation. Two
current GetState round trips from every unconfigured managed node must precede
one official ManageLifecycleNodes STARTUP request. Positive manager confirmation,
current active states and the action server are required before readiness.
Failed or uncertain startup is never automatically replayed. Closing the driver
cancels its pending queries/manager future and fences late readiness.

Reading action events does not revoke startup ownership. Action completion,
output retirement, controller freshness and generation process exit remain
separate evidence. No motion publisher was added to the action driver.

OwnedNav2Backend's 30-second prewarm deadline and the isolated workspace's
five-second goal-acceptance deadline are unchanged. Prewarm still cannot queue
a goal, arm AIMDK output, bypass map-bound localization or relax supporting-ground,
costmap, footprint, braking and operation-lease checks.

This is a bounded startup-ordering mitigation. The host's complete FastDDS
discovery cause remains unproven; no RMW/package replacement or vendor update
is part of this change. The older response-send timeout logs remain preserved.

## Package Completeness

The independent shared SLAM source inventory previously omitted the physical
launch and `robot-navigation/bridge` scripts. Both the Python packager and the
JavaScript builder now bind those files, as well as the automatically inventoried
shared startup module. Other package profiles remain separate.

Source-bound extraction tests verify the launch, entrypoint and lease guard are
present. In a fresh isolated Python process with no project PYTHONPATH, all 16
checked runtime modules load from the extracted source tree. This includes the
new startup module, navigation action/producer modules and both bridge modules.
ROS is not imported, native code is not executed and robot IO is not opened.
Dependency and Web/API fixtures in this package test remain explicitly synthetic;
this is not a newly built complete ARM64 release archive.

## Actual ROS Verification

The existing loopback-only isolated Humble wrapper ran two production-path tests
successfully in 47.140 seconds, with no skipped tests. Raw results remain under
`owned-startup-20261104-b` in the separate rootfs.

The new repeated-prewarm case confirms eight distinct owned namespaces, official
startup success in every generation, no action submitted during prewarm, no
armed output, zero nonzero output messages and complete process-group retirement.
Observed prewarm durations were 1.5372--4.9642 seconds within the unchanged
30-second budget. This is not voice-command or acoustic first-response latency.

The existing composed case also passes actual Nav2 action/controller operation
against the in-memory output, pause/retirement/fresh-epoch resume, localization
loss while sensor publishing continues, explicit recovery, sensor loss, map
handoff with new owned localization topics and fresh map-bound output retirement.
It does not use real robot sensors or physical transport.

The first repeated-prewarm fixture in `owned-startup-20261104-a` incorrectly
expected same-map confirmation to replace an unused prewarm when the fixture's
localization-only loader permits reuse. It failed that identity assertion, not
a startup timeout; the existing composed test passed in the same run. The final
fixture explicitly stops and confirms retirement before each fresh generation.
No production reuse policy was changed to satisfy the test.

## Regression Scope

Focused ordinary-user Linux regression discovers 136 tests: 135 pass and one
JavaScript inventory check skips because that environment lacks a host Node.
It covers the action-driver startup opt-in, passive-client compatibility,
manager refusal/no replay, cancellation/closure, runtime ownership, physical
bridge, velocity, map activation, localization guard and shared package.
The Python/JavaScript source inventory equality check passes separately on
Windows. The full scientific/native Python suite was not rerun.

After sharing the startup module, the separate actual offline API/voice Nav2
suite also passes all four tests in 104.76 seconds. Its 12 repeated generations
use `product-offline-c0ad4586b9d69c458b0a0633`; client request-to-running intervals
were 1,232--2,758 ms under the unchanged five-second acceptance limit. This
includes port-conflict cleanup, paired requests, voice/tour pause and resume,
Q&A preserving navigation and actual synthetic-world detour. These 12 offline
generations are separate from the eight owned production prewarms above.

The final Windows shared-package suite passes 11 tests with one scientific
dependency skip; its source inventory equality test is not skipped. Release
source filtering/copy checks pass two tests, with the Windows symlink-specific
test skipped. Skips are not counted as acceptance evidence.

The final default TypeScript regression passes 750 tests with 25 explicit
environment-dependent skips. The actual-Nav2 opt-in execution above is separate
from those skips. Frontend/API/runtime type checks and the production Web build
also pass. Build output contains the existing dependency comment-annotation
warnings, without a build failure.

## Outstanding Acceptance

These results do not certify current ARM64 execution, real terrain support,
independent metric calibration, AIMDK transport, AgentSDK acoustic behavior or
cloud latency. The preserved real recording still has no accepted supporting
free cells. No robot connection, vendor modification, APK or deployment occurs
in this milestone. Full independent-stack composition/release and field
acceptance remain open.
