from PyQt6.QtWidgets import QStyledItemDelegate
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPen, QColor


class CompareBorderDelegate(QStyledItemDelegate):
    """Δ 셀을 선택하면 그 차이가 비교한 두 부모(기준)열 셀에 테두리를 덧그린다.
    - 현재 행(R(n))   = 파랑
    - 이전 행(R(n-1)) = 빨강  (스냅샷 시점의 '이전 보이는 행'; 필터로 숨겨졌으면 표시 안 함)

    좌표는 프록시 (row, col). 선택이 바뀔 때 ViewerWindow 가 set_marks 로 갱신한다.
    렌더는 super().paint()(=기본 셀: 배경 하이라이트·텍스트·Δ italic 등)를 그대로 두고
    그 위에 1px 테두리만 overlay 한다.

    ⚠ 색은 의도적으로 고정 상수(BLUE/RED) — 사용자가 직접 맞춰가며 조정.
    """
    RED = QColor(230, 40, 40)    # 이전 행 R(n-1)
    BLUE = QColor(0, 110, 255)   # 현재 행 R(n)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._blue = None   # (proxy_row, proxy_col) | None
        self._red = None    # (proxy_row, proxy_col) | None

    def set_marks(self, blue, red):
        # 변경 없으면 False(호출측이 불필요한 viewport 갱신을 피하도록)
        if (blue, red) == (self._blue, self._red):
            return False
        self._blue, self._red = blue, red
        return True

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        rc = (index.row(), index.column())
        color = self.BLUE if rc == self._blue else self.RED if rc == self._red else None
        if color is None:
            return
        painter.save()
        pen = QPen(color)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # 1px 테두리가 셀 경계 안에 온전히 들어오도록 우/하단을 1 줄인다
        painter.drawRect(option.rect.adjusted(1, 1, -1, -1))
        painter.restore()
