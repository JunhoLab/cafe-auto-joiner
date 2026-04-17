from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_dir = Path(__file__).resolve().parent
datas = []
playwright_bundle = project_dir / "ms-playwright"
if playwright_bundle.exists():
    datas.append((str(playwright_bundle), "ms-playwright"))

hiddenimports = (
    collect_submodules("playwright")
    + collect_submodules("PySide6")
    + collect_submodules("openpyxl")
)


a = Analysis(
    ["main.py"],
    pathex=[str(project_dir), str(project_dir / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CafeAutoJoiner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CafeAutoJoiner",
)
