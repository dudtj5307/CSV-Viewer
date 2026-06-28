# -*- mode: python ; coding: utf-8 -*-
# CSV Viewer - onedir(폴더형) + windowed 빌드.
#   onedir: 실행 시 압축해제가 없어 프로세스 기동이 빠르다 (단일 인스턴스 전달 경로에 유리).
#   console=False: 다른 SW에서 실행할 때 검은 콘솔창이 뜨지 않는다.

import glob
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

# 뷰어가 쓰는 리소스: png(아이콘) + ico(창 아이콘) + gif(로딩 스피너)
res_files  = [(f, 'GUI\\res') for f in glob.glob('GUI\\res\\*.png')]
res_files += [(f, 'GUI\\res') for f in glob.glob('GUI\\res\\*.ico')]
res_files += [(f, 'GUI\\res') for f in glob.glob('GUI\\res\\*.gif')]

# conda PyQt6는 Qt 런타임 DLL(Qt6Core/Gui/Widgets.dll 등)이 .pyd 옆이 아닌
# <env>\Library\bin 에 있어 PyInstaller 자동수집이 놓친다. 명시적으로 binaries에 넣으면
# PyInstaller가 이 DLL들의 의존성(ICU/zlib/pcre2 등, 같은 폴더)까지 추적해 번들한다.
QT_BIN = os.path.join(sys.prefix, 'Library', 'bin')
# 이 앱이 실제로 쓰는 Qt 모듈 (의존 DLL: ICU/zlib/pcre2 등은 PyInstaller가 자동 추적).
# ⚠ 3D 그래프(pyqtgraph GLViewWidget = QOpenGLWidget) 때문에 OpenGL Qt 모듈도 필수 —
#   Qt6OpenGL/Qt6OpenGLWidgets 가 없으면 그래프 창을 띄울 때 Qt6Core.dll 에서 크래시한다(0xc0000409).
QT_NEEDED = ('Qt6Core', 'Qt6Gui', 'Qt6Widgets', 'Qt6OpenGL', 'Qt6OpenGLWidgets')
qt_dlls = [(os.path.join(QT_BIN, f'{n}.dll'), '.') for n in QT_NEEDED]

# 3D 그래프 의존성: PyQt6 OpenGL 바인딩만 명시한다(PyInstaller 가 GLViewWidget 의 QtOpenGLWidgets
# 의존을 정적분석에서 놓칠 수 있어). PyOpenGL·pyqtgraph 는 PyInstaller 내장 hook(hook-OpenGL/
# hook-pyqtgraph)이 필요한 모듈만 알아서 수집하므로 collect_submodules 로 통째 끌어오지 않는다(과수집 방지).
# numpy 는 아래 excludes 에서 빼 자동 수집(pyqtgraph 필수). 환경 numpy 는 nomkl(openblas) 빌드라
# MKL DLL(~600MB)이 안 딸려온다(conda numpy=MKL 빌드면 거대해지므로 nomkl 로 둘 것).
graph_hidden = ['PyQt6.QtOpenGL', 'PyQt6.QtOpenGLWidgets']
# ⚠ PyInstaller 6.x 내장 numpy 훅(hook-numpy.py)은 numpy 1.x 시절 버전이라
#   numpy 2.x 의 numpy._core 하위 모듈(예: numpy._core._exceptions)을 지연 import 라
#   정적분석으로 못 잡아 번들에서 누락 → 그래프 실행 시
#   "ModuleNotFoundError: No module named 'numpy._core._exceptions'" 크래시.
#   numpy 순수 파이썬 하위 모듈을 명시 수집해 해결(DLL 은 훅의 collect_dynamic_libs 가 처리).
# ⚠ 단, collect_submodules('numpy') 는 테스트/f2py/distutils/mypy 플러그인까지 통째로
#   끌어와(279개 중 117개가 런타임 무관) PYZ 가 부푼다 → 빌드/테스트 전용 서브패키지만 거른다.
#   검증: numpy + pyqtgraph.opengl + gui_graph 가 쓰는 전 연산을 실제 실행해도 아래 _NUMPY_DROP
#   대상은 단 하나도 import 되지 않음(런타임 86개 모두 통과). 거름 후에도 numpy._core._exceptions 보존.
def _numpy_runtime_submodules():
    def drop(m):
        return ('.tests' in m or m.endswith('.tests')
                or m.startswith('numpy.f2py') or m.startswith('numpy.distutils')
                or m == 'numpy.typing.mypy_plugin' or m == 'numpy.conftest'
                or m == 'numpy._configtool' or m == 'numpy._pyinstaller')
    return [m for m in collect_submodules('numpy') if not drop(m)]

