from PyQt6.QtWidgets import QStyledItemDelegate
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPen, QColor


class CompareBorderDelegate(QStyledItemDelegate):
    """셀 위에 테두리만 overlay 하는 델리게이트 (배경/텍스트는 super().paint() 그대로).
    두 가지 용도가 공존한다:
    - Δ 비교: Δ 셀을 선택하면 비교한 두 부모(기준)열 셀에 — 현재 행 R(n)=파랑 / 이전 행 R(n-1)=빨강
      (이전 행은 스냅샷 시점의 '이전 보이는 행'; 필터로 숨겨졌으면 표시 안 함). ViewerWindow 가 set_marks 로 갱신.
    - 검색 현재 셀 = 회색. 검색 이동(다음/이전) 때 ViewerWindow 가 set_search_mark 로 갱신.

    좌표는 모두 프록시 (row, col). Δ 테두리와 검색 테두리는 독립이라 같은 셀이면 둘 다(회색이 위) 그려진다.

    ⚠ 색은 의도적으로 고정 상수(BLUE/RED/GRAY) — 사용자가 직접 맞춰가며 조정.
    """
    RED = QColor(230, 40, 40)    # 이전 행 R(n-1)
    BLUE = QColor(0, 110, 255)   # 현재 행 R(n)
    GRAY = QColor(110, 110, 110) # 검색 현재 셀

    def __init__(self, parent=None):
        super().__init__(parent)
        self._blue = None   # (proxy_row, proxy_col) | None
        self._red = None    # (proxy_row, proxy_col) | None
        self._search = None # (proxy_row, proxy_col) | None — 검색 현재 매치 셀

    def set_marks(self, blue, red):
        # 변경 없으면 False(호출측이 불필요한 viewport 갱신을 피하도록)
        if (blue, red) == (self._blue, self._red):
            return False
        self._blue, self._red = blue, red
        return True

    def set_search_mark(self, cell):
        # 검색 현재 셀 마크. set_marks 와 동일하게 변경 시에만 True.
        if cell == self._search:
            return False
        self._search = cell
        return True

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        rc = (index.row(), index.column())
        if rc == self._blue:
            self._draw_border(painter, option, self.BLUE)
        elif rc == self._red:
            self._draw_border(painter, option, self.RED)
        if rc == self._search:        # 검색 현재 셀: Δ 테두리와 독립(같은 셀이면 위에 덧그림)
            self._draw_border(painter, option, self.GRAY)

    @staticmethod
    def _draw_border(painter, option, color):
        painter.save()
        pen = QPen(color)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # 1px 테두리가 셀 경계 안에 온전히 들어오도록 우/하단을 1 줄인다
        painter.drawRect(option.rect.adjusted(1, 1, -1, -1))
        painter.restore()
