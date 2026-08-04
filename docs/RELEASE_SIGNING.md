# Windows release signing

StarCompanion's ordinary push and pull-request builds remain unsigned and do
not receive signing credentials. Authenticode is available only through a
manual `CI` workflow dispatch with `sign_release` enabled. That job is bound to
the protected `release-signing` GitHub environment and runs only after the full
Windows/Ubuntu test matrix, Ubuntu supply-chain audit, Windows offline build,
and unsigned packaged smoke succeed.

## One-time GitHub configuration

Create a GitHub environment named `release-signing`. Require approval from a
release maintainer, prevent self-review, restrict deployment branches to the
release branch/tag policy, and store these settings in that environment:

| Kind | Name | Value |
|---|---|---|
| Secret | `WINDOWS_CODESIGN_PFX_BASE64` | Base64 encoding of the complete PFX bytes, with no data-URI prefix |
| Secret | `WINDOWS_CODESIGN_PFX_PASSWORD` | PFX import password |
| Variable | `WINDOWS_CODESIGN_CERT_THUMBPRINT` | Expected 40-character SHA-1 certificate thumbprint |
| Variable | `WINDOWS_CODESIGN_TIMESTAMP_URL` | Operator-selected RFC 3161 timestamp-service URL |

Do not add the PFX or password to repository files, Actions variables, command
arguments, artifacts, caches, or logs. Rotate the environment secrets before
certificate renewal and update the expected thumbprint in the same reviewed
change window.

## Signing controls

`packaging/sign_windows.ps1` decodes the PFX only into a random runner temporary
directory, imports it as non-exportable into the current-user certificate
store, and selects it by the configured thumbprint. The script requires the
code-signing extended key usage and a currently valid certificate. Both GUI and
CLI executables must be present and are signed together with SHA-256 plus an
RFC 3161 SHA-256 timestamp.

Each executable must then pass both `signtool verify /pa /all` and PowerShell's
Authenticode verification with the expected signer and a timestamp
certificate. Newly imported certificates and the temporary PFX are removed in
a `finally` block. Any absent setting, thumbprint mismatch, bad certificate,
missing timestamp, partial artifact set, or verification failure stops the job.

The job emits `authenticode-report.json`. The cross-platform report verifier
binds the certificate metadata and post-signing SHA-256 values to both files.
The complete disconnected packaged smoke then runs against the signed files and
regenerates `release-manifest.json` with the signature report and signed hashes.
Only that output is uploaded as `StarCompanion-windows-signed`.

## External service disclosure

The timestamp URL is deliberately an operator-configured variable rather than
a hard-coded application dependency. During a signed release, `signtool`
contacts that external timestamp authority once per executable. This is a CI
release operation, not application runtime behavior. StarCompanion itself
continues to have no updater, telemetry, or required network access.

Before choosing a timestamp authority, review its availability, retention,
privacy, certificate-policy, and terms. Record the selected provider and URL in
the release notes. The signed RFC 3161 token allows the signature to remain
valid after the publisher certificate expires, subject to platform trust and
revocation policy.

## Release procedure

1. Push or tag the exact reviewed release-candidate commit.
2. From GitHub Actions, dispatch `CI` against that ref with `sign_release=true`.
3. Approve the `release-signing` environment deployment after the test,
   supply-chain, and unsigned build jobs pass.
4. Download only `StarCompanion-windows-signed` from that run.
5. Confirm the run SHA matches the intended release commit and inspect
   `release-manifest.json` and `authenticode-report.json`.
6. On a clean Windows system, verify both executables' Digital Signatures UI or
   run `signtool verify /pa /all /v <file>` before publishing.

Never re-sign an artifact from an unreviewed or different commit, and never
substitute the unsigned artifact under a signed release name.
