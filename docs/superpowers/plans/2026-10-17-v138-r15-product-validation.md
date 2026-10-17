# V1.3.8 r15 Product Validation Plan

**Goal:** Audit the delivered r14 as a user-facing product, fix reproducible faults, and supply an independent complete r15 archive with two first-install guides.

**Architecture:** Extract the immutable r14 archive into an isolated candidate directory in the existing V1.3.8 worktree. Exercise actual HTTP and Linux process boundaries, preserve existing motion/voice/tour behavior, and validate the final archive rather than only working sources.

**Tech Stack:** Python 3, Java 17, TypeScript/Fastify/Vitest, Bash, browser automation, ARM64 robot runtime artifacts.

**Constraints:** Product remains V1.3.8. Do not deploy to a robot. Preserve AIMDK, customer credentials, maps, tour tasks and Linkcraft resources. Never force accepted motion status or re-send a gesture after ambiguous acknowledgement. Stage only r15 work and this plan.

## 1. Immutable Baseline and Reproduction

- [x] Extract r14 SHA `ac7f93f20d1ed2155e394b4bf216d8717476925d571582632d84b4081d05f0b0` with `prepare_candidate.py`.
- [x] Extend `candidate/source/server/test/robotClient.test.ts` to prime the map list, fail the map server, force status refresh and assert `map_web_online=false` while cached display data remains available.
- [x] Add Linux voice-script scenarios: stale PID must not terminate an unrelated process; repeated/concurrent starts preserve one healthy voice bridge; occupied unrelated port must not be called ready.
- [x] Run new tests against unmodified candidate and retain failing evidence.

## 2. Focused Fixes

- [x] Separate live map-service health from cached map-list display in `candidate/source/server/src/robotClient.ts`; confirm failure then recovery over real HTTP.
- [x] Harden `candidate/scripts/robot_voice_bridge_control.sh` with process identity, mutation lock, HTTP readiness and idempotent start. Ensure background children release the lock. Preserve explicit restart behavior.
- [x] Verify `navigation_control.sh status` cannot report a stale unrelated PID as running.
- [x] Investigate additional findings only when a concrete reproduction or traced contract supports a change. Added required AIMDK interface preflight and restoration after failed directory switch; both reproduced before fixes.

## 3. Product Workflows

- [x] Browser-check desktop and mobile navigation, task editing/saving/refresh, start failure/retry, image loading, and absence of script errors or overflow using an isolated fixture.
- [x] Run full server, Python, Java, motion acknowledgement and Linux process suites.
- [x] Recheck wake/ASR-to-text model input, model outage recovery, positive/negative motion requests, exact saved-point navigation, tour wait/interrupt/resume and task persistence. Java/Python regressions passed; real Node route -> Python HTTP -> disk -> reloaded store passed for waits 0/5/45/300, command phrases and speech; invalid values leave data unchanged.

## 4. Delivery

- [x] Write `V1.3.8-r15-完整部署说明.md` for first install including AIMDK preparation/compilation.
- [x] Write `V1.3.8-r15-已编译AIMDK新机完整部署说明.md` as a standalone first-install procedure that does not compile or replace AIMDK.
- [x] Build the complete archive and LF SHA files; check source/runtime equivalence and all checksum layers.
- [x] Validate Linux fresh install, r11-r14 upgrades, repeat install, rollback and customer-state preservation against the final archive.
- [ ] Record exact tested hash, limitations and evidence. Review, commit, push and compare remote/local commit before delivery.

## Verification Notes

- Baseline reproduction failed as intended for cached-map health, stale navigation PID, all three voice-process cases, missing AIMDK types and failed AIMDK switch.
- Browser fixture had an obsolete 10-second minimum assertion; current UI/API/storage agree on 0-300 seconds. Corrected the test, preserved product behavior, then passed all widths and startup failure/retry checks.
- Final-archive Linux voice fixture initially could not re-execute from root-only temporary parents as nobody. Copied the exact packaged script into the fixture's traversable directory and required the explicit occupied-port diagnostic. All three scenarios then passed.
- Final archive SHA: `9d56d4718c4e5d103fa8ececd5110e0fd93cb98bc10a66360f35b4ffa0012b68`.
- Windows final archive verification passed: 74 server tests, 133 Python tests, 8 Java suites, actual task HTTP persistence and source/runtime equality.
- Linux final archive verification passed: all process, storage, AIMDK failure and install lifecycle cases; 30 Bash scripts and 24 documentation blocks parsed successfully. Both reports bind the final hash above. No robot/cloud/acoustic tests were claimed.
