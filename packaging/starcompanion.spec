# PyInstaller spec: one-file Windows build of the GUI.
#
#   pip install pyinstaller
#   pyinstaller packaging/starcompanion.spec
#
# Templates and profiles are package data loaded at runtime by path, so they
# must be collected explicitly -- PyInstaller only follows imports.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

SRC = Path(SPECPATH).parent / "src"

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
    hiddenimports=["starcompanion.sources.contracts_ini", "starcompanion.sources.datacore_source"],
    hookspath=[],
    runtime_hooks=[],
    # Qt ships far more than this needs; dropping the unused modules keeps the
    # one-file build from ballooning.
    excludes=[
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtBluetooth",
        "tkinter", "matplotlib", "numpy",
    ],
    noarchive=False,
)

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
)
