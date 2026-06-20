from PyQt6.QtCore import QAbstractTableModel, pyqtSignal, Qt
from PyQt6.QtGui import QBrush

from utils.view_state import color_to_str, str_to_color

class CSVTableModel(QAbstractTableModel):
    load_fail = pyqtSignal(str)  # (csv_path)
    def __init__(self, data, csv_path):
        super().__init__()
        self.csv_path = csv_path

        # CSV Header & Data (rows: 연속된 데이터 행 리스트)
        self.headers = data[0]
        self.rows = data[1:]

        # Highlight cells - {(row, col): QColor} (source model 좌표 기준)
        self.highlight_cells = {}

        # Valid Flag
        self.valid = True

    def rowCount(self, parent=None):
        return len(self.rows)

    def columnCount(self, parent=None):
        return len(self.headers) if self.headers else 0

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not self.valid:
            return None
        row, col = index.row(), index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self.rows[row][col]
        elif role == Qt.ItemDataRole.BackgroundRole:
            color = self.highlight_cells.get((row, col))
            if color is not None:
                return QBrush(color)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if not self.valid or role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.headers):
                return self.headers[section]
            return None
        return str(section + 1)

    def highlight_cell(self, color, cell_indexes):
        # color=None -> 전체 하이라이트 해제 (기존 셀만 갱신)
        if color is None:
            if not self.highlight_cells:
                return
            keys = list(self.highlight_cells.keys())
            self.highlight_cells.clear()
            self._emit_cells_changed(keys)
            return

        # Color selected cells
        if not cell_indexes:
            return
        for index in cell_indexes:
            self.highlight_cells[(index.row(), index.column())] = color
        self._emit_cells_changed([(i.row(), i.column()) for i in cell_indexes])

    def _emit_cells_changed(self, cells):
        # 변경된 셀들의 bounding box 한 번만 갱신 (모델 전체 reset 회피)
        rows = [r for r, _ in cells]
        cols = [c for _, c in cells]
        top_left = self.index(min(rows), min(cols))
        bottom_right = self.index(max(rows), max(cols))
        self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.BackgroundRole])

    def highlight_rows(self, color, source_rows):
        # 값별 색칠: 주어진 소스 행들의 '모든 열' 셀을 highlight_cells에 직접 기록한다.
        # 수동 셀 색칠(highlight_cell)과 같은 저장소를 쓰므로, 이후 그 행의 일부 셀만
        # 다른 색으로 덮어쓰면 그 (row, col) 키만 갱신된다(우선순위 충돌·폴백 로직 없음).
        # ⚠ 셀마다 QModelIndex를 만들면 그게 병목이라, (row, col) 좌표로 직접 기록하고
        #   dataChanged는 셀 리스트 없이 bounding box 한 번만 emit한다(뷰는 보이는 셀만 다시 그림).
        if not source_rows:
            return
        ncols = len(self.headers)
        if ncols == 0:
            return
        if color is None:
            for r in source_rows:
                for c in range(ncols):
                    self.highlight_cells.pop((r, c), None)
        else:
            for r in source_rows:
                for c in range(ncols):
                    self.highlight_cells[(r, c)] = color
        top_left = self.index(min(source_rows), 0)
        bottom_right = self.index(max(source_rows), ncols - 1)
        self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.BackgroundRole])

    # ---------- 영속화(.viewer): 셀 하이라이트 ----------
    def export_highlights(self):
        """{(row,col): QColor} → {색문자열: {열: [행, ...]}} (색→열로 묶어 좌표쌍 반복 제거).
        보통 행이 열보다 압도적으로 많아, 열 기준으로 행을 모으면 셀마다 반복되던 열 인덱스·
        대괄호가 사라져 파일이 작아진다(구포맷 {색: [[row,col],...]} 대비 bulk 색칠에서 ~절반).
        그룹화는 Ctrl+S/로드 때만 도는 O(셀 수) 1회 루프라 핫패스 아님 — 메모리 highlight_cells 는
        O(1) 페인트를 위해 (row,col) 키를 그대로 유지하고, 이 포맷은 '저장용'으로만 쓴다."""
        grouped = {}
        for (row, col), color in self.highlight_cells.items():
            grouped.setdefault(color_to_str(color), {}).setdefault(col, []).append(row)
        return grouped

    def restore_highlights(self, grouped):
        """export_highlights 역(逆). 모델 생성 직후(뷰 부착 전) 호출 → dataChanged emit 불필요.
        새 포맷 {색: {열: [행]}} 과 구포맷 {색: [[행,열], ...]} 을 모두 복원(이미 저장된 .viewer 하위호환).
        열 키는 int 로 받는다(저장 직후 in-memory=int 키, 디스크 JSON 로드=str 키 둘 다 호환).
        파일 범위를 벗어난 좌표는 무시(혹시 모를 불일치 방어)."""
        cells = {}
        nrows, ncols = len(self.rows), len(self.headers)
        for hexstr, payload in (grouped or {}).items():
            color = str_to_color(hexstr)
            if isinstance(payload, dict):
                # 새 포맷: {열: [행, ...]}
                for col, rows in payload.items():
                    try:
                        c = int(col)
                    except (TypeError, ValueError):
                        continue
                    if 0 <= c < ncols:
                        for row in rows:
                            if 0 <= row < nrows:
                                cells[(row, c)] = color
            else:
                # 구포맷: [[행,열], ...]
                for rc in payload:
                    row, col = rc[0], rc[1]
                    if 0 <= row < nrows and 0 <= col < ncols:
                        cells[(row, col)] = color
        self.highlight_cells = cells
