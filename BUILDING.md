# Building StarCompanion

The supported release build uses Python 3.12 on GitHub-hosted Windows and
Ubuntu runners. All Python and GitHub Action inputs are pinned.

## Test from source

```bash
python -m venv .venv
python -m pip install --require-hashes -r requirements/dev.txt
python -m pytest
```

Tests use synthetic fixtures and do not require a game installation.

## Build frozen applications

```bash
python -m pip download --require-hashes --only-binary=:all: \
  --dest build/wheelhouse -r requirements/build.txt
python -m venv build/offline-build
```

Install `requirements/build.txt` into that environment with `--no-index`, then
run:

```bash
python packaging/write_version_info.py
pyinstaller packaging/starcompanion.spec
```

Windows produces `StarCompanion.exe` and `starcompanion-cli.exe`; Ubuntu
produces equivalent files without `.exe`. The Windows version resources are
generated from `pyproject.toml` and checked before artifacts are uploaded.

The authoritative commands, system packages, packaging checks, offline smoke
tests, and uploaded file set are in [.github/workflows/ci.yml](.github/workflows/ci.yml).
Release builds must use that workflow so their source commit and build origin
remain independently verifiable.
