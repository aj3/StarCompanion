# Software bill of materials

`starcompanion-runtime.cdx.json` is the reproducible CycloneDX 1.6 inventory
for the application and its locked runtime dependencies. It intentionally
excludes pytest, PyInstaller, vulnerability scanners, and SBOM tooling because
those packages are not bundled into the shipped application.
The root component declares the project's Apache-2.0 license; validation fails
if that identifier drifts from `pyproject.toml`.

Generate and validate it with the separately locked tooling environment:

```powershell
build\supply-tools\Scripts\cyclonedx-py requirements requirements\runtime.txt --pyproject pyproject.toml --mc-type application --sv 1.6 --output-reproducible --of JSON --output-file sbom\starcompanion-runtime.cdx.json
python packaging\finalize_sbom.py sbom\starcompanion-runtime.cdx.json
python packaging\verify_sbom.py sbom\starcompanion-runtime.cdx.json
```

Release CI regenerates the document and rejects an uncommitted difference.
