# Third-party notices

StarCompanion source is Apache-2.0. Frozen releases also contain the CPython
runtime and the following pinned open-source Python packages and their
transitive dependencies. Exact versions and hashes are recorded in
`requirements/runtime.txt` and `sbom/starcompanion-runtime.cdx.json`.

| Component | License |
|---|---|
| annotated-types | MIT |
| cffi | MIT-0 |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| Jinja2 | BSD-3-Clause |
| MarkupSafe | BSD-3-Clause |
| pycparser | BSD-3-Clause |
| pydantic and pydantic-core | MIT |
| PySide6, PySide6 Addons, PySide6 Essentials, and shiboken6 | LGPL-3.0-only |
| typing-extensions | PSF-2.0 |
| typing-inspection | MIT |
| tzdata | Apache-2.0 |
| zstandard | BSD-3-Clause |
| CPython | PSF-2.0 |

StarCompanion uses LGPL-3.0-only for the Qt for Python components. The release
license bundle includes LGPL-3.0 and the incorporated GPL-3.0 terms. Corresponding
source and license information are available from the
[Qt for Python project](https://code.qt.io/cgit/pyside/pyside-setup.git/) and
[Qt licensing documentation](https://www.qt.io/licensing/open-source-lgpl-obligations).
StarCompanion does not modify those components.

Package names, versions, source references, authors, license identifiers, and
dependency relationships remain available in the CycloneDX SBOM shipped with
each release. License and notice files collected from the pinned build
environment are included as `THIRD_PARTY_LICENSES.txt`.
