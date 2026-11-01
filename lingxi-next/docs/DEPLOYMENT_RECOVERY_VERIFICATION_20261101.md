# Core Deployment Recovery Verification

Verified 2026-11-01, entirely on the workstation. No robot connection, service
change or deployment was performed.

## Implemented Scope

`deploy/transaction.sh` shares an exclusive install/rollback lock, validated
release pointers, stopped-service config/data backups, atomic release switches
and error/signal recovery. New health must pass before the previous pointer is
advanced. Recovery retains the failing status and restores pointers, unit files
and saved paths. Only a previously active management API may restart; navigation,
AIMDK bridge and voice remain stopped for inspection. An unconfirmed stop blocks
data restore/restart and records `recovery-incomplete` in the private backup.

First-install, upgrade and rollback paths were exercised in an isolated Linux
mount namespace/chroot with real files, hashes, TLS certificates and archives.
systemd and HTTPS responses were test substitutes, not real service acceptance.
The lifecycle test covers first activation failure, failed upgrade and rollback
health, service enable failure, TERM, stop failure, inactive/disabled prior API,
concurrent deployment, corrupt releases and vendor-directory preservation.

Shell traps do not handle SIGKILL or power loss. Newly created config/data paths
are retained rather than purged during snapshot restore. Failed release copies
are retained for inspection, not silently overwritten or automatically removed.
These limits remain explicit; this is not a power-loss transaction journal.

## Checks

- Isolated Linux lifecycle unittest: passed (2.755 seconds).
- Release scope unit tests: four passed; missing components, divergent scope
  and false complete/independent/production claims rejected.
- Bash syntax checks and release-builder JavaScript syntax: passed.
- Actual `npm run release:commissioning`: passed, including 502 TypeScript tests
  (16 explicitly skipped), 39 API Python tests, type checks and Web build.
- Independent actual archive audit: 12,661 hashed files, ELF64 AArch64 runtimes,
  executable modes, safe links, pinned AgentSDK and declared component presence.

Archive: `lingxi-next-0.1.19-core-commissioning-20261101-linux-arm64.tar.gz`

SHA-256: `994cfc3a676c8ebe557629c908e8c5434bc4bf6abe0b640ca0b068436911c9d5`

The generated archive/report remain local ignored artifacts. They are not an
installation recommendation. `deploy/release-scope.json` and embedded status
identify profile `core-services-vendor-adapter`, `completeProduct: false`,
`independentStackIncluded: false`, and `productionAccepted: false`.

This package includes Web/API, the vendor AIMDK bridge, Java Agent, and pinned
Node/Java ARM64 runtimes. It does NOT include the independent GLIM mapper,
seeded localization, OctoMap clearance or Nav2 runtime composition. Independent
runtime integration and robot-specific acceptance remain required before a
complete-product delivery claim.
