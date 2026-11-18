# -*- mode: python ; coding: utf-8 -*-
# Empaquetado de la app de macOS (arm64, Apple Silicon). Uso: pyinstaller PanelInventario.spec
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

VERSION = re.search(r'APP_VERSION = "([^"]+)"', Path("app/config.py").read_text()).group(1)

a = Analysis(
    ["panel_inventario.py"],
    pathex=["."],
    datas=[("static", "static")],
    hiddenimports=collect_submodules("uvicorn") + ["keyring.backends.macOS", "app.main", "app.desktop", "truststore", "certifi"],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PyQt5", "PyQt6", "PySide6", "gi"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Panel Inventario",
    console=False,
    target_arch="arm64",
    codesign_identity=None,  # sin cuenta de Apple: firma ad-hoc en el workflow
)
coll = COLLECT(exe, a.binaries, a.datas, name="Panel Inventario")
app = BUNDLE(
    coll,
    name="Panel Inventario.app",
    icon=None,
    bundle_identifier="com.thothalpha.panelinventario",
    version=VERSION,
    info_plist={
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "CFBundleDisplayName": "Panel Inventario",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.business",
        # La interfaz se sirve desde el servidor local (http://127.0.0.1)
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
