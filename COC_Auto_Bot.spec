# -*- mode: python ; coding: utf-8 -*-

import sys
import os
import customtkinter

block_cipher = None

# Lấy đường dẫn thư viện CustomTkinter để đóng gói giao diện
ctk_path = os.path.dirname(customtkinter.__file__)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets', 'assets'),
        ('config.json', '.'),
        (ctk_path, 'customtkinter'),
    ],
    hiddenimports=[
        'customtkinter',
        'PIL',
        'PIL._tkinter_finder',
        'cv2',
        'pytesseract',
        'numpy',
    ],
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
    name='COC_Auto_Bot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Đặt True nếu muốn hiện cửa sổ đen Console để xem Log khi chạy
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None  # Đổi thành 'assets/icon.ico' nếu có file icon
)