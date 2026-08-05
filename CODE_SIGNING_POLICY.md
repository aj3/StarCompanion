# Code signing policy

StarCompanion is applying to the SignPath Foundation open source code-signing
program. Once accepted: **Free code signing provided by
[SignPath.io](https://signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/).** Until then, release notes state
clearly that Windows binaries are unsigned.

## Team roles

- Authors and committers: [aj3](https://github.com/aj3)
- Reviewers for contributions from non-committers:
  [aj3](https://github.com/aj3)
- Release signing approver: [aj3](https://github.com/aj3)

Role holders must use multi-factor authentication for GitHub and SignPath.
Changes from contributors without commit access require maintainer review.
Every release-signing request requires a separate manual approval.

## Source and build policy

- The authoritative source is
  [github.com/aj3/StarCompanion](https://github.com/aj3/StarCompanion).
- Signed artifacts must be built from a public tagged commit by the checked-in
  GitHub Actions workflow using GitHub-hosted runners.
- Dependency and action versions are pinned; Python packages are installed from
  hash-verified locks and an offline wheelhouse.
- The signing workflow runs only after Windows, Ubuntu, supply-chain, SBOM,
  package-metadata, frozen-build, and packaged offline tests pass.
- The protected `release-signing` environment provides the manual approval
  boundary. Signing is never enabled for ordinary pushes or pull requests.

## Artifact policy

Only `StarCompanion.exe` and `starcompanion-cli.exe`, built from this
repository, may receive the project signature. The SignPath artifact
configuration requires both files together and enforces their product name,
product version, file version, company, copyright, and original filename.
Third-party binaries are not signed as StarCompanion components.

Signed files are returned with the SBOM, Apache-2.0 license, project notice,
third-party notices, and integrity manifest. CI verifies the SignPath
Foundation signer, timestamp, exact two-file set, and post-signing SHA-256
values before publishing anything.

## Privacy

See [PRIVACY.md](PRIVACY.md). StarCompanion has no telemetry or automatic
network transfer. Signing-related network access occurs only in GitHub Actions
between GitHub and SignPath during an approved release; it is not application
runtime behavior.
