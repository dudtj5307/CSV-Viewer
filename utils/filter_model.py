from PyQt6.QtCore import QAbstractProxyModel, QModelIndex, Qt


class CSVFilterProxyModel(QAbstractProxyModel):
    """열별 값 필터 프록시 (행 부분집합).

    ⚠ 성능: 기존 QSortFilterProxyModel은 invalidateRowsFilter() 한 번에
    소스 행 수만큼 파이썬 filterAcceptsRow()를 호출한다. 18만 행 기준
    C++↔Python 경계를 18만 번 넘으며 측정상 **약 3.9초**가 걸렸다(필터 한 번
    적용에). 여기서는 필터 통과 행 목록(self._accepted)을 **벌크 파이썬 루프로
    한 번에** 계산하고 beginResetModel/endResetModel 로 뷰에 알린다 → 경계
    통과가 '행 수만큼'이 아니라 0이 되어 수십 ms 수준으로 떨어진다.

    공개 인터페이스(column_filters / setFilterForColumn /
    column_values_excluding_self / 헤더 '⏷' 표시)는 그대로 유지하므로
    gui_filter.py·gui_viewer.py 는 수정이 필요 없다.

    ⚠ QAbstractProxyModel 은 소스 모델의 시그널을 자동 전달하지 않는다
    (QSortFilterProxyModel 과 다름). 셀 하이라이트(소스 dataChanged) 재페인트를
    위해 setSourceModel 에서 dataChanged 를 직접 연결해 전달한다.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.column_filters = {}          # {col: frozenset(hidden_texts)} - 컬럼별 독립 필터
        self._src = None                  # 소스 모델 참조 캐시 (렌더 핫패스의 sourceModel() 왕복 회피)
        self._accepted = range(0)         # proxy_row -> source_row
        self._src_to_proxy = None         # source_row -> proxy_row (None = 항등 매핑)
        self._identity = True             # 필터 없음(항등 매핑) 여부

    # ---------- 소스 모델 연결 ----------
    def setSourceModel(self, model):
        if self._src is not None:
            try:
                self._src.dataChanged.disconnect(self._on_source_data_changed)
            except TypeError:
                pass
        self.beginResetModel()
        super().setSourceModel(model)
        self._src = model
        if model is not None:
            # QAbstractProxyModel 은 소스 시그널을 자동 전달하지 않으므로 직접 연결
            model.dataChanged.connect(self._on_source_data_changed)
        self._rebuild()
        self.endResetModel()

    def _on_source_data_changed(self, top_left, bottom_right, roles):
        # 소스 셀 변경(하이라이트 등)을 프록시 좌표로 전달. 행은 보이는 전 구간으로
        # 잡아도 뷰는 '보이는 셀'만 다시 그리므로 비용이 거의 없다.
        if not top_left.isValid() or not bottom_right.isValid():
            return
        n = len(self._accepted)
        if n == 0:
            return
        c1, c2 = top_left.column(), bottom_right.column()
        self.dataChanged.emit(self.createIndex(0, c1),
                              self.createIndex(n - 1, c2), roles)

    # ---------- 필터 ----------
    def setFilterForColumn(self, column, hide_texts):
        # 숨길 값이 없으면 해당 컬럼 필터 해제
        if hide_texts:
            self.column_filters[column] = frozenset(hide_texts)
        else:
            self.column_filters.pop(column, None)

        # 통과 행 목록을 한 번에 재계산 후 뷰 갱신 (행별 콜백 없음)
        self.beginResetModel()
        self._rebuild()
        self.endResetModel()
        # 필터 표시(⏷) 갱신 - 해당 열 헤더 리페인트
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, column, column)

    def _rebuild(self):
        """필터를 통과하는 source_row 목록(self._accepted)을 한 번에 계산."""
        if self._src is None:
            self._accepted = range(0)
            self._src_to_proxy = None
            self._identity = True
            return

        rows = self._src.rows
        if not self.column_filters:
            # 필터 없음 → 전 행 항등 매핑 (큰 list/dict 생성 회피)
            self._accepted = range(len(rows))
            self._src_to_proxy = None
            self._identity = True
            return

        active = list(self.column_filters.items())   # [(col, frozenset), ...]
        if len(active) == 1:
            # 단일 필터(가장 흔한 경우) - 리스트 컴프리헨션이 가장 빠름
            col, hidden = active[0]
            accepted = [i for i, row in enumerate(rows) if row[col] not in hidden]
        else:
            accepted = []
            ap = accepted.append
            for i, row in enumerate(rows):
                for col, hidden in active:
                    if row[col] in hidden:
                        break
                else:
                    ap(i)
        self._accepted = accepted
        self._src_to_proxy = {s: p for p, s in enumerate(accepted)}
        self._identity = False

    # ---------- 프록시 ↔ 소스 좌표 매핑 ----------
    def mapToSource(self, proxy_index):
        if not proxy_index.isValid() or self._src is None:
            return QModelIndex()
        r = proxy_index.row()
        if r < 0 or r >= len(self._accepted):
            return QModelIndex()
        return self._src.index(self._accepted[r], proxy_index.column())

    def mapFromSource(self, source_index):
        if not source_index.isValid():
            return QModelIndex()
        sr = source_index.row()
        if self._identity:
            p = sr if 0 <= sr < len(self._accepted) else None
        else:
            p = self._src_to_proxy.get(sr)
        if p is None:
            return QModelIndex()
        return self.createIndex(p, source_index.column())

    # ---------- QAbstractItemModel 필수 구현 ----------
    def index(self, row, column, parent=QModelIndex()):
        if parent.isValid():
            return QModelIndex()
        if 0 <= row < len(self._accepted) and 0 <= column < self.columnCount():
            return self.createIndex(row, column)
        return QModelIndex()

    def parent(self, child=QModelIndex()):
        return QModelIndex()   # 평면 테이블 - 부모 없음

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._accepted)

    def columnCount(self, parent=QModelIndex()):
        return self._src.columnCount() if self._src is not None else 0

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self._src is None:
            return None
        return self._src.data(self.mapToSource(index), role)

    def flags(self, index):
        if not index.isValid() or self._src is None:
            return Qt.ItemFlag.NoItemFlags
        return self._src.flags(self.mapToSource(index))

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if self._src is None:
            return None
        if orientation == Qt.Orientation.Horizontal:
            header = self._src.headerData(section, orientation, role)
            # 필터가 적용된 열은 헤더 텍스트 우측에 '⏷' 표시
            if role == Qt.ItemDataRole.DisplayRole and section in self.column_filters:
                return f"⧩ {header}"
            return header
        # 수직 헤더: 프록시 행 → 소스 행으로 매핑해 원본 행 번호를 유지
        if 0 <= section < len(self._accepted):
            return self._src.headerData(self._accepted[section], orientation, role)
        return None

    # ---------- 캐스케이딩 드롭다운(이 열 제외 후보값) ----------
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
        for row in self._src.rows:
            if self._row_passes(row, exclude=column):
                value = row[column]
                values[value] = value not in hidden
        return values
