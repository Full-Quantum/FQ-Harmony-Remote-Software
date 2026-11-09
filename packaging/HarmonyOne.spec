# -*- mode: python ; coding: utf-8 -*-

import os
import sys

project_root = os.path.abspath(os.path.join(SPECPATH, ".."))
source_root = os.path.join(project_root, "src")
data_root = os.path.join(source_root, "harmony_one", "data")

# PyInstaller's _tkinter hook has been observed to silently skip collecting the
# Tcl/Tk script libraries (_tcl_data/_tk_data) on this toolchain, which makes
# the built exe fail at start with "Can't find a usable init.tcl". Collect them
# explicitly from the base interpreter and fail the build if they are missing.
# The _tcl_data/_tk_data names are what pyi_rth__tkinter expects.
_tcl_dir = os.path.join(sys.base_prefix, "tcl", "tcl8.6")
_tk_dir = os.path.join(sys.base_prefix, "tcl", "tk8.6")
if not os.path.isdir(_tcl_dir) or not os.path.isdir(_tk_dir):
    raise SystemExit(
        f"Tcl/Tk library directories not found at {_tcl_dir!r} and {_tk_dir!r}; "
        "the GUI exe would not start"
    )
tcl_tk_datas = [(_tcl_dir, "_tcl_data"), (_tk_dir, "_tk_data")]

a = Analysis(
    [os.path.join(SPECPATH, 'run_harmony_one.py')],
    pathex=[source_root],
    binaries=[],
    datas=[
        (os.path.join(data_root, 'catalogs.json'), 'data'),
        (os.path.join(data_root, 'features.json'), 'data'),
        (os.path.join(data_root, 'alphabet.json'), 'harmony_one/data'),
        (os.path.join(source_root, 'harmony_one', 'vendor'), 'harmony_one/vendor'),
    ] + tcl_tk_datas,
    hiddenimports=['hid'],
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
    a.binaries,
    a.datas,
    [],
    name='HarmonyOne',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
