# Dependency and release supply-chain policy

**Audit date:** 2026-08-04

## Locked surfaces

StarCompanion uses four separate, universal, hash-verified dependency sets:

- `runtime.txt`: 16 runtime distributions represented in the release SBOM;
- `dev.txt`: runtime plus pytest and its test-only dependencies;
- `build.txt`: runtime plus PyInstaller and its packaging dependencies; and
- `tools.txt`: isolated lock compiler, vulnerability-audit, and SBOM-generation
  tools.

The lower bound is CPython 3.12. Every package is pinned with `==`, every
distribution has one or more SHA-256 hashes, and platform markers preserve the
Windows/Linux differences. Project metadata pins direct and PEP 517 build
dependencies to the same versions.

CI actions are pinned to complete commit SHAs rather than movable tags. Checkout
credentials are not persisted.

## Current audit result

`pip-audit 2.10.1` reported **no known vulnerabilities** in runtime, dev, build,
or tools locks using the Python Packaging Advisory Database/PyPI advisory
service. This is a point-in-time known-vulnerability check, not proof that a
dependency is defect-free. CI repeats the audit on every change.

Direct runtime pins:

| Package | Version | Purpose |
|---|---:|---|
| PySide6 | 6.11.0 | Desktop GUI |
| Jinja2 | 3.1.6 | Sandboxed rendering |
| pydantic | 2.13.4 | Profile/schema validation |
| zstandard | 0.25.0 | P4K decompression |
| cryptography | 50.0.0 | P4K AES decryption |
| tzdata | 2026.3 | Frozen Windows IANA timezone fallback |

## Release gates

1. Install every CI environment with hashes enforced.
2. Audit all four lock surfaces; do not suppress an advisory without a written,
   scoped risk decision and expiry date.
3. Regenerate the reproducible CycloneDX 1.6 runtime SBOM and reject drift.
   Its root component must declare `Apache-2.0`.
4. Download release wheels with hashes, then install the build environment with
   `--no-index` from that wheelhouse.
5. Build the Windows and Ubuntu frozen executables from offline environments.
6. Launch the packaged GUI and run import/extraction plus the complete guarded
   workflow with `STARCOMPANION_ENFORCE_OFFLINE=1`, which denies Python
   IPv4/IPv6 socket creation and DNS lookup in the parent and helper.
7. Reject new network-capable Python or Qt imports unless the network-surface
   policy and external-resource inventory are deliberately revised.
8. Build the Python wheel and require `License-Expression: Apache-2.0` plus
   byte-identical bundled copies of LICENSE and NOTICE.
9. Publish executable, SBOM, LICENSE, and NOTICE SHA-256 values in
   `release-manifest.json`.
10. For a signed release, require the protected `release-signing` environment,
    sign and RFC 3161 timestamp both executables together, verify their signer
    and timestamp, rerun the disconnected packaged smoke, and bind the signed
    hashes plus `authenticode-report.json` into the manifest. See
    `RELEASE_SIGNING.md`.

Release validation installs the complete build lock from downloaded,
hash-verified wheelhouses with `--no-index`, builds both one-file executables on
Windows and Ubuntu from those isolated environments, launches the frozen GUI,
and completes the frozen import/extraction plus prepared-channel
preview/apply/rollback workflow with the offline guard enabled.

The offline guard covers StarCompanion's Python runtime. It does not claim to
be a host firewall or prove that arbitrary native OS/Qt code could never open a
connection. StarCompanion does not call Qt networking APIs, and the packaged
core workflow does not require them.
