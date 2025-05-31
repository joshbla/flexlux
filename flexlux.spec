# -*- mode: python ; coding: utf-8 -*-
import sys
import os

block_cipher = None

# Determine the correct separator for data files based on the platform
if sys.platform.startswith('win'):
    data_separator = ';'
else:
    data_separator = ':'

a = Analysis(
    ['flexlux.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/icon.png', 'assets')],
    hiddenimports=['screen_brightness_control'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='flexlux',
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
    icon='assets/icon.png' if os.path.exists('assets/icon.png') else None,
)