graph_hidden += _numpy_runtime_submodules()

a = Analysis(
    ['csv_viewer.py'],
    pathex=[QT_BIN],
    binaries=qt_dlls,
    datas=res_files,
    hiddenimports=graph_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # numpy 제외 해제(pyqtgraph 필수). 단 numpy.f2py(Fortran 컴파일러)는 정적분석이 의존성으로
    # 끌어오지만 런타임 무관 → excludes 로 능동 차단(hiddenimports 필터만으론 안 빠짐). 런타임 추적상
    # numpy.f2py·numpy.distutils 는 그래프 전 연산에서 한 번도 import 되지 않음(검증 완료).
    # 코드/리소스에서 안 쓰는 모듈 차단(PYZ·바인딩 축소). DLL 자체는 아래 _DROP_BIN 가 마저 제거.
    #   ssl/sqlite/QtNetwork/QtTest/QtSvg/QtSql 미사용(grep 검증). ⚠ hashlib(libcrypto)·gif/ico 는 유지.
    excludes=['scapy', 'tkinter', 'psutil', 'pandas', 'matplotlib',
              'numpy.f2py', 'numpy.distutils',
              'ssl', '_ssl', 'sqlite3', '_sqlite3',
              'PyQt6.QtNetwork', 'PyQt6.QtTest', 'PyQt6.QtSvg', 'PyQt6.QtSql'],
    noarchive=False,
    optimize=0,
)

# 미사용 DLL/Qt 플러그인 제거(용량 절감). PyInstaller 의 PyQt6 훅은 모든 Qt 플러그인을 강제 수집하고,
# conda 환경 DLL 도 의존성 추적으로 딸려와 excludes 만으론 안 빠진다 → Analysis 산출물에서 직접 거른다.
# 전부 코드/리소스 grep + pefile 의존성 검증으로 '소비자 없음' 확인된 것만:
#   tcl/tk(tkinter 제외 후 고아) · Qt6Network+qtuiotouchplugin(네트워크 미사용) ·
#   Qt6Svg/qsvg/qsvgicon(svg 리소스 없음) · libjpeg/qjpeg(jpeg 리소스 없음) · Qt6Test/QtTest ·
#   libssl/_ssl · sqlite3/_sqlite3. ⚠ 유지: libcrypto(hashlib.sha256), qgif·qico(gif/ico 리소스).
_DROP_BIN = (
    'tcl86t.dll', 'tk86t.dll',
    'qt6network.dll', 'qtuiotouchplugin.dll',
    'qt6svg.dll', 'qsvg.dll', 'qsvgicon.dll',
    'qjpeg.dll', 'libjpeg',
    'qt6test.dll', 'qttest',
    'libssl-3-x64.dll', '_ssl', 'sqlite3.dll', '_sqlite3',
)
def _keep_bin(entry):
    name = os.path.basename(entry[0]).lower()
    return not any(name == d or name.startswith(d) for d in _DROP_BIN)
a.binaries = [e for e in a.binaries if _keep_bin(e)]
a.datas    = [e for e in a.datas    if _keep_bin(e)]

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
    console=True,                  # [임시 디버그] 콘솔로 그래프 크래시 진단 — 끝나면 False 로 원복
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['GUI\\res\\button_csv_view.ico'],
    contents_directory='_python_lib',   # onedir 라이브러리 폴더명 (_internal → _python_lib). EXE 인자임에 주의
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    # ⚠ UPX 는 numpy 의 컴파일된 확장(.pyd)·openblas DLL 을 손상시켜 import 실패를 자주 일으킨다 →
    #   numpy 관련 바이너리는 압축 제외(약간 커지지만 그래프 numpy 로딩 안정성 우선).
    upx_exclude=['*numpy*', '_multiarray*.pyd', 'libopenblas*.dll', 'openblas*.dll'],
    name='CSV Viewer',
)
