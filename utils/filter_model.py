from PyQt6.QtCore import QAbstractProxyModel, QModelIndex, Qt
from PyQt6.QtGui import QFont, QBrush, QColor

from utils.view_state import color_to_str, str_to_color, pack_runs, unpack_runs


class CSVFilterProxyModel(QAbstractProxyModel):
    """열별 값 필터 프록시 (행 부분집합) + Δ(행간 차이) 가상 열.

    ⚠ 성능: 기존 QSortFilterProxyModel은 invalidateRowsFilter() 한 번에
    소스 행 수만큼 파이썬 filterAcceptsRow()를 호출한다. 18만 행 기준
    C++↔Python 경계를 18만 번 넘으며 측정상 **약 3.9초**가 걸렸다(필터 한 번
    적용에). 여기서는 필터 통과 행 목록(self._accepted)을 **벌크 파이썬 루프로
    한 번에** 계산하고 beginResetModel/endResetModel 로 뷰에 알린다 → 경계
    통과가 '행 수만큼'이 아니라 0이 되어 수십 ms 수준으로 떨어진다.

    ⚠ QAbstractProxyModel 은 소스 모델의 시그널을 자동 전달하지 않는다
    (QSortFilterProxyModel 과 다름). 셀 하이라이트(소스 dataChanged) 재페인트를
    위해 setSourceModel 에서 dataChanged 를 직접 연결해 전달한다.

    ⚠ 열 간접화(col_map): 원래 이 프록시는 '프록시 열 == 소스 열'을 가정했다.
    Δ 열을 기준 열 바로 오른쪽에 끼워넣으면서 그 가정을 깼다 → _col_kind /
    _col_src / _src_to_pcol 가 프록시 열 ↔ 소스 열을 잇는다. column_filters 및
    필터/색칠 관련 메서드는 전부 **소스 열** 기준이며, 호출자(FilterHeaderView)가
    클릭한 프록시 열을 source_column_of()로 1회 변환해 넘긴다.

    ⚠ Δ 값은 '스냅샷(고정)'이다: add_delta_column() 시점의 *보이는(필터 통과) 행
    순서*로 1회 계산해 _delta_snap 에 source_row 키로 저장하고, 이후 필터가 바뀌어도
    재계산하지 않는다. 스냅샷 당시 숨겨져 있던 행은 키가 없어 빈칸이 되고, 필터를
    풀어도 그 행은 빈칸으로 남는다(계산됐던 행은 값 유지).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.column_filters = {}          # {src_col: frozenset(hidden_texts)} - 컬럼별 독립 필터 (소스 열 기준)
        self._src = None                  # 소스 모델 참조 캐시 (렌더 핫패스의 sourceModel() 왕복 회피)
        self._accepted = range(0)         # proxy_row -> source_row
        self._src_to_proxy = None         # source_row -> proxy_row (None = 항등 매핑)
        self._identity = True             # 필터 없음(항등 매핑) 여부
        self._fast_header_paint = False   # 가로 헤더 paint 중 True → rowCount 0 위장(헤더 전행 스캔 차단, gui_header 참고)

        # --- Δ(행간 차이) 가상 열 ---
        self._delta_base = set()          # Δ를 가진 소스 열 집합
        self._delta_snap = {}             # {base_col: {source_row: 표시문자열}} - 클릭 시점 1회 고정(스냅샷)
        self.delta_filters = {}           # {base_col: frozenset(hidden_delta_values)} - Δ 열 값 필터(스냅샷 값 기준)
        self._col_kind = []               # proxy_col -> 'src' | 'delta'
        self._col_src = []                # proxy_col -> 소스 열 ('delta'면 그 기준 열)
        self._src_to_pcol = []            # source_col -> 그 'src' 프록시 열
        self._italic_font = QFont()       # Δ 열 셀은 italic 으로 표시 (FontRole)
        self._italic_font.setItalic(True)
        self._cell_font = None            # 줌≠100% 일 때 일반 셀 FontRole 로 줄 폰트(None=뷰 기본 폰트 사용)
        self._delta_color = {}            # {base_col: {source_row: QColor}} - Δ 셀 사용자 색칠(소스 셀이 없어 별도 저장)
        self._delta_prev = {}             # {base_col: {source_row: prev_source_row|None}} - 짝(스냅샷 시점 이전 보이는 행)
        self._delta_snap_filter = {}      # {base_col: {src_col: frozenset}} - 스냅샷 당시 활성 '열 값 필터'(Option2 영속화: 멤버십의 '원인'만 저장)
        self._delta_first_bg = QBrush(QColor(236, 236, 236))   # 'R(n)-R(n-1)' 첫칸 옅은 회색 배경
        self._FIRST_LABEL = "r(n)-r(n-1)"  # Δ 첫 행 안내 문구(스냅샷·배경·표시 한 곳에서 참조)

        # --- 행/열 숨기기(hide) ---
        # 숨김 = 위치(소스 인덱스) 기반 분석 상태. 값 기준 필터와 직교 — 보이는 행 = 필터통과 ∧ 비숨김.
        # 숨겨진 연속 구간마다 가느다란 '마커 섹션' 1개(열=⋯, 행=︙)를 합성해 그 자리에 끼운다.
        #  - 행 마커: _accepted[proxy_row] 에 음수 sentinel(-(run_id+1)) 로 표기, run_id→소스행들은 _row_marker_src.
        #  - 열 마커: _col_kind[proxy_col] == 'hidemark', proxy_col→소스열들은 _col_marker_src.
        self.hidden_rows = set()          # 숨긴 source_row 집합
        self.hidden_cols = set()          # 숨긴 source_col 집합
        # 펼칠 때 원래 크기 복원용 — 열너비·행높이 모두 숨기기 전 크기를 소스키로 보관(엑셀식, 열·행 대칭).
        self.hidden_col_sizes = {}        # {source_col: 숨기기 전 열너비}
        self.hidden_row_sizes = {}        # {source_row: 숨기기 전 행높이}
        self._row_marker_src = {}         # {run_id: [source_row, ...]} - 행 마커가 대표하는 숨김 행들
        self._row_marker_pos = []         # 마커인 proxy_row 목록(_scan_overrides skip 등 — 18만 재스캔 회피)
        self._col_marker_src = {}         # {proxy_col: [source_col, ...]} - 열 마커가 대표하는 숨김 열들
        self._HMARK_ROW = "︙"        # ︙ 행 마커 글리프 (안 보이면 이 상수만 교체)
        self._HMARK_COL = "⋯"        # ⋯ 열 마커 글리프
        self._hidemark_bg = QBrush(QColor(228, 228, 228))   # 마커 셀 본문 회색 배경

    # ---------- 확대/축소: 셀 폰트 크기 동기화 ----------
    def set_cell_font_point_size(self, pt):
        # 줌 단계 폰트 크기(pt)에 맞춰 일반 셀 폰트 + Δ italic 폰트를 다시 만든다.
        # ⚠ 테이블에 스타일시트(background:white)가 걸려 있어 table_csv.setFont() 가 *셀 텍스트*에는
        #   반영되지 않는다(Qt 함정). 그래서 일반 셀도 모델 FontRole 로 폰트를 직접 줘야 글자가 확대/축소된다.
        # pt 가 None/0/음수면(=100% 기본) self._cell_font=None → 뷰 기본 폰트 그대로(네이티브 모습 유지).
        italic = QFont()
        italic.setItalic(True)
        if pt and pt > 0:
            italic.setPointSize(pt)
            cell = QFont()
            cell.setPointSize(pt)
            self._cell_font = cell
        else:
            self._cell_font = None
        self._italic_font = italic

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
        # 새 소스(다른 CSV) → 열/행 의미가 달라지므로 Δ·숨김 초기화
        self._delta_base = set()
        self._delta_snap = {}
        self.delta_filters = {}
        self._delta_color = {}
        self._delta_prev = {}
        self._delta_snap_filter = {}
        self.hidden_rows = set()
        self.hidden_cols = set()
        self.hidden_col_sizes = {}
        self.hidden_row_sizes = {}
        self._row_marker_src = {}
        self._row_marker_pos = []
        self._col_marker_src = {}
        if model is not None:
            # QAbstractProxyModel 은 소스 시그널을 자동 전달하지 않으므로 직접 연결
            model.dataChanged.connect(self._on_source_data_changed)
        self._rebuild()
        self._rebuild_columns()
        self.endResetModel()

    def _on_source_data_changed(self, top_left, bottom_right, roles):
        # 소스 셀 변경(하이라이트 등)을 프록시 좌표로 전달. 행/열 모두 보이는 전 구간으로
        # 잡아도 뷰는 '보이는 셀'만 다시 그리므로 비용이 거의 없다(열 간접화 변환 불필요).
        if not top_left.isValid() or not bottom_right.isValid():
            return
        n = len(self._accepted)
        ncols = len(self._col_kind)
        if n == 0 or ncols == 0:
            return
        self.dataChanged.emit(self.createIndex(0, 0),
                              self.createIndex(n - 1, ncols - 1), roles)

    # ---------- 필터 ----------
    def setFilterForColumn(self, column, hide_texts):
        # column 은 소스 열 기준. 숨길 값이 없으면 해당 컬럼 필터 해제
        if hide_texts:
            self.column_filters[column] = frozenset(hide_texts)
        else:
            self.column_filters.pop(column, None)

        # 통과 행 목록을 한 번에 재계산 후 뷰 갱신 (행별 콜백 없음). 열 매핑은 행과 무관하므로 불변.
        self.beginResetModel()
        self._rebuild()
        self.endResetModel()
        # 필터 표시(⧩) 갱신 - 해당 소스 열의 'src' 프록시 열 헤더 리페인트 (숨긴 열이면 pcol=-1 → 스킵)
        pcol = self._src_to_pcol[column] if 0 <= column < len(self._src_to_pcol) else column
        if pcol >= 0:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, pcol, pcol)

    def _rebuild(self):
        """보이는 source_row 목록(self._accepted)을 한 번에 계산. 보임 = (열/Δ 값 필터 통과) ∧ (숨김 아님).
        숨김이 있으면 숨겨진 연속 구간마다 음수 sentinel 마커를 self._accepted 에 끼워 넣는다."""
        if self._src is None:
            self._accepted = range(0)
            self._src_to_proxy = None
            self._identity = True
            self._row_marker_src = {}
            self._row_marker_pos = []
            return

        rows = self._src.rows
        hidden = self.hidden_rows
        if not self.column_filters and not self.delta_filters and not hidden:
            # 필터·숨김 없음 → 전 행 항등 매핑 (큰 list/dict 생성 회피)
            self._accepted = range(len(rows))
            self._src_to_proxy = None
            self._identity = True
            self._row_marker_src = {}
            self._row_marker_pos = []
            return

        src_active = list(self.column_filters.items())   # [(src_col, frozenset), ...]
        # Δ 값 필터: (스냅샷 dict, 숨길 값 집합) - 행 인덱스로 스냅샷 값을 조회해 판정
        delta_active = [(self._delta_snap.get(b, {}), h) for b, h in self.delta_filters.items()]

        if not hidden and not delta_active and len(src_active) == 1:
            # 단일 열 값 필터(가장 흔한 경우) - 리스트 컴프리헨션이 가장 빠름
            col, hide_vals = src_active[0]
            accepted = [i for i, row in enumerate(rows) if row[col] not in hide_vals]
            self._accepted = accepted
            self._src_to_proxy = {s: p for p, s in enumerate(accepted)}
            self._identity = False
            self._row_marker_src = {}
            self._row_marker_pos = []
            return

        def passes(i, row):
            for col, h in src_active:
                if row[col] in h:
                    return False
            for snap, h in delta_active:
                if snap.get(i, "") in h:
                    return False
            return True

        if not hidden:
            accepted = [i for i, row in enumerate(rows) if passes(i, row)]
            self._accepted = accepted
            self._src_to_proxy = {s: p for p, s in enumerate(accepted)}
            self._identity = False
            self._row_marker_src = {}
            self._row_marker_pos = []
            return

        # --- 숨김 있음: 보이는 행 사이의 'gap(숨긴 행 포함)'마다 마커 1개를 끼운다 ---
        # 필터로만 빠진 행은 마커 run 에 넣지 않되 gap 은 계속 이어가 → 마커는 'gap당 1개'(연속 ⋯/︙ 1개).
        accepted = []
        src_to_proxy = {}
        marker_src = {}
        marker_pos = []
        pending = []        # 직전 보이는 행 이후 숨긴 source_row 누적(= 현재 gap)
        run_id = 0
        ap = accepted.append
        for i, row in enumerate(rows):
            if i in hidden:
                pending.append(i)
                continue
            if not passes(i, row):
                continue
            if pending:
                marker_src[run_id] = pending
                marker_pos.append(len(accepted))
                ap(-(run_id + 1))
                run_id += 1
                pending = []
            src_to_proxy[i] = len(accepted)
            ap(i)
        if pending:         # 마지막 trailing 숨김 run
            marker_src[run_id] = pending
            marker_pos.append(len(accepted))
            ap(-(run_id + 1))
        self._accepted = accepted
        self._src_to_proxy = src_to_proxy
        self._identity = False
        self._row_marker_src = marker_src
        self._row_marker_pos = marker_pos

    # ---------- 열 매핑(col_map) ----------
    def _rebuild_columns(self):
        """_delta_base + 숨김 열 + 소스 열 수로부터 프록시 열 레이아웃을 재구성.
        숨긴 소스 열은 레이아웃에서 빼고(연속 구간마다 'hidemark' 1개), 그 외엔 각 소스 열 뒤에 Δ(있으면)를
        끼운다. 숨긴 소스 열은 그 Δ도 함께 빠진다(Δ는 소스를 따라감). 행과 무관 → 필터 변경 시엔 미호출."""
        self._col_kind = []
        self._col_src = []
        self._col_marker_src = {}
        if self._src is None:
            self._src_to_pcol = []
            return
        S = self._src.columnCount()
        self._src_to_pcol = [-1] * S          # 숨긴 소스 열은 -1(보이는 프록시 열 없음)
        hidden = self.hidden_cols
        pending = []                          # 연속 숨김 소스 열 누적(= 한 마커)
        for sc in range(S):
            if sc in hidden:
                pending.append(sc)
                continue
            if pending:
                self._col_marker_src[len(self._col_kind)] = pending
                self._col_kind.append('hidemark')
                self._col_src.append(-1)
                pending = []
            self._src_to_pcol[sc] = len(self._col_kind)
            self._col_kind.append('src')
            self._col_src.append(sc)
            if sc in self._delta_base:
                self._col_kind.append('delta')
                self._col_src.append(sc)
        if pending:                           # trailing 숨김 열
            self._col_marker_src[len(self._col_kind)] = pending
            self._col_kind.append('hidemark')
            self._col_src.append(-1)

    # ---------- 프록시 ↔ 소스 좌표 매핑 ----------
    def mapToSource(self, proxy_index):
        if not proxy_index.isValid() or self._src is None:
            return QModelIndex()
        r = proxy_index.row()
        pc = proxy_index.column()
        if r < 0 or r >= len(self._accepted):
            return QModelIndex()
        sr = self._accepted[r]
        if sr < 0:
            return QModelIndex()   # 행 마커는 대응하는 소스 셀이 없음
        if pc < 0 or pc >= len(self._col_kind) or self._col_kind[pc] != 'src':
            return QModelIndex()   # Δ 열·열 마커는 대응하는 소스 셀이 없음
        return self._src.index(sr, self._col_src[pc])

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
        sc = source_index.column()
        pc = self._src_to_pcol[sc] if 0 <= sc < len(self._src_to_pcol) else sc
        if pc < 0:                 # 숨겨진 소스 열 → 보이는 프록시 열 없음
            return QModelIndex()
        return self.createIndex(p, pc)

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
        if self._fast_header_paint:
            return 0                       # 가로 헤더 paint 중: Qt 내부 isColumnSelected 의 전행 스캔 차단(gui_header)
        return len(self._accepted)

    def columnCount(self, parent=QModelIndex()):
        return len(self._col_kind)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self._src is None:
            return None
        r = index.row()
        pc = index.column()
        # 마커 셀(행 마커 또는 열 마커): 빈 표시 + 회색 배경(글리프 ⋯/︙ 는 헤더가 그림)
        if (0 <= r < len(self._accepted) and self._accepted[r] < 0) or \
           (0 <= pc < len(self._col_kind) and self._col_kind[pc] == 'hidemark'):
            if role == Qt.ItemDataRole.BackgroundRole:
                return self._hidemark_bg
            if role == Qt.ItemDataRole.DisplayRole:
                return ""
            return None
        if 0 <= pc < len(self._col_kind) and self._col_kind[pc] == 'delta':
            # Δ 열: 스냅샷 표시문자열 + italic 폰트 + 배경(사용자 색칠 우선, 없으면 첫칸 옅은 회색).
            if role == Qt.ItemDataRole.FontRole:
                return self._italic_font
            r = index.row()
            if not (0 <= r < len(self._accepted)):
                return None
            base = self._col_src[pc]
            sr = self._accepted[r]
            if role == Qt.ItemDataRole.BackgroundRole:
                color = self._delta_color.get(base, {}).get(sr)
                if color is not None:
                    return QBrush(color)
                if self._delta_snap.get(base, {}).get(sr) == self._FIRST_LABEL:
                    return self._delta_first_bg
                return None
            if role == Qt.ItemDataRole.ToolTipRole:
                return self._delta_tooltip(base, sr)   # hover 시에만 호출(페인트 무관) → 무비용
            if role != Qt.ItemDataRole.DisplayRole:
                return None
            return self._delta_snap.get(base, {}).get(sr, "")
        # 일반 셀: 줌≠100% 면 FontRole 로 확대/축소된 폰트를 직접 준다(스타일시트로 setFont 가 안 먹는 함정 회피).
        if role == Qt.ItemDataRole.FontRole and self._cell_font is not None:
            return self._cell_font
        return self._src.data(self.mapToSource(index), role)

    def flags(self, index):
        if not index.isValid() or self._src is None:
            return Qt.ItemFlag.NoItemFlags
        r = index.row()
        pc = index.column()
        # 마커 셀은 선택 가능(완전선택 판정·범위검색이 마커 위로도 연속되도록) + 활성(본문 더블클릭 펼침).
        if (0 <= r < len(self._accepted) and self._accepted[r] < 0) or \
           (0 <= pc < len(self._col_kind) and self._col_kind[pc] == 'hidemark'):
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if 0 <= pc < len(self._col_kind) and self._col_kind[pc] == 'delta':
            # Δ 열은 소스 셀이 없어 mapToSource 가 무효 → 선택/복사 위해 기본 플래그를 직접 부여
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return self._src.flags(self.mapToSource(index))

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if self._src is None:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if not (0 <= section < len(self._col_kind)):
                return None
            if self._col_kind[section] == 'hidemark':
                # 열 마커 헤더 = ⋯ (배경/회색 페인트는 FilterHeaderView.paintSection 가 담당)
                if role == Qt.ItemDataRole.DisplayRole:
                    return self._HMARK_COL
                return None
            sc = self._col_src[section]
            if self._col_kind[section] == 'delta':
                # Δ 열 헤더 = Δ [원본헤더] (Δ값 필터가 걸리면 좌측에 '⧩')
                if role == Qt.ItemDataRole.DisplayRole:
                    base = self._src.headerData(sc, orientation, role)
                    label = f"Δ [{base}]"
                    return f"⧩ {label}" if sc in self.delta_filters else label
                return None
            header = self._src.headerData(sc, orientation, role)
            # 필터가 적용된 열은 헤더 텍스트 좌측에 '⧩' 표시
            if role == Qt.ItemDataRole.DisplayRole and sc in self.column_filters:
                return f"⧩ {header}"
            return header
        # 수직 헤더: 프록시 행 → 소스 행으로 매핑해 원본 행 번호를 유지 (행 마커는 ︙)
        if 0 <= section < len(self._accepted):
            sr = self._accepted[section]
            if sr < 0:
                if role == Qt.ItemDataRole.DisplayRole:
                    return self._HMARK_ROW
                return None
            return self._src.headerData(sr, orientation, role)
        return None

    # ---------- Δ(행간 차이) 가상 열 ----------
    def add_delta_column(self, src_col):
        """src_col 바로 오른쪽에 Δ 열을 추가하고 현재 보이는 행 기준으로 스냅샷을 고정.
        beginInsertColumns 한 번이라 행 수와 무관하게 즉시(뷰는 보이는 셀만 다시 그림)."""
        if self._src is None or src_col in self._delta_base:
            return
        if not (0 <= src_col < self._src.columnCount()):
            return
        if self._src_to_pcol[src_col] < 0:       # 숨긴 열엔 Δ 추가 불가(보이는 위치가 없음)
            return
        pos = self._src_to_pcol[src_col] + 1     # 기준 'src' 열 바로 오른쪽
        self._snapshot(src_col)                  # 현재 보이는 행 기준으로 1회 고정
        self._delta_snap_filter[src_col] = dict(self.column_filters)   # Option2 영속화: 스냅샷 당시 열 값 필터 기록
        self.beginInsertColumns(QModelIndex(), pos, pos)
        self._delta_base.add(src_col)
        self._rebuild_columns()
        self.endInsertColumns()

    def remove_delta_column(self, src_col):
        if src_col not in self._delta_base:
            return
        if src_col in self.delta_filters or self._src_to_pcol[src_col] < 0:
            # Δ값 필터가 걸려 있었으면(행 집합 변동) 또는 기준 열이 숨겨져 Δ 위치가 없으면 → 전체 리셋이 단순·안전
            self.beginResetModel()
            self._delta_base.discard(src_col)
            self._delta_snap.pop(src_col, None)
            self._delta_color.pop(src_col, None)
            self._delta_prev.pop(src_col, None)
            self._delta_snap_filter.pop(src_col, None)
            self.delta_filters.pop(src_col, None)
            self._rebuild_columns()
            self._rebuild()
            self.endResetModel()
            return
        pos = self._src_to_pcol[src_col] + 1     # 그 기준 열 다음의 Δ 열
        self.beginRemoveColumns(QModelIndex(), pos, pos)
        self._delta_base.discard(src_col)
        self._delta_snap.pop(src_col, None)
        self._delta_color.pop(src_col, None)
        self._delta_prev.pop(src_col, None)
        self._delta_snap_filter.pop(src_col, None)
        self._rebuild_columns()
        self.endRemoveColumns()

    def has_delta(self, src_col):
        return src_col in self._delta_base

    def setDeltaFilterForColumn(self, base, hide_values):
        # base(소스 열)의 Δ 열 값 필터. 숨길 값이 없으면 해제. (스냅샷 값 기준 → _rebuild 가 _delta_snap 조회)
        if hide_values:
            self.delta_filters[base] = frozenset(hide_values)
        else:
            self.delta_filters.pop(base, None)
        self.beginResetModel()
        self._rebuild()
        self.endResetModel()
        # ⧩ 표시 갱신 - Δ 열 헤더(기준 'src' 열 바로 오른쪽; 기준 열이 숨겨졌으면 보일 게 없어 스킵)
        if 0 <= base < len(self._src_to_pcol) and base in self._delta_base and self._src_to_pcol[base] >= 0:
            pcol = self._src_to_pcol[base] + 1
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, pcol, pcol)

    def has_delta_filter(self, base):
        return base in self.delta_filters

    def is_delta_column(self, proxy_col):
        return 0 <= proxy_col < len(self._col_kind) and self._col_kind[proxy_col] == 'delta'

    def source_column_of(self, proxy_col):
        # 프록시 열 → 소스 열 (Δ 열이면 그 기준 열). 헤더뷰가 클릭 열을 소스 열로 변환할 때 사용.
        if 0 <= proxy_col < len(self._col_src):
            return self._col_src[proxy_col]
        return proxy_col

    def source_columns(self):
        # 프록시 열 → 소스 열 리스트 (Δ 열은 -1). 검색이 소스 행을 직접 읽을 때 변환/스킵용.
        return [sc if k == 'src' else -1 for k, sc in zip(self._col_kind, self._col_src)]

    def accepted_rows(self):
        # proxy_row → source_row 시퀀스(range 또는 list). 복사 등 대량 행 매핑을 벌크로 할 때
        # (행마다 mapToSource 호출하면 18만 번 경계를 넘어 느림 → 이 리스트를 직접 인덱싱).
        return self._accepted

    def delta_snapshot(self, base_col):
        # base_col Δ열의 {source_row: 표시문자열} 스냅샷(복사 시 직접 조회용). 없으면 빈 dict.
        return self._delta_snap.get(base_col, {})

    def column_label(self, proxy_col):
        # 복사용 헤더: 'src'는 ⧩ 없는 원본 헤더, 'delta'는 Δ[원본헤더].
        if self._src is None or not (0 <= proxy_col < len(self._col_kind)):
            return ""
        sc = self._col_src[proxy_col]
        base = self._src.headerData(sc, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        base = "" if base is None else str(base)
        if self._col_kind[proxy_col] == 'delta':
            return f"Δ [{base}]"
        return base

    def graph_dataset(self):
        """3D 그래프용 (headers, rows). **값 필터(열·Δ)는 적용**하되 **행/열 '숨기기'는 무시**한다.
        - 열 = 모든 소스 열(숨긴 열 포함) + 각 Δ 열(테이블과 동일 배치, 마커 열 제외)
        - 행 = 값 필터(column_filters + delta_filters)를 통과한 소스 행(숨김 무시 → 숨긴 행도 포함)
        - Δ 값 = 스냅샷. 단 **첫 행 안내문구(r(n)-r(n-1))는 빈값으로 예외처리**(숫자 판정에서 무시)
          → 나머지가 모두 숫자면 Δ 열도 x/y/z 축 선택 가능. 문자비교 =/≠ 등 다른 비숫자 값은 그대로
          둬, 그런 값이 섞인 Δ 열은 일반 열과 동일하게 자동으로 숫자 축 후보에서 빠진다.
        숨김 좌표(col_map)를 거치지 않고 소스에서 직접 구성하므로 숨김과 무관하다."""
        if self._src is None:
            return [], []
        src_rows = self._src.rows
        headers_src = self._src.headers
        ncols = len(headers_src)

        # 값 필터(열 + Δ) 통과 행 — 숨김은 무시. _rebuild 의 passes() 와 동일 판정(숨김 분기만 제거).
        src_active = list(self.column_filters.items())
        delta_active = [(self._delta_snap.get(b, {}), h) for b, h in self.delta_filters.items()]
        if not src_active and not delta_active:
            rows_idx = range(len(src_rows))
        else:
            def passes(i, row):
                for col, h in src_active:
                    if row[col] in h:
                        return False
                for snap, h in delta_active:
                    if snap.get(i, "") in h:
                        return False
                return True
            rows_idx = [i for i, row in enumerate(src_rows) if passes(i, row)]

        # 열 구성: 각 소스 열 + (있으면) 그 Δ 열
        headers = []
        specs = []        # ('src', col) | ('delta', base_col)
        for c in range(ncols):
            headers.append(str(headers_src[c]))
            specs.append(('src', c))
            if c in self._delta_base:
                headers.append(f"Δ [{headers_src[c]}]")
                specs.append(('delta', c))

        out = []
        for i in rows_idx:
            srow = src_rows[i]
            line = []
            for kind, c in specs:
                if kind == 'src':
                    line.append(srow[c] if c < len(srow) else "")
                else:
                    v = self._delta_snap.get(c, {}).get(i, "")
                    # Δ 첫 행 안내문구(r(n)-r(n-1))는 예외 — 빈값으로 둬 숫자 판정에서 무시
                    # (나머지가 숫자면 Δ 축 선택 가능). =/≠ 등 다른 비숫자는 그대로 → 축 불가 유지.
                    line.append("" if v == self._FIRST_LABEL else v)
            out.append(line)
        return headers, out

    # ---------- 행/열 숨기기 ----------
    # 숨김 좌표는 소스 인덱스 기준(정렬·필터와 무관하게 안정). 구조 변경이라 beginResetModel 로 알린다
    # (행/열 다수 → 마커 1개로 접히는 비단순 변경이라 insert/remove 보다 reset 이 단순·안전. 뷰는 보이는 셀만 다시 그림).
    def hide_columns(self, src_cols, sizes=None):
        """src_cols(소스 열들)를 숨긴다. sizes={src_col: 숨기기 전 너비} 면 펼칠 때 원복용으로 보관."""
        if self._src is None:
            return
        add = {c for c in src_cols if 0 <= c < self._src.columnCount()} - self.hidden_cols
        if not add:
            return
        self.beginResetModel()
        self.hidden_cols |= add
        if sizes:
            self.hidden_col_sizes.update({c: sizes[c] for c in add if c in sizes})
        self._rebuild_columns()
        self.endResetModel()

    def unhide_columns(self, src_cols):
        """src_cols 중 숨김 상태인 것을 펼친다. 반환: {source_col: 보관해 둔 원래 너비}(GUI 가 복원)."""
        rem = self.hidden_cols & {c for c in src_cols}
        if not rem:
            return {}
        sizes = {c: self.hidden_col_sizes.pop(c) for c in rem if c in self.hidden_col_sizes}
        self.beginResetModel()
        self.hidden_cols -= rem
        self._rebuild_columns()
        self.endResetModel()
        return sizes

    def hide_rows(self, src_rows, sizes=None):
        """src_rows(소스 행들)를 숨긴다. sizes={src_row: 숨기기 전 높이} 면 펼칠 때 원복용으로 보관(열과 대칭)."""
        if self._src is None:
            return
        add = {r for r in src_rows if 0 <= r < len(self._src.rows)} - self.hidden_rows
        if not add:
            return
        self.beginResetModel()
        self.hidden_rows |= add
        if sizes:
            self.hidden_row_sizes.update({r: sizes[r] for r in add if r in sizes})
        self._rebuild()
        self.endResetModel()

    def unhide_rows(self, src_rows):
        """src_rows 중 숨김 상태인 것을 펼친다. 반환: {source_row: 보관해 둔 원래 높이}(GUI 가 복원)."""
        rem = self.hidden_rows & {r for r in src_rows}
        if not rem:
            return {}
        sizes = {r: self.hidden_row_sizes.pop(r) for r in rem if r in self.hidden_row_sizes}
        self.beginResetModel()
        self.hidden_rows -= rem
        self._rebuild()
        self.endResetModel()
        return sizes

    def has_hidden(self):
        return bool(self.hidden_rows or self.hidden_cols)

    def is_hidemark_column(self, proxy_col):
        return 0 <= proxy_col < len(self._col_kind) and self._col_kind[proxy_col] == 'hidemark'

    def is_hidemark_row(self, proxy_row):
        return 0 <= proxy_row < len(self._accepted) and self._accepted[proxy_row] < 0

    def hidemark_column_run(self, proxy_col):
        # 그 열 마커가 대표하는 숨김 소스 열들(더블클릭 펼침용). 마커가 아니면 빈 리스트.
        return list(self._col_marker_src.get(proxy_col, []))

    def hidemark_row_run(self, proxy_row):
        # 그 행 마커가 대표하는 숨김 소스 행들. 마커가 아니면 빈 리스트.
        if 0 <= proxy_row < len(self._accepted):
            sr = self._accepted[proxy_row]
            if sr < 0:
                return list(self._row_marker_src.get(-sr - 1, []))
        return []

    def marker_col_positions(self):
        # 열 마커인 proxy_col 목록 (열너비 저장 시 스캔 제외용).
        return list(self._col_marker_src.keys())

    def marker_row_positions(self):
        # 행 마커인 proxy_row 목록 (행높이 저장 시 스캔 제외용 — 18만 행 재스캔 회피).
        return list(self._row_marker_pos)

    def proxy_column_of(self, src_col):
        # 소스 열 → 보이는 프록시 'src' 열 (숨김이면 -1). 펼친 뒤 원래 너비를 그 위치에 복원할 때 사용.
        return self._src_to_pcol[src_col] if 0 <= src_col < len(self._src_to_pcol) else -1

    def proxy_row_of(self, src_row):
        # 소스 행 → 보이는 프록시 행 (숨김/필터로 안 보이면 -1).
        if self._identity:
            return src_row if 0 <= src_row < len(self._accepted) else -1
        return self._src_to_proxy.get(src_row, -1)

    def source_row_of(self, proxy_row):
        # 보이는 프록시 행 → 소스 행 (행 마커/범위 밖이면 -1).
        if 0 <= proxy_row < len(self._accepted):
            sr = self._accepted[proxy_row]
            return sr if sr >= 0 else -1
        return -1

    def _snapshot(self, base):
        """현재 보이는 행 기준으로 base 의 Δ 스냅샷을 고정 (행 마커 sentinel 은 제외하고 실제 소스 행만)."""
        self._compute_snapshot(base, [sr for sr in self._accepted if sr >= 0])

    def _compute_snapshot(self, base, accepted):
        """accepted(보이는 source_row, 오름차순) 순서로 base 열의 Δ 값+짝을 1회 계산해 고정.
        첫 보이는 행=설명문구, 이후=직전 보이는 행과의 차. 짝(이전 소스행)은 _delta_prev 에 저장
        (Δ 셀 선택 시 비교한 두 부모셀 테두리/툴팁용). 숨겨진 행은 미저장(=빈칸).
        ⚠ add_delta_column(현재 필터)과 restore_state(스냅샷 당시 필터 재구성)가 공유하는 단일 로직."""
        rows = self._src.rows
        snap = {}
        partner = {}
        prev = None
        prev_sr = None
        first = True
        for sr in accepted:
            cur = rows[sr][base]
            if first:
                snap[sr] = self._FIRST_LABEL
                first = False
            else:
                snap[sr] = self._format_delta(prev, cur)
            partner[sr] = prev_sr        # 첫 행은 None(비교 대상 없음)
            prev = cur
            prev_sr = sr
        self._delta_snap[base] = snap
        self._delta_prev[base] = partner

    @staticmethod
    def _format_delta(prev, cur):
        """Δ 표시 포맷 (유일한 포맷 지점). 숫자면 차이(정수면 정수, 아니면 부동소수 노이즈 없이),
        숫자가 아니면(문자 비교) 같으면 '=', 다르면 '≠'. (여기만 바꾸면 표시·툴팁 둘 다 따라옴)"""
        try:
            d = float(cur) - float(prev)
            if d == int(d):
                return str(int(d))
            return f"{d:g}"
        except (ValueError, TypeError, OverflowError):
            return "=" if cur == prev else "≠"

    # ---------- 캐스케이딩 드롭다운(이 열 제외 후보값) ----------
    def _row_passes(self, i, exclude_src=None, exclude_delta=None):
        # 열 값 필터 + Δ 값 필터를 AND로 판정. 캐스케이딩 드롭다운용으로 자기 자신(exclude)은 건너뛴다.
        row = self._src.rows[i]
        for col, hidden in self.column_filters.items():
            if col == exclude_src:
                continue
            if row[col] in hidden:
                return False
        for base, hidden in self.delta_filters.items():
            if base == exclude_delta:
                continue
            if self._delta_snap.get(base, {}).get(i, "") in hidden:
                return False
        return True

    def column_values_excluding_self(self, column):
        """이 column(소스 열)을 제외한 나머지 필터를 통과한 행에서 column의 고유값 수집.
        반환: {value: checked}  (checked = 이 열에서 숨기지 않은 허용값)"""
        hidden = self.column_filters.get(column, frozenset())
        values = {}
        for i, row in enumerate(self._src.rows):
            if self._row_passes(i, exclude_src=column):
                value = row[column]
                values[value] = value not in hidden
        return values

    def delta_values_excluding_self(self, base):
        """Δ 열(기준=base)의 고유 Δ값 수집 (이 Δ 필터만 제외한 나머지 필터를 통과한 행 기준).
        반환: {delta_value: checked}. 값은 스냅샷 표시문자열(스냅샷 때 숨겨졌던 행은 빈칸 "")."""
        snap = self._delta_snap.get(base, {})
        hidden = self.delta_filters.get(base, frozenset())
        values = {}
        for i in range(len(self._src.rows)):
            if self._row_passes(i, exclude_delta=base):
                v = snap.get(i, "")
                if v == self._FIRST_LABEL:   # 첫(보이는) 행 안내문구 r(n)-r(n-1) 은 필터 후보에서 제외 → 2번째 행부터
                    continue
                values[v] = v not in hidden
        return values

    # ---------- 값별 행 색칠(필터창 색버튼) ----------
    def source_rows_with_value(self, column, value):
        """column(소스 열) 값이 value와 같은 모든 소스 행 인덱스 목록 (값별 색칠용).
        ⚠ 필터를 무시하고 전체 소스 행을 스캔한다(다른 열 필터로 가려진 행도 칠해
          필터 해제 시 색이 일관되게 유지되도록). 색 선택 시점에만 도는 1회 O(N) 스캔."""
        if self._src is None:
            return []
        return [i for i, row in enumerate(self._src.rows) if row[column] == value]

    def source_rows_with_delta_value(self, base, value):
        """Δ값(스냅샷)이 value와 같은 모든 소스 행 인덱스 목록 (Δ 열 값별 색칠용).
        스냅샷에 없는 행(=숨겨졌던 빈칸)은 대상 없음."""
        snap = self._delta_snap.get(base, {})
        return [i for i, v in snap.items() if v == value]

    # ---------- Δ 셀 색칠 (소스 셀이 없어 프록시에 (기준열, 소스행)으로 별도 저장) ----------
    def set_delta_cell_colors(self, color, targets):
        """targets=[(base_col, source_row)] 의 Δ 셀을 color 로 칠한다(None=해제). 선택 색칠용."""
        bases = set()
        for base, sr in targets:
            if base not in self._delta_base:
                continue
            cmap = self._delta_color.setdefault(base, {})
            if color is None:
                cmap.pop(sr, None)
            else:
                cmap[sr] = color
            bases.add(base)
        self._emit_delta_bg(bases)

    def color_delta_rows(self, color, source_rows):
        """주어진 소스 행들의 '모든 Δ 열' 셀을 color 로 칠한다(None=해제). 값별 행 색칠용."""
        bases = set()
        for base in self._delta_base:
            cmap = self._delta_color.setdefault(base, {})
            for sr in source_rows:
                if color is None:
                    cmap.pop(sr, None)
                else:
                    cmap[sr] = color
            bases.add(base)
        self._emit_delta_bg(bases)

    def has_delta_colors(self):
        """Δ 셀 사용자 색칠이 하나라도 있는지 (전체 해제 no-op 판정용)."""
        return bool(self._delta_color)

    def clear_all_delta_colors(self):
        """모든 Δ 셀 색칠 해제 (소스 모델의 '전체 해제'와 짝)."""
        if not self._delta_color:
            return
        bases = set(self._delta_color)
        self._delta_color = {}
        self._emit_delta_bg(bases)

    def _emit_delta_bg(self, bases):
        # 변경된 Δ 열들의 전 행 구간을 1회 dataChanged (뷰는 보이는 셀만 다시 그림 → 행 수 무관)
        n = len(self._accepted)
        if n == 0:
            return
        for base in bases:
            if base in self._delta_base and 0 <= base < len(self._src_to_pcol) and self._src_to_pcol[base] >= 0:
                pcol = self._src_to_pcol[base] + 1
                self.dataChanged.emit(self.createIndex(0, pcol),
                                      self.createIndex(n - 1, pcol),
                                      [Qt.ItemDataRole.BackgroundRole])

    # ---------- Δ 비교(짝) 조회: 선택 시 부모셀 테두리 + hover 툴팁 ----------
    def delta_compare_cells(self, proxy_col, proxy_row):
        """Δ 셀(proxy_col, proxy_row)이 비교한 두 부모셀의 프록시 좌표를 반환.
        반환: (base_pcol, cur_prow, prev_prow|None) 또는 None(Δ셀 아님/첫 행).
        prev_prow=None → 이전 행이 필터로 숨겨짐(빨강 테두리 표시 불가)."""
        if not self.is_delta_column(proxy_col) or not (0 <= proxy_row < len(self._accepted)):
            return None
        base = self._col_src[proxy_col]
        sr = self._accepted[proxy_row]
        prev_sr = self._delta_prev.get(base, {}).get(sr)
        if prev_sr is None:                       # 첫 행 = 비교 대상 없음
            return None
        base_pcol = self._src_to_pcol[base]
        if self._src_to_proxy is None:            # 필터 없음(항등) → proxy_row == source_row
            prev_prow = prev_sr if 0 <= prev_sr < len(self._accepted) else None
        else:
            prev_prow = self._src_to_proxy.get(prev_sr)   # 숨겨졌으면 None
        return (base_pcol, proxy_row, prev_prow)

    def _delta_tooltip(self, base, sr):
        """Δ 셀 hover 툴팁: 비교한 두 부모셀의 행번호 + 값 + 관계(숫자는 차이식, 문자는 =/≠)."""
        prev_sr = self._delta_prev.get(base, {}).get(sr)
        if prev_sr is None:                       # 첫 행
            return None
        rows = self._src.rows
        cur, prev = rows[sr][base], rows[prev_sr][base]
        hidden = self._src_to_proxy is not None and prev_sr not in self._src_to_proxy
        note = f" (hidden)" if hidden else ""
        try:                                       # 숫자면 'cur − prev = 차이', 문자면 'cur =/≠ prev'
            float(cur); float(prev)
            prev_text = f"{prev}" if float(prev) >=0 else f"({prev})"
            body = f"{cur} − {prev_text} = {self._format_delta(prev, cur)}"
        except (ValueError, TypeError):
            body = f"{cur} {self._format_delta(prev, cur)} {prev}"
        return f"row #{sr + 1} ↔ #{prev_sr + 1}{note}\n{body}"

    # ---------- 영속화(.viewer): 사용자 입력만 추출/복원 (파생은 재계산) ----------
    def export_state(self):
        """저장용 상태(열 값 필터 + Δ 정의). 행 리스트 없이 '필터(원인)'만 → 18만 행도 수십 byte.
        highlights/col_widths/scroll 은 뷰 소유라 GUI 가 따로 담는다.
        반환: {'column_filters': [...], 'deltas': [...], 'hidden_*': ...} (JSON 직렬화 가능 평면 구조)."""
        return {
            "column_filters": [
                {"col": col, "hidden": sorted(hidden)}
                for col, hidden in self.column_filters.items()
            ],
            "hidden_rows": pack_runs(self.hidden_rows),     # 숨김(위치 기반) — 연속구간 run 인코딩
            "hidden_cols": pack_runs(self.hidden_cols),
            "hidden_col_sizes": {str(c): w for c, w in self.hidden_col_sizes.items()},  # 숨기기 전 너비/높이(열·행 대칭)
            "hidden_row_sizes": {str(r): h for r, h in self.hidden_row_sizes.items()},
            "deltas": [
                {
                    "base": base,
                    # Option2: 스냅샷 당시 활성 '열 값 필터'(보통 빈 리스트). 멤버십의 '원인'만 저장 →
                    # 재현 시 동일 파일에 다시 적용해 그때 보이던 행을 그대로 복원("필터 걸고 Δ" 까지 정확).
                    "snapshot_filter": [
                        {"col": c, "hidden": sorted(h)}
                        for c, h in self._delta_snap_filter.get(base, {}).items()
                    ],
                    "filter_hidden": sorted(self.delta_filters[base]) if base in self.delta_filters else [],
                    "colors": self._export_delta_colors(base),
                }
                for base in sorted(self._delta_base)
            ],
        }

    def _export_delta_colors(self, base):
        # {source_row: QColor} → {색문자열: [source_row, ...]} (색으로 그룹화)
        grouped = {}
        for sr, color in self._delta_color.get(base, {}).items():
            grouped.setdefault(color_to_str(color), []).append(sr)
        return grouped

    def restore_state(self, state):
        """export_state 역. 한 번의 reset 으로 열 값 필터 + Δ(Option2 재현)를 일괄 복원한다.
        ⚠ Δ 멤버십은 저장된 snapshot_filter 를 동일 파일에 다시 적용해 재구성(행 리스트 미저장).
           스냅샷 당시 *다른 Δ의 값 필터*까지 걸려 있던 극히 드문 경우는 열 값 필터만 반영된다(허용)."""
        if self._src is None:
            return
        ncols = self._src.columnCount()
        self.beginResetModel()
        # 1) 열 값 필터
        self.column_filters = {
            f["col"]: frozenset(f["hidden"])
            for f in state.get("column_filters", [])
            if f.get("hidden") and isinstance(f.get("col"), int) and 0 <= f["col"] < ncols
        }
        # 2) Δ: snapshot_filter 로 보이던 행 재구성 → 스냅샷 재생 → Δ값 필터/색 복원
        self._delta_base = set()
        self._delta_snap = {}
        self._delta_prev = {}
        self.delta_filters = {}
        self._delta_color = {}
        self._delta_snap_filter = {}
        for d in state.get("deltas", []):
            base = d.get("base")
            if not isinstance(base, int) or not (0 <= base < ncols):
                continue
            snap_filter = {
                f["col"]: frozenset(f["hidden"])
                for f in d.get("snapshot_filter", [])
                if f.get("hidden") and isinstance(f.get("col"), int) and 0 <= f["col"] < ncols
            }
            self._delta_base.add(base)
            self._delta_snap_filter[base] = snap_filter
            self._compute_snapshot(base, self._accepted_for_filter(snap_filter))
            if d.get("filter_hidden"):
                self.delta_filters[base] = frozenset(d["filter_hidden"])
            cmap = {}
            for hexstr, srows in d.get("colors", {}).items():
                color = str_to_color(hexstr)
                for sr in srows:
                    cmap[sr] = color
            if cmap:
                self._delta_color[base] = cmap
        # 3) 숨김(위치 기반) — 소스 좌표라 안정. 범위 밖은 방어적으로 무시.
        nrows = len(self._src.rows)
        self.hidden_cols = {c for c in unpack_runs(state.get("hidden_cols", [])) if 0 <= c < ncols}
        self.hidden_rows = {r for r in unpack_runs(state.get("hidden_rows", [])) if 0 <= r < nrows}
        self.hidden_col_sizes = {int(c): w for c, w in (state.get("hidden_col_sizes") or {}).items()
                                 if str(c).lstrip("-").isdigit() and 0 <= int(c) < ncols}
        self.hidden_row_sizes = {int(r): h for r, h in (state.get("hidden_row_sizes") or {}).items()
                                 if str(r).lstrip("-").isdigit() and 0 <= int(r) < nrows}
        # 4) 열 레이아웃 + 통과 행 재계산 (숨김·필터·Δ 모두 반영)
        self._rebuild_columns()
        self._rebuild()
        self.endResetModel()

    def _accepted_for_filter(self, snap_filter):
        """주어진 열 값 필터를 통과하는 source_row 목록(오름차순). 이 앱은 정렬이 없어 보이는 행=오름차순이라
        순서 저장이 불필요하고 멤버십만 재구성하면 된다. snap_filter 가 비면 전 행(range)."""
        rows = self._src.rows
        if not snap_filter:
            return range(len(rows))
        items = list(snap_filter.items())
        return [i for i, row in enumerate(rows)
                if all(row[col] not in hidden for col, hidden in items)]
