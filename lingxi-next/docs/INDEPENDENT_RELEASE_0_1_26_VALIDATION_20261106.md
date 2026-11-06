# Independent Commissioning Release 0.1.26

Recorded 2026-11-06. Configuration-fix commissioning build, not a completed
product or physical navigation acceptance.

## Change

The 0.1.25 environment template populated `LINGXI_NAVIGATION_SOCKET` while
`LINGXI_INDEPENDENT_PRODUCT=0`. The API correctly rejected that contradictory
configuration with `physical_navigation_requires_independent_product`, so a
fresh target install could not pass its health check. The 0.1.26 template now
leaves the socket empty until an explicitly commissioned product bridge is
selected. A regression test asserts the safe default. No navigation, mapping or
voice service is enabled by this fix.

## Artifact And Offline Checks

| Property | Value |
| --- | --- |
| ARM64 archive | `artifacts/lingxi-next-0.1.26-independent-commissioning-20261106-linux-arm64.tar.gz` |
| Bytes | `131699299` |
| SHA256 | `5f3370791800e4b25efc73d264e78e1a95b29e8c1f85aea3921d2e8f9ea22220` |
| Verified files | 13,215 |
| Verified native source references | 86 |

Build passed the full TypeScript/Vitest release suite (750 passed, 25 skipped),
API Python release tests (55 passed, one skipped), Web production build,
independent source-scope verification and ARM64 archive verification. The new
deployment-default and runtime-kit tests pass 7/7. The archive remains marked
`productionAccepted=false` and `completeProduct=false`.

The commissioning Android APK was not rebuilt for this server-side default
configuration fix. The previously verified 0.1.25 APK is API-compatible; its
own acceptance record and hash are unchanged.

## Target Boundary

Installation is a separate operator/target event and is recorded only after
its own checksum, transaction, health and rollback-state checks. This archive
does not provide target Python wheel installation, sensor/TF calibration,
ground support, saved-map accuracy, physical navigation or voice acceptance.
Keep the product's independent owners disabled until those gates pass.
