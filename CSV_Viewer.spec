# -*- mode: python ; coding: utf-8 -*-
# CSV Viewer - onedir(폴더형) + windowed 빌드.
#   onedir: 실행 시 압축해제가 없어 프로세스 기동이 빠르다 (단일 인스턴스 전달 경로에 유리).
#   console=False: 다른 SW에서 실행할 때 검은 콘솔창이 뜨지 않는다.

import glob
import os
import sys

# 뷰어가 쓰는 리소스: png(아이콘) + ico(창 아이콘) + gif(로딩 스피너)
res_files  = [(f, 'GUI\\res') for f in glob.glob('GUI\\res\\*.png')]
res_files += [(f, 'GUI\\res') for f in glob.glob('GUI\\res\\*.ico')]
res_files += [(f, 'GUI\\res') for f in glob.glob('GUI\\res\\*.gif')]

# conda PyQt6는 Qt 런타임 DLL(Qt6Core/Gui/Widgets.dll 등)이 .pyd 옆이 아닌
# <env>\Library\bin 에 있어 PyInstaller 자동수집이 놓친다. 명시적으로 binaries에 넣으면
# PyInstaller가 이 DLL들의 의존성(ICU/zlib/pcre2 등, 같은 폴더)까지 추적해 번들한다.
QT_BIN = os.path.join(sys.prefix, 'Library', 'bin')
# 이 앱이 실제로 쓰는 Qt 모듈만 (의존 DLL: ICU/zlib/pcre2 등은 PyInstaller가 자동 추적)
QT_NEEDED = ('Qt6Core', 'Qt6Gui', 'Qt6Widgets')
qt_dlls = [(os.path.join(QT_BIN, f'{n}.dll'), '.') for n in QT_NEEDED]

a = Analysis(
    ['csv_viewer.py'],
    pathex=[QT_BIN],
    binaries=qt_dlls,
    datas=res_files,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['scapy', 'tkinter', 'psutil', 'numpy', 'pandas', 'matplotlib'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # onedir: 바이너리는 COLLECT 로 분리
    name='CSV Viewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                  # windowed (콘솔창 없음)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['GUI\\res\\PPS.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CSV Viewer',
)
