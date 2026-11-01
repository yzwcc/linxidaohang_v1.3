# Offline Voice Failure Verification

Date: 2026-11-01. No robot connection, real cloud request or speaker playback.
Tests use controlled HTTP/SSE responses, callback events and speaker failures.

## Verified Behavior

- Turn cancellation reaches streaming and nonstreaming model calls and Retriever
  requests, including cancellation before asynchronous state/credential reads end.
- Explicit unsuccessful model finish reasons and provider error frames are
  refused. Events after a terminal frame in the same network chunk are ignored.
  Compatible providers may terminate normally with `stop` or `[DONE]`.
- Interrupted response text includes only emitted segments and a fixed refusal;
  pending, unspoken text is not reported as delivered.
- Speaker failure requests a correlated stop and aborts upstream generation.
  Queued segments are not played after failure. Already successful first-audio
  diagnostics are preserved when a later segment fails.
- A failed stop remains a dispatch barrier and is retried by a later interaction.
  A successful stop does not leave a stale barrier. HTTP/SDK playback errors alone
  are never treated as confirmation that audio stopped.
- A current failed answer returns explicit rejected diagnostics. A superseded
  answer returns no reply and cannot overwrite the new turn's diagnostics or
  execute commands. Q&A playback failure does not cancel active point navigation.

## Checks

From `lingxi-next/`:

```text
npm test
  31 files passed, 2 files skipped
  498 tests passed, 16 tests skipped
npm run build
  frontend/API/runtime TypeScript checks passed; Vite production build passed
```

Rollup reported two non-fatal annotation warnings in the installed Zod package.
No production dependency was changed by this milestone. Regression sources are
`robot-api/tests/answerer.test.ts` and `robot-api/tests/api.test.ts`.

## Remaining Acceptance

Real AgentSDK stop acknowledgement and callback timing, cloud availability,
speaker behavior, acoustic first-audio P95 <=2 s, wake success/false-wake rates
and field navigation remain unaccepted. Test durations or mocked first-audio
timestamps are not evidence for acoustic latency. Broader independent-stack
Python failures remain recorded in `OFFLINE_WORKSPACE_VERIFICATION_20261101.md`.
