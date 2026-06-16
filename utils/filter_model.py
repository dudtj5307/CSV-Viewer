from PyQt6.QtCore import QSortFilterProxyModel, Qt

class CSVFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        # {col: frozenset(hidden_texts)} - 컬럼별 독립 필터
        self.column_filters = {}
        self.setDynamicSortFilter(False)

    def setFilterForColumn(self, column, hide_texts):
        # 숨길 값이 없으면 해당 컬럼 필터 해제
        if hide_texts:
            self.column_filters[column] = frozenset(hide_texts)
        else:
            self.column_filters.pop(column, None)

        # Refresh Filter
        self.layoutAboutToBeChanged.emit()
        self.blockSignals(True)
        self.invalidateRowsFilter()
        self.blockSignals(False)
        # Update GUI
        self.layoutChanged.emit()
        # 필터 표시(*) 갱신 - 해당 열 헤더 리페인트
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, column, column)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        header = super().headerData(section, orientation, role)
        # 필터가 적용된 열은 헤더 텍스트 우측에 '*' 표시
        if (orientation == Qt.Orientation.Horizontal
                and role == Qt.ItemDataRole.DisplayRole
                and section in self.column_filters):
            return f"{header} ⏷"
        return header

    def _row_passes(self, row, exclude=None):
        # column_filters를 AND로 판정. exclude 열은 건너뛴다(캐스케이딩 드롭다운용).
        for col, hidden in self.column_filters.items():
            if col == exclude:
                continue
            if row[col] in hidden:
                return False
        return True

    def column_values_excluding_self(self, column):
        """이 column을 제외한 나머지 열 필터를 통과한 행에서 column의 고유값 수집.
        반환: {value: checked}  (checked = 이 열에서 숨기지 않은 허용값)"""
        hidden = self.column_filters.get(column, frozenset())
        values = {}
        for row in self.sourceModel().rows:
            if self._row_passes(row, exclude=column):
                value = row[column]
                values[value] = value not in hidden
        return values

    def filterAcceptsRow(self, source_row, source_parent):
        if not self.column_filters:
            return True
        row = self.sourceModel().rows[source_row]
        return self._row_passes(row)

    def sort(self, column, order=None):
        pass

    def lessThan(self, left, right):
        return False
