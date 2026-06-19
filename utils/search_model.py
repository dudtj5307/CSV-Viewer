from PyQt6.QtWidgets import QTableView
from PyQt6.QtCore import pyqtSignal, Qt, QObject


class SearchModel(QObject):
    search_widget_update = pyqtSignal(int, int)

    def __init__(self, table_view: QTableView):
        super().__init__()

        self.table_view = table_view
        self.model = None

        self.matches = []           # (row, col) 튜플 리스트 (row == -1 은 헤더)
        self.current_index = -1     # 현재 선택된 결과 인덱스

        self.is_running = False

        # --- 선택 영역 검색(scoped search) ---
        # 검색바를 열 때(capture_scope) 열/행 '전체 선택'을 1회 캡처해 검색바가 닫힐 때까지 유지.
        # None = 제약 없음(전체). 셀 클릭·셀범위 드래그는 selectedColumns/Rows가 비어 전체검색이 된다.
        self.scope_rows = None      # set[proxy_row] | None
        self.scope_cols = None      # set[proxy_col] | None

    @property
    def scope_active(self):
        return self.scope_cols is not None or self.scope_rows is not None

    def reset_scope(self):
        # 검색바를 닫을 때 호출 -> 다음에 열 때 현재 선택을 다시 캡처
        self.scope_rows = None
        self.scope_cols = None

    def capture_scope(self):
        # 검색바를 열 때(Ctrl+F) 호출. 열/행 '전체 선택'만 범위로 인정한다.
        # ⚠ selectedColumns()/selectedRows()는 열 전체 선택 시 18만 행을 내부 순회해 수 초가
        #   걸린다(측정값: 열 선택 시 합 ~3.6s). 그래서 선택 '범위'만 보고 직접 판정한다.
        #   범위 수는 항상 소수라 즉시 끝나고, 혼합 선택으로 범위가 쪼개져도 구간 커버리지로 정확히 잡는다.
        sel = self.table_view.selectionModel()
        model = self.table_view.model()
        if sel is None or model is None:
            self.scope_cols = None
            self.scope_rows = None
            return

        row_count, col_count = model.rowCount(), model.columnCount()
        ranges = [(r.top(), r.bottom(), r.left(), r.right()) for r in sel.selection()]

        # 열 전체 선택: 그 열을 덮는 (쪼개졌을 수 있는) 행 구간들이 [0, row_count-1]를 모두 덮으면 그 열
        cols = set()
        for c in range(col_count):
            row_spans = [(top, bot) for (top, bot, left, right) in ranges if left <= c <= right]
            if row_spans and self._spans_cover(row_spans, row_count):
                cols.add(c)

        # 행 전체 선택: 행 경계로 밴드를 나눠, 밴드를 덮는 range들의 열 구간이 모든 열을 덮으면 그 밴드의 행들
        rows = set()
        bounds = sorted({b for (top, bot, _, _) in ranges for b in (top, bot + 1) if 0 <= b <= row_count})
        for i in range(len(bounds) - 1):
            a, b = bounds[i], bounds[i + 1]
            col_spans = [(left, right) for (top, bot, left, right) in ranges if top <= a and bot >= b - 1]
            if col_spans and self._spans_cover(col_spans, col_count):
                rows.update(range(a, b))

        self.scope_cols = cols or None
        self.scope_rows = rows or None

    @staticmethod
    def _spans_cover(spans, total):
        # spans: [(lo, hi)] (양끝 포함) 구간들이 [0, total-1] 전체를 빈틈없이 덮는가
        if total <= 0:
            return False
        nxt = 0
        for lo, hi in sorted(spans):
            if lo > nxt:            # 빈틈 발생
                return False
            if hi >= nxt:
                nxt = hi + 1
            if nxt >= total:
                return True
        return nxt >= total

    def search(self, search_text: str):
        # Return if already searching
        if self.is_running:
            print("Already Searching")
            return
        # Connect to model
        self.model = self.table_view.model()

        self.matches.clear()
        self.current_index = -1
        if not search_text or not self.model:
            self.send_result_to_gui()
            return

        self.is_running = True

        needle = search_text.lower()
        proxy = self.model
        source_model = proxy.sourceModel() if hasattr(proxy, "sourceModel") else proxy
        headers = getattr(source_model, "headers", [])
        rows = getattr(source_model, "rows", None)
        col_count = proxy.columnCount()
        # 프록시 열 → 소스 열 (Δ 열은 -1). 아래에서 소스 행을 직접 읽으므로 열 변환이 필수
        # (Δ 가상 열이 끼면 프록시 열 != 소스 열 → 변환 없이는 IndexError/열 어긋남).
        src_cols = proxy.source_columns() if hasattr(proxy, "source_columns") else None

        scope_cols, scope_rows = self.scope_cols, self.scope_rows
        scope_active = scope_cols is not None or scope_rows is not None

        # Find in Column header (row == -1) - 전체검색일 때만 (범위 지정 시 헤더 제외)
        if not scope_active:
            for col in range(col_count):
                sc = src_cols[col] if src_cols is not None else col
                if sc < 0:                  # Δ 열 헤더는 검색 제외(v1)
                    continue
                header_text = headers[sc] if sc < len(headers) else ""
                if header_text and needle in str(header_text).lower():
                    self.matches.append((-1, col))

        # Find in data cells - 보이는(프록시) 행만 순회하되 source 데이터를 직접 읽음
        # 범위(scope): 전체(None) / 선택 열 / 선택 행 / 둘 다(합집합: 선택 열 OR 선택 행)
        if rows is not None and col_count:
            map_to_source = proxy.mapToSource
            proxy_index = proxy.index
            for proxy_row in range(proxy.rowCount()):
                # 행만 지정된 범위면 범위 밖 행은 통째로 건너뜀 (열 범위가 함께면 합집합이라 못 건너뜀)
                if scope_cols is None and scope_rows is not None and proxy_row not in scope_rows:
                    continue
                source_row = map_to_source(proxy_index(proxy_row, 0)).row()
                row_data = rows[source_row]
                row_selected = scope_rows is not None and proxy_row in scope_rows
                for col in range(col_count):
                    if scope_active and not (row_selected or (scope_cols is not None and col in scope_cols)):
                        continue
                    sc = src_cols[col] if src_cols is not None else col
                    if sc < 0:              # Δ 열은 소스 데이터가 없음 → 검색 제외(v1)
                        continue
                    if needle in row_data[sc].lower():
                        self.matches.append((proxy_row, col))

        # 전체 탐색 후, 결과가 있으면 첫번째 결과 선택
        if self.matches:
            self.current_index = 0
            self.select_current()

        self.send_result_to_gui()
        self.is_running = False

    def next_match(self):
        if not self.matches:
            return
        self.current_index = (self.current_index + 1) % len(self.matches)
        self.select_current()
        self.send_result_to_gui()

    def previous_match(self):
        if not self.matches:
            return
        self.current_index = (self.current_index - 1 + len(self.matches)) % len(self.matches)
        self.select_current()
        self.send_result_to_gui()

    def select_current(self):
        if self.current_index >= 0 and self.matches:
            row, col = self.matches[self.current_index]
            self.table_view.clearSelection()
            # If in Column Header
            if row == -1:
                self.table_view.scrollTo(self.model.index(0, col))
                self.table_view.selectColumn(col)
            # If in data
            else:
                idx = self.model.index(row, col)
                self.table_view.scrollTo(idx)
                self.table_view.setCurrentIndex(idx)
                self.table_view.setFocus()


    def send_result_to_gui(self):
        # 현재 선택된 검색 결과 번호와 전체 결과 개수를 전달합니다.
        self.search_widget_update.emit(self.current_index + 1, len(self.matches))
