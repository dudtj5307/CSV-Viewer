"""ESC 연타로 창을 닫는 안내 토스트 (독립 재사용 컴포넌트).

ViewerWindow(gui_viewer) 와 GraphWindow(gui_graph) 가 공유한다 — 첫 ESC 는 화면
가운데 둥근 토스트("Press ESC again to exit")만 띄우고, INTERVAL_SEC 이내에 다시 ESC 를
누르면 host 창을 close() 한다.

사용법:
    self.esc_toast = EscCloseToast(self)   # self = 닫을 대상 top-level 창
    # keyPressEvent 의 ESC 분기에서:
    self.esc_toast.handle_esc()            # 첫 ESC=안내, 간격내 재-ESC=창 닫힘

⚠ 연타 유효 간격 = 토스트 노출 시간(둘을 INTERVAL_SEC 하나로 의도적으로 묶음, 토스트=
  int(INTERVAL_SEC*1000)ms). 독립값으로 분리하지 말 것.
⚠ 자동생성 GUI/ui/widget_esc.py 는 투박한 회색 박스라, 여기서 둥근 알약 + 그라데이션 +
  그림자로 다시 입힌다(_style_toast). 생성본은 건드리지 않는다.
"""

import time

from PyQt6.QtWidgets import QWidget, QGraphicsDropShadowEffect
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from GUI.ui.widget_esc import Ui_WidgetESC


class EscCloseToast(QWidget):
    # 첫 ESC 후 이 시간(초) 이내 재-ESC 면 host 를 닫는다. 토스트 노출 시간도 같은 값.
    INTERVAL_SEC = 0.5

    def __init__(self, host):
        # host = 닫을 대상 창(부모). 토스트는 host 위에 뜨는 자식 오버레이.
        super().__init__(host)
        self._host = host
        self._last_esc_time = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.ui = Ui_WidgetESC()
        self.ui.setupUi(self)
        self._style_toast()
        self.hide()

    def _style_toast(self):
        # 투박한 회색 박스 → 둥근 알약 + 위→아래 옅은 그라데이션 + 가장자리로 번지는 부드러운 그림자.
        label = self.ui.label_esc
        # 첫 ESC 는 안내만 띄우고 간격 내 다시 누르면 닫힘 → "다시 누르면 나간다"를 간결히 전달
        label.setText("Press ESC again to exit")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # 알약 폭은 문구 길이에 맞춰 자동 산정 (문구가 바뀌어도 잘리지 않게)
        PAD_H, PILL_H, MARGIN = 28, 64, 34     # PAD_H=좌우 안쪽 여백, MARGIN=그림자 번질 여백
        PILL_W = label.fontMetrics().horizontalAdvance(label.text()) + PAD_H * 2
        label.setFixedSize(PILL_W, PILL_H)
        label.move(MARGIN, MARGIN)
        label.setStyleSheet(
            "QLabel {"
            "  color: rgba(248, 250, 252, 235);"
            "  background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            "      stop:0 rgba(118, 137, 176, 200), stop:1 rgba(82, 95, 122, 200));"
            "  border-radius: 18px;"
            "}"
        )
        # 가장자리로 갈수록 옅어지는 그림자(글로우) → 박스가 화면 위에 부드럽게 떠 보이게
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(26)
        shadow.setColor(QColor(20, 25, 40, 110))
        shadow.setOffset(0, 6)
        label.setGraphicsEffect(shadow)
        # 그림자 여백까지 포함한 크기 (_show_message 가 이 크기로 창 가운데 정렬)
        self.resize(PILL_W + 2 * MARGIN, PILL_H + 2 * MARGIN)

    def handle_esc(self):
        """ESC 1회 처리. 간격 내 연타면 host.close(), 아니면 안내 토스트."""
        if time.time() - self._last_esc_time < self.INTERVAL_SEC:  # ESC 연타 간격(초)
            self._host.close()
        self._last_esc_time = time.time()    # Update last esc pressed time
        self._show_message()

    def _show_message(self):
        pos_x = (self._host.width() - self.width()) // 2
        pos_y = (self._host.height() - self.height()) // 2
        self.setGeometry(pos_x, pos_y, self.width(), self.height())
        self.show()
        QTimer.singleShot(int(self.INTERVAL_SEC * 1000), self.hide)  # 토스트 표시 시간 = 연타 유효 시간
