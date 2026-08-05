# v0.2.0 release checklist

## Reviewed source

- [ ] The release PR contains only C6, G1, G2, and release-assurance changes.
- [ ] Final security/privacy diff review has no unresolved high, medium, or low
  findings.
- [ ] `pyproject.toml`, `starcompanion.__version__`, diagnostics, wheel metadata,
  and SBOM all report `0.2.0`.
- [ ] No real game data, logs, ownership state, local settings, caches, or
  unredacted diagnostics are tracked.
- [ ] External URLs and development references match `EXTERNAL_RESOURCES.md` and
  `NOTICE`.

## Validation

- [ ] Full Windows and Ubuntu pytest matrix passes.
- [ ] Network-surface, dependency audit, SBOM, license, streaming, ownership,
  and no-game-data gates pass.
- [ ] Windows and Ubuntu frozen GUI/CLI artifacts build from hash-verified
  offline wheelhouses.
- [ ] Each packaged GUI launches headlessly and closes cleanly with Python
  networking/DNS denied.
- [ ] Each packaged CLI completes preview, unconfirmed-write rejection,
  confirmed apply, rollback, ownership recovery, settings portability, and
  redacted diagnostics smoke tests offline.
- [ ] `release-manifest.json` hashes match every artifact in its platform set.

## Publication hold

- [ ] Record the exact merge commit and confirm tag `v0.2.0` will point to it.
- [ ] Decide whether to publish verified unsigned Windows artifacts or run the
  protected Authenticode job after a certificate becomes available.
- [ ] Inspect downloaded CI artifacts on clean Windows and Ubuntu systems.
- [ ] Obtain explicit publication approval.
- [ ] Only after approval: create/push the annotated tag, create the GitHub
  release from `RELEASE_NOTES_v0.2.0.md`, attach the reviewed artifact sets, and
  verify the public hashes.

Do not create a GitHub release, push the release tag, or dispatch signing while
the publication hold remains in effect.
