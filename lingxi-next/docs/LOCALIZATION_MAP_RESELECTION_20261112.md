# Localization map reselection — 2026-11-12

After a localization run stops and cleanup completes, the console can load the
run's saved map before the operator explicitly selects it for the next run.
Selecting that same map cleared the preview even though the preview effect's
map ID and checksums did not change. No replacement fetch ran, leaving the
point cloud unavailable and the start button disabled. Returning from another
page produced the same problem.

The map selector now updates only the selected ID. The existing preview effect
continues to invalidate and reload data when the connection, effective map ID,
receipt checksum, or point checksum changes. Rendering and seed controls still
require the preview to match the current map and session.

## Verification

- Before the fix, the expanded HTTPS browser check reached successful service
  cleanup and failed when reselecting the same map: the start button remained
  disabled for the entire 25-second observation window.
- After the fix, all 23 browser stages passed. They include selecting the same
  map after cleanup and after leaving/reopening the page; both preserve the
  visible cloud, enable explicit start, and send no lifecycle command merely
  from selecting a map. Existing checks cover seed confirmation, map-binding
  mismatch, late replies after pause, outages, conflicts, and cleanup failure.
- Numeric-field edits in this browser driver now use select-all and text
  insertion, with an exact field-value assertion, to avoid the CLI occasionally
  appending to a prefilled number.
- The four focused localization/backend/workspace test files passed 108 tests;
  13 tests require ordinary-user Linux and were skipped on Windows.
- TypeScript checks for the web, API and runtime, and the Vite build passed.

Browser reports and screenshots are ignored local artifacts under
`.local/localization-reselect-after-1112-b/`. This is an isolated synthetic UI
check; physical localization accuracy and navigation acceptance remain open.
The robot keeps serving the installed version during the operator's new scan;
this source change has not yet been deployed. The console remains HTTPS 3444.
