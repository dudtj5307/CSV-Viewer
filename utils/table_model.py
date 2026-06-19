from PyQt6.QtCore import QAbstractTableModel, pyqtSignal, Qt
from PyQt6.QtGui import QBrush

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
