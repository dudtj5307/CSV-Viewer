"""CSV Viewer 진입점.

각 실행이 독립 프로세스로 ViewerWindow 하나를 띄운다. 다른 응용 SW가
`CSV Viewer.exe [CSV폴더경로]` 를 여러 번 실행하면 그때마다 독립된 창이 새로 뜬다.
(onedir 빌드라 압축해제가 없어 매 실행 콜드스타트가 가볍다 → 백엔드/IPC 불필요.)

인자로 CSV 폴더를 주면 그 폴더를, 없거나 잘못된 경로면 폴더 선택창을 연다.
"""

import os
import sys

from PyQt6.QtWidgets import QApplication, QFileDialog

from GUI.gui_viewer import ViewerWindow


def resource_dir():
    # PyInstaller 빌드 → sys._MEIPASS, 개발 실행 → 이 파일 위치 기준
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "GUI", "res")


def setup_std_streams():
    # windowed(console=False) 빌드에서는 stdout/stderr가 None → 기존 print()가 크래시한다.
    # 코드 곳곳의 print를 안전하게 흘려보내도록 None이면 devnull로 대체.
    if sys.stdout is None or sys.stderr is None:
        sink = open(os.devnull, "w")
        if sys.stdout is None:
            sys.stdout = sink
        if sys.stderr is None:
            sys.stderr = sink


def resolve_folder(folder):
    # 인자 폴더가 유효하면 그대로, 아니면(없음/무효) 폴더 선택창. 취소 시 None.
    if folder and os.path.isdir(folder):
        return folder
    picked = QFileDialog.getExistingDirectory(None, "Select CSV folder", folder or "")
    return picked or None


def main():
    setup_std_streams()
    app = QApplication(sys.argv)

    folder = resolve_folder(sys.argv[1] if len(sys.argv) > 1 else None)
    if not folder:
        return 0   # 인자 없이 실행 후 폴더 선택 취소 → 열 창이 없으니 종료

    viewer = ViewerWindow(resource_dir(), os.path.normpath(folder))
    viewer.show()
    viewer.raise_()
    viewer.activateWindow()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
