# Project workflow

## GitHub version history

The user requires each completed code update to be committed and pushed to
https://github.com/yzwcc/linxidaohang_v1.3.git.

- Review changes and run checks appropriate to the update before committing.
- Include related source, tests, build scripts, and documentation in the version.
- Respect `.gitignore`; keep credentials, workstation files, dependencies,
  generated archives, and extracted verification copies out of Git.
- Preserve unrelated work. Never discard changes or force-push.
- Commit with a meaningful description, then push the current branch to origin.
- Verify that the remote branch matches the local commit before reporting upload
  success. If authentication or networking prevents upload, report that clearly
  and retain the local commit for retry.
- `scripts/sync-github.ps1 -Message "description"` stages, commits, pushes, and
  verifies a version. `-DryRun` previews the changes without modifying Git.

## Local tooling

Prefix shell commands with `rtk`; use `rtk proxy` for commands without a filter.
If `.codegraph/` exists at the repository root, use CodeGraph before searching
or reading source to locate or understand code. Do not create an index unless
the user requests it.
