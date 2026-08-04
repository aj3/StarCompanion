# Dependency locks

The `.in` files contain intentionally selected direct dependencies. Their
matching `.txt` files are generated transitive locks with SHA-256 hashes and
are the only dependency inputs used by CI and release builds.

Regenerate all universal CPython 3.12+ locks from the repository root with the
pinned lock compiler (which is also captured and audited in `tools.txt`):

```powershell
python -m venv build\lock-tools
build\lock-tools\Scripts\python -m pip install uv==0.12.1
build\lock-tools\Scripts\uv pip compile requirements\runtime.in --universal --python-version 3.12 --generate-hashes --upgrade --output-file requirements\runtime.txt
build\lock-tools\Scripts\uv pip compile requirements\dev.in --universal --python-version 3.12 --generate-hashes --upgrade --output-file requirements\dev.txt
build\lock-tools\Scripts\uv pip compile requirements\build.in --universal --python-version 3.12 --generate-hashes --upgrade --output-file requirements\build.txt
build\lock-tools\Scripts\uv pip compile requirements\tools.in --universal --python-version 3.12 --generate-hashes --upgrade --output-file requirements\tools.txt
```

Universal resolution treats CPython 3.12 as the supported lower bound and keeps
platform markers needed by Windows and Linux CI. Do not hand-edit generated
lock files. Updating a dependency requires reviewing the generated diff,
running the vulnerability audit, regenerating the SBOM, and completing the
offline packaged smoke test.
