"""CSV 목록(list_csv_names)의 '편집/저장 상태' 연필 마커 델리게이트.

각 CSV 항목 우측 끝에 상태별 연필 아이콘을 overlay 한다(배경 상태색·선택·텍스트는
super().paint() 그대로). 상태는 항목 data(EditMarkDelegate.STATE_ROLE)에 문자열로 들어온다.

상태 3종(모두 '로드된 CSV' 기준 — ViewerWindow._compute_mark_state 가 판정):
  - 'white'  : 저장(.viewer) 이력이 없는데 분석을 처음 바꾸기 시작함(미저장 신규 편집)
  - 'green'  : .viewer 에서 불러온 직후 / 저장한 직후(저장 시점과 변경사항 없음)
  - 'yellow' : green 상태에서 또 바꿨고 아직 저장 안 함

⚠ 아이콘 pixmap 은 생성자에서 1회 로드해 재사용(목록 paint 핫패스에서 재로딩 없음).
  목록 행 수는 적어(파일 개수) overlay 비용은 무시 가능.
"""
from PyQt6.QtWidgets import QStyledItemDelegate
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap


class EditMarkDelegate(QStyledItemDelegate):
    STATE_ROLE = Qt.ItemDataRole.UserRole + 17     # 항목에 상태 문자열을 담는 전용 role
    ICON_PX = 14                                   # 연필 아이콘 한 변(px)
    RIGHT_MARGIN = 4                               # 우측 끝에서 띄울 여백(px)

    # 상태 문자열 → 리소스 파일명
    _FILES = {
        'white':  'image_pencil_white.png',
        'green':  'image_pencil_green.png',
        'yellow': 'image_pencil_yellow.png',
    }

    def __init__(self, icon_path, parent=None):
        super().__init__(parent)
        # 상태별 pixmap 1회 로드(없으면 None → 그 상태는 그냥 안 그림).
        self._pix = {}
        for state, fn in self._FILES.items():
            pm = QPixmap(f"{icon_path}/{fn}")
            self._pix[state] = pm if not pm.isNull() else None

    def paint(self, painter, option, index):
        super().paint(painter, option, index)      # 배경(상태색)·선택·텍스트 정상 렌더
        state = index.data(self.STATE_ROLE)
        pm = self._pix.get(state) if state else None
        if pm is None:
            return
        r = option.rect
        size = self.ICON_PX
        x = r.right() - size - self.RIGHT_MARGIN
        y = r.top() + (r.height() - size) // 2      # 세로 중앙
        painter.drawPixmap(x, y, size, size, pm)

    def sizeHint(self, option, index):
        # 항목이 아이콘보다 낮아지지 않게 최소 높이 보장(텍스트는 우측 여백으로 살짝 가릴 수 있음 — 허용).
        s = super().sizeHint(option, index)
        return QSize(s.width(), max(s.height(), self.ICON_PX + 2))
