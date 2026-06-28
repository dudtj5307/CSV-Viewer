"""CSV Viewer 진입점.

각 실행이 독립 프로세스로 ViewerWindow 하나를 띄운다. 다른 응용 SW가
`CSV Viewer.exe [CSV폴더경로]` 를 여러 번 실행하면 그때마다 독립된 창이 새로 뜬다.
(onedir 빌드라 압축해제가 없어 매 실행 콜드스타트가 가볍다 → 백엔드/IPC 불필요.)

인자로 CSV 폴더를 주면 그 폴더를 바로 연다. 인자가 없거나 잘못된 경로면 빈 창을
먼저 띄운 뒤 그 위에 폴더 선택창을 올린다(취소하면 빈 화면 그대로 유지).
"""

import os
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

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


def main():
    setup_std_streams()
    app = QApplication(sys.argv)

    # [임시 디버그] CSVV_GRAPHTEST 면 더미 데이터로 그래프만 띄워 frozen GL 렌더 크래시 지점을 진단.
    if os.environ.get("CSVV_GRAPHTEST"):
        print("GRAPHTEST: importing GraphWindow", flush=True)
        from GUI.gui_graph import GraphWindow
        print("GRAPHTEST: creating window", flush=True)
        w = GraphWindow()
        w.set_data(["x", "y", "z"], [[str(i), str(i * 2), str(i * 3)] for i in range(5)], "graphtest")
        w.combo_x.setCurrentIndex(w.combo_x.findData(0))
        w.combo_y.setCurrentIndex(w.combo_y.findData(1))
        w.combo_z.setCurrentIndex(w.combo_z.findData(2))
        print("GRAPHTEST: showing (GL render starts)", flush=True)
        w.show()
        QTimer.singleShot(3000, app.quit)
        rc = app.exec()
        print("GRAPHTEST: exec returned", rc, flush=True)
        return rc

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    folder = arg if (arg and os.path.isdir(arg)) else None

    # 인자 폴더가 유효하면 그대로 열고, 없거나 무효면 빈 창으로 시작한다.
    viewer = ViewerWindow(resource_dir(), os.path.normpath(folder) if folder else None)
    viewer.show()
    viewer.raise_()
    viewer.activateWindow()

    # 폴더 인자가 없으면: 빈 창을 먼저 그린 뒤(이벤트 루프 진입 후) 그 위에 폴더 선택창을
    # 띄운다. 선택 없이 닫으면 빈 화면 상태 그대로 유지한다.
    if not folder:
        QTimer.singleShot(0, viewer.open_csv_folder)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
