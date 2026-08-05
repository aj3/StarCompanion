# Windows release signing

StarCompanion is applying to the SignPath Foundation open-source signing
program. Ordinary push and pull-request builds remain unsigned. After approval,
only a manual `CI` workflow dispatch with `sign_release` enabled may submit the
already-tested Windows artifact to SignPath.

## One-time SignPath setup

1. Create the StarCompanion project in SignPath and connect the public GitHub
   repository through the SignPath GitHub App.
2. Upload `.signpath/artifact-configuration.xml` as the project's artifact
   configuration. Record its slug.
3. Create a signing policy for releases. Require manual approval and allow only
   the maintained GitHub Actions workflow on GitHub-hosted runners.
4. Create a SignPath API token for a submitter that can use only this project
   and policy. Keep author, reviewer, and approver roles separated when more
   maintainers join the project.
5. Create a protected GitHub environment named `release-signing`. Require
   approval, prevent self-review when GitHub permits it, and restrict deployment
   refs to the release policy.

Store these values only in the `release-signing` environment:

| Kind | Name | Value |
|---|---|---|
| Secret | `SIGNPATH_API_TOKEN` | Narrow SignPath submitter API token |
| Variable | `SIGNPATH_ORGANIZATION_ID` | SignPath organization UUID |
| Variable | `SIGNPATH_PROJECT_SLUG` | StarCompanion project slug |
| Variable | `SIGNPATH_SIGNING_POLICY_SLUG` | Approved release policy slug |
| Variable | `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | Uploaded artifact-configuration slug |

No private key or certificate file belongs in GitHub. SignPath keeps the key in
its controlled service and returns the signed artifact after policy approval.

## Enforced artifact identity

The artifact configuration accepts one GitHub-generated ZIP and requires
exactly these project-owned PE files:

- `dist/StarCompanion.exe`
- `dist/starcompanion-cli.exe`

Both files must have the project name, version, company, copyright, and original
filename generated from `pyproject.toml`. The remaining manifest, SBOM, project
license, and third-party notice files must be present but are not signed.

After SignPath returns the archive, `packaging/report_signpath.ps1` independently
requires a valid SignPath Foundation Authenticode signature and timestamp on
both executables. `packaging/verify_offline.py` then repeats the disconnected
packaged smoke and binds the exact signed SHA-256 values, signer, timestamps,
SBOM, and license bundle into the release manifest. Any missing, extra,
partially signed, altered, or wrongly identified executable stops publication.

## Release procedure

1. Merge the reviewed release candidate and create the intended public tag.
2. Dispatch `CI` against that exact tag with `sign_release=true`.
3. Confirm every prerequisite job used GitHub-hosted runners and passed without
   a re-run, then approve the protected `release-signing` environment.
4. Review the linked SignPath request and approve it only when the source ref,
   artifact configuration, version, and signing policy are correct.
5. Download `StarCompanion-windows-signed` from that run.
6. Confirm the workflow SHA and inspect `release-manifest.json` and
   `authenticode-report.json`.
7. On a separate Windows system, run `Get-AuthenticodeSignature` or
   `signtool verify /pa /all /v` against both executables before publication.

Never sign a locally built artifact, a workflow re-run, an unreviewed commit, or
an artifact uploaded outside the checked-in release workflow.

## Runtime disclosure

SignPath and its timestamp service are release infrastructure only. The
installed application has no updater, telemetry, or required network access.
