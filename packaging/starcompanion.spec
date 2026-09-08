# PyInstaller spec: one-file Windows and Ubuntu builds of the GUI.
#
#   pip install pyinstaller
#   python packaging/build_release.py
#
# Templates and profiles are package data loaded at runtime by path, so they
# must be collected explicitly -- PyInstaller only follows imports.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


def verified_python_binaries(entries):
    """Reject DLLs collected from unrelated host applications on Windows."""
    if sys.platform != "win32":
        return entries
    filtered = [
        entry
        for entry in entries
        if not (
            Path(entry[0]).name.casefold() == "ucrtbase.dll"
            or Path(entry[0]).name.casefold().startswith("api-ms-win-")
            or Path(entry[0]).name.casefold().startswith("ext-ms-win-")
        )
    ]
    allowed_roots = (Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve())
    foreign = []
    for entry in filtered:
        source = Path(entry[1]).resolve()
        if not any(source == root or root in source.parents for root in allowed_roots):
            foreign.append(source)
    if foreign:
        details = "\n".join(f"  {path.name}: {path}" for path in foreign)
        raise SystemExit(
            "Refusing to package binaries from outside the selected Python runtime:\n"
            + details
        )
    return filtered


SRC = Path(SPECPATH).parent / "src"
VERSION_INFO = Path(SPECPATH).parent / "build" / "version-info"
GUI_VERSION = (
    str(VERSION_INFO / "StarCompanion.exe.version-info.txt")
    if sys.platform == "win32"
    else None
)
CLI_VERSION = (
    str(VERSION_INFO / "starcompanion-cli.exe.version-info.txt")
    if sys.platform == "win32"
    else None
)

datas = [
    (str(SRC / "starcompanion" / "templates"), "starcompanion/templates"),
    (str(SRC / "starcompanion" / "profiles"), "starcompanion/profiles"),
]
datas += collect_data_files("zstandard")

a = Analysis(
    # entry.py, not gui/app.py: PyInstaller runs its target as __main__, which
    # breaks the package's relative imports when a module is used directly.
    [str(Path(SPECPATH) / "entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "starcompanion.helper_process",
        "starcompanion.operations",
        "starcompanion.sources.contracts_ini",
        "starcompanion.sources.datacore_source",
    ],
    hookspath=[],
    runtime_hooks=[],
    # Qt ships far more than this needs; dropping the unused modules keeps the
    # one-file build from ballooning.
    excludes=[
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtBluetooth",
        "PySide6.QtNetwork",
        "tkinter", "matplotlib", "numpy",
    ],
    noarchive=False,
)
a.binaries = verified_python_binaries(a.binaries)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="StarCompanion",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=GUI_VERSION,
)


# ── Command line build ───────────────────────────────────────────────────────
# A second executable, console=True so --help and command output are visible.
# The GUI build has no console attached and cannot print.

cli_analysis = Analysis(
    [str(Path(SPECPATH) / "entry_cli.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "starcompanion.helper_process",
        "starcompanion.operations",
        "starcompanion.sources.contracts_ini",
        "starcompanion.sources.datacore_source",
        "starcompanion.sources.scmdb",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # The CLI never opens a window, so Qt can go entirely.
        "PySide6", "shiboken6",
        "tkinter", "matplotlib", "numpy",
    ],
    noarchive=False,
)
cli_analysis.binaries = verified_python_binaries(cli_analysis.binaries)

cli_pyz = PYZ(cli_analysis.pure)

cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    cli_analysis.binaries,
    cli_analysis.datas,
    [],
    # Not "starcompanion": Windows filenames are case-insensitive, so that
    # collides with StarCompanion.exe and silently overwrites the GUI build.
    name="starcompanion-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=CLI_VERSION,
)
