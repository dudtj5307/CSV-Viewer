import os

from PyQt6.QtWidgets import QHeaderView, QStyleOptionHeader, QStyle
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QFont, QColor

from GUI.gui_filter import FilterWidget


class FilterHeaderView(QHeaderView):
    """테이블(table_csv)의 커스텀 수평 헤더(QHeaderView).

    역할: 열 헤더 우클릭 → 열 필터 팝업(FilterWidget) 표시, 필터 적용/해제,
    값별 행 색칠, Δ(행간 차이) 열 추가/삭제. 우클릭으로 띄우는 팝업 UI 자체는
    gui_filter.FilterWidget 이고, 이 클래스는 그 팝업을 띄우는 '헤더 위젯'이다.
    (둘은 서로 다른 개념이라 파일을 분리했다 — 헤더=gui_header, 팝업=gui_filter.)
    """
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.parent = parent
        self.table_view = parent.table_csv
        self.setSectionsClickable(True)
        # ⚠ 선택된 셀/열의 헤더에 State_On(= paintSection 의 Bold 트리거)을 부여하려면 필요.
        #   QTableView 가 자동 생성하는 헤더는 True 지만, setHorizontalHeader 로 교체한 커스텀
        #   헤더는 bare QHeaderView 기본값 False 라 직접 켜야 한다(안 켜면 선택해도 State_On 안 뜸 →
        #   세로 헤더는 굵어지는데 가로만 안 굵어지던 원인). 켜도 선택 추적은 구간 기반이라 행 수 무관.
        self.setHighlightSections(True)
        self.current_col = None       # 우클릭한 프록시 열
        self.source_col = None        # 그 열의 소스 열 (Δ 열이면 기준 열)
        self.is_delta = False         # 우클릭한 열이 Δ 열인지 (필터/색칠 경로 분기)
        self.filter_popup = None
        self.setAutoFillBackground(True)
        self.setStyleSheet("QHeaderView::section { background-color: rgb(240, 240, 240); }")

    def paintSection(self, painter, rect, logicalIndex):
        # 열 헤더 폰트 스타일링: 선택 열 / 필터 걸린 열 → Bold, Δ(행간 차이) 열 → Italic.
        # ⚠ 왜 paintSection 인가: `QHeaderView::section` 스타일시트가 걸리면 렌더러가 QStyleSheetStyle
        #   로 바뀌어 (1) 선택 섹션의 native bold 와 (2) headerData FontRole 을 둘 다 무시한다
        #   (QHeaderView 는 애초에 헤더 FontRole 을 안 읽음 — 스타일시트 유무 무관, 측정으로 확인).
        #   그래서 폰트만 painter 에 직접 주입하고, 배경·테두리·정렬·말줄임 등 나머지 렌더는
        #   super()(=native) 에 그대로 위임한다(측정: 주입한 painter 폰트는 super 를 거쳐 텍스트까지 도달).
        # ⚠ 검증: offscreen 은 폰트 굵기를 렌더하지 않아 자동/스크린샷 검증 불가 → 실기 육안.
        opt = QStyleOptionHeader()
        self.initStyleOptionForIndex(opt, logicalIndex)   # 선택 상태(State_On) 조회 (행 수 무관, ≈6µs)
        selected = bool(opt.state & QStyle.StateFlag.State_On)

        model = self.model()
        is_delta = filtered = False
        if model is not None and hasattr(model, "is_delta_column"):
            is_delta = model.is_delta_column(logicalIndex)
            src = model.source_column_of(logicalIndex)
            filtered = model.has_delta_filter(src) if is_delta else src in model.column_filters

        font = QFont(self.font())
        if selected or filtered:       # 셀/열 선택, 또는 필터 걸린 열
            font.setBold(True)
        if is_delta:                   # Δ 열 헤더 (셀 italic 은 별개의 FontRole 경로)
            font.setItalic(True)

        if is_delta:
            # Δ 열 헤더는 배경을 약간 더 어둡게 해 구분한다. super 는 스타일시트(240)로 배경을
            # 덮어버리므로 직접 그린다(텍스트는 좌측정렬·수직중앙 = 일반 헤더 기본 정렬과 동일).
            text = model.headerData(logicalIndex, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
            painter.save()
            painter.fillRect(rect, QColor(223, 223, 223))   # 240 보다 어둡게
            painter.setPen(QColor(208, 208, 208))           # 섹션 구분선(우/하단)
            painter.drawLine(rect.topRight(), rect.bottomRight())
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
            painter.setFont(font)
            painter.setPen(Qt.GlobalColor.black)
            tr = rect.adjusted(4, 0, -4, 0)
            elided = painter.fontMetrics().elidedText(str(text or ""),
                                                      Qt.TextElideMode.ElideRight, tr.width())
            painter.drawText(tr, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), elided)
            painter.restore()
            return

        painter.save()
        painter.setFont(font)
        super().paintSection(painter, rect, logicalIndex)
        painter.restore()

    def contextMenuEvent(self, event):
        if self.filter_popup:
            self.filter_popup.close()

        # Get current column & table model
        self.current_col = self.logicalIndexAt(event.pos())
        if self.current_col < 0:        # 헤더 빈 영역 우클릭 등 -> 무시
            return

        model = self.table_view.model()
        if model is None:
            return
        source_model = model.sourceModel()
        if source_model is None:
            return

        # 클릭한 프록시 열을 소스 열로 변환 (Δ 열이면 그 기준 열). 이후 필터/색칠은 전부 소스 열 기준.
        self.source_col = model.source_column_of(self.current_col)
        self.is_delta = model.is_delta_column(self.current_col)

        # 캐스케이딩 후보값: Δ 열이면 Δ값(스냅샷) 기준, 일반 열이면 원본 값 기준
        if self.is_delta:
            unique_values = model.delta_values_excluding_self(self.source_col)
        else:
            unique_values = model.column_values_excluding_self(self.source_col)

        # Pop up Filter UI as Dialog
        self.filter_popup = FilterWidget(unique_values, self.parent)
        self.filter_popup.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint)

        # Connect signals for the apply and close buttons
        self.filter_popup.button_apply.clicked.connect(self.apply_filter)
        self.filter_popup.button_close.clicked.connect(self.filter_popup.close)
        self.filter_popup.button_clear.clicked.connect(self.clear_filter)
        # 값 항목의 색버튼 -> 그 값을 가진 모든 행 색칠 (팝업은 계속 열어둬 여러 값 연속 색칠 가능)
        self.filter_popup.color_picked.connect(self.paint_value)

        # ☰🡫Δ 버튼: 우클릭한 열 종류에 따라 추가/비활성/삭제로 구성 (popup은 매번 새 인스턴스 → stale 연결 없음)
        #  - Δ 열                  → "X"(삭제). 누르면 그 Δ 열 제거
        #  - 이미 Δ 보유한 원본 열  → 비활성(중복 추가 방지)
        #  - 그 외 일반 열          → 기본(추가). 누르면 source_col 오른쪽에 Δ 열 추가
        btn_delta = self.filter_popup.button_row_delta
        btn_delta.setText("")                        # 텍스트(☰🡫Δ / X) 대신 아이콘 사용
        icon_dir = self.parent.icon_path             # GUI/res (ViewerWindow가 주입)
        add_icon = QIcon(os.path.join(icon_dir, "button_row_delta.png"))
        if self.is_delta:
            btn_delta.setIcon(QIcon(os.path.join(icon_dir, "button_row_delta_delete.png")))
            btn_delta.setToolTip("Remove Δ column")
            btn_delta.clicked.connect(self.remove_delta)
        elif model.has_delta(self.source_col):
            btn_delta.setIcon(add_icon)              # 비활성 → Qt가 자동으로 흐리게 렌더
            btn_delta.setEnabled(False)
        else:
            btn_delta.setIcon(add_icon)
            btn_delta.setToolTip("Add Δ column")
            btn_delta.clicked.connect(self.add_delta)

        # 이 열에 필터가 걸려 있을 때만 "Clear Filter" 활성화 (Δ 열이면 Δ값 필터 기준)
        if self.is_delta:
            self.filter_popup.button_clear.setEnabled(model.has_delta_filter(self.source_col))
        else:
            self.filter_popup.button_clear.setEnabled(self.source_col in model.column_filters)

        # Display popup by mouse click position
        popup_x = event.globalPos().x()  # Mouse click global x-coordinate
        popup_y = event.globalPos().y()  # Mouse click global y-coordinate
        self.filter_popup.move(popup_x, popup_y)
        self.filter_popup.show()


    def apply_filter(self):
        proxy_model = self.table_view.model()

        # 드롭다운에 표시됐던 값과, 그중 체크 해제된 값
        shown     = {cb.text() for cb in self.filter_popup.checkboxes}
        unchecked = {cb.text() for cb in self.filter_popup.checkboxes if not cb.isChecked()}

        # 캐스케이딩: 지금 드롭다운에 보이지 않던 기존 숨김값은 그대로 보존. (Δ 열이면 Δ값 필터로)
        if self.is_delta:
            old_hidden = proxy_model.delta_filters.get(self.source_col, frozenset())
            new_hidden = (old_hidden - shown) | unchecked
            proxy_model.setDeltaFilterForColumn(self.source_col, new_hidden)
            self.filter_popup.button_clear.setEnabled(proxy_model.has_delta_filter(self.source_col))
        else:
            old_hidden = proxy_model.column_filters.get(self.source_col, frozenset())
            new_hidden = (old_hidden - shown) | unchecked
            proxy_model.setFilterForColumn(self.source_col, new_hidden)
            self.filter_popup.button_clear.setEnabled(self.source_col in proxy_model.column_filters)

        # 현재 상태를 새 기준선으로 -> 변동 없으니 Apply 비활성화
        self.filter_popup.mark_applied()

        # Update Search model if visible
        if self.parent.frame_search.isVisible():
            self.parent.search_model.search(self.parent.edit_text_input.text())

    def clear_filter(self):
        # 엑셀 "Clear Filter From [Column]" - 이 열 필터를 한 번에 완전 해제 (Δ 열이면 Δ값 필터)
        proxy_model = self.table_view.model()
        if self.is_delta:
            proxy_model.setDeltaFilterForColumn(self.source_col, [])
        else:
            proxy_model.setFilterForColumn(self.source_col, [])   # 빈 입력 → pop → 완전 해제

        self.filter_popup.close()   # 드롭다운 체크 상태가 stale → 닫기

        # Update Search model if visible
        if self.parent.frame_search.isVisible():
            self.parent.search_model.search(self.parent.edit_text_input.text())

    def paint_value(self, value, color):
        # 이 열에서 value를 가진 모든 소스 행의 셀 전체를 color로 칠한다 (Δ 열이면 Δ값 기준).
        # 행 목록은 색 선택 시점에 1회 스캔(lazy). 색칠은 소스 모델의 highlight_cells에 직접 기록.
        proxy_model = self.table_view.model()
        if proxy_model is None:
            return
        source_model = proxy_model.sourceModel()
        if source_model is None:
            return
        if self.is_delta:
            source_rows = proxy_model.source_rows_with_delta_value(self.source_col, value)
        else:
            source_rows = proxy_model.source_rows_with_value(self.source_col, value)
        source_model.highlight_rows(color, source_rows)        # 실제 열 셀
        proxy_model.color_delta_rows(color, source_rows)       # Δ 열 셀도 동일하게

    def add_delta(self):
        # 일반 열의 ☰🡫Δ -> source_col 오른쪽에 Δ 열 추가(현재 보이는 행 기준 스냅샷)
        proxy_model = self.table_view.model()
        if proxy_model is None:
            return
        proxy_model.add_delta_column(self.source_col)
        self.filter_popup.close()        # 결과(추가된 Δ 열)를 바로 보이도록 팝업 닫기
        self._refresh_search_if_open()

    def remove_delta(self):
        # Δ 열의 "X" -> 그 Δ 열 제거 (source_col = 기준 열)
        proxy_model = self.table_view.model()
        if proxy_model is None:
            return
        proxy_model.remove_delta_column(self.source_col)
        self.filter_popup.close()
        self._refresh_search_if_open()

    def _refresh_search_if_open(self):
        # 열이 추가/삭제되면 검색 결과의 (행,열) 좌표가 어긋날 수 있어 재검색 (apply_filter와 동일 패턴)
        if self.parent.frame_search.isVisible():
            self.parent.search_model.search(self.parent.edit_text_input.text())
