import sys
from collections import defaultdict

from PyQt6.QtWidgets import (QWidget, QCheckBox, QHeaderView, QApplication,
                             QPushButton, QHBoxLayout, QSizePolicy, QColorDialog)
from PyQt6.QtCore import Qt, QEvent, QTimer, QSize, pyqtSignal
from PyQt6.QtGui import QColor

from GUI.ui.dialog_filter import Ui_FilterForm


def _filter_sort_key(text):
    """필터 목록 정렬 키 (엑셀 정렬과 유사).

    순수 숫자(0-9로만 구성)는 숫자값 오름차순으로, 문자가 섞인 값은
    그 뒤에 사전순(대소문자 무시)으로 배치한다.
    예) 100, 11, 133, 1F3, 230  →  11, 100, 133, 230, 1F3
        ('1F3'은 'F' 때문에 문자 그룹으로 분류되어 맨 뒤)
    """
    if text.isascii() and text.isdigit():
        return (0, int(text), "")
    return (1, 0, text.lower())


class _FilterItemRow(QWidget):
    """필터 항목 한 줄. 체크박스가 줄 전체를 채우고, 색버튼은 레이아웃에 넣지 않고
    줄 오른쪽 끝에 '오버레이'로 띄운다(resizeEvent에서 재배치). 그래서:
    - 텍스트 길이와 무관하게 색버튼이 항상 줄 최우측에 보이고,
    - 긴 텍스트는 버튼 아래로 깔린다(겹침),
    - 체크박스 폭 정책이 Ignored라 줄이 텍스트만큼 넓어지지 않아 가로 스크롤이 안 생긴다.
    """
    RIGHT_MARGIN = 3

    def __init__(self, checkbox, color_btn, parent=None):
        super().__init__(parent)
        self._color_btn = color_btn
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(checkbox)        # 체크박스는 줄 전체를 채움
        color_btn.setParent(self)         # 색버튼은 레이아웃 밖 자식 = 오버레이
        color_btn.raise_()                # 체크박스 텍스트 위에 그려지도록

    def resizeEvent(self, event):
        super().resizeEvent(event)
        b = self._color_btn
        b.move(self.width() - b.width() - self.RIGHT_MARGIN,
               (self.height() - b.height()) // 2)
        b.raise_()


class FilterWidget(QWidget, Ui_FilterForm):
    # 값 항목의 색버튼으로 색을 고르면 (값, QColor) 방출 -> FilterHeaderView가 받아 색칠
    color_picked = pyqtSignal(str, object)

    def __init__(self, data_set, parent=None):
        super().__init__(parent)
        self.setupUi(self)

        self.parent = parent
        if parent: self.parent.destroyed.connect(self.close)

        # 색 대화상자가 떠 있는 동안엔 '바깥 클릭=닫힘'을 막는 가드 (대화상자 클릭이 바깥으로 잡혀
        # 팝업이 먼저 닫히는 것 방지). 이벤트필터 설치 전에 먼저 정의해야 한다.
        self._dialog_open = False

        # Global EventFilter for noticing clicked outside this widget -> this widget closing
        QApplication.instance().installEventFilter(self)

        # Set Focus to this widget
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

        # Create the checkbox items in the scroll area's layout
        self.create_items(data_set)

    def keyPressEvent(self, event):
        # Key 'ESC' - Close widget
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress and not self._dialog_open:
            # 전역 좌표를 이 위젯 로컬로 변환해 '바깥 클릭'인지 정확히 판정
            # (색 대화상자가 열려 있을 땐 그 안의 클릭을 '바깥'으로 오인하지 않도록 가드)
            local = self.mapFromGlobal(event.globalPosition().toPoint())
            if not self.rect().contains(local):
                self.close()
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        # Remove the event filter when the widget is closed
        QApplication.instance().removeEventFilter(self)
        super().closeEvent(event)

    def create_items(self, data_set):
        self.checkboxes = []
        # master_checkbox 바로 아래(= .ui의 trailing stretch 위)에 항목 행 삽입
        insert_at = self.verticalLayout.indexOf(self.master_checkbox) + 1
        for item, status in sorted(data_set.items(), key=lambda x: _filter_sort_key(x[0])):
            checkbox = QCheckBox()
            checkbox.setText(item)
            checkbox.setChecked(status)
            checkbox._initial = status          # 적용된 필터 기준선(변동 감지용)
            # ⚠ Ignored 폭: 텍스트가 길어도 줄(=scroll 내용)이 그만큼 넓어지지 않게 → 가로 스크롤 방지.
            #   그래야 색버튼이 텍스트에 밀려 화면 밖으로 나가지 않는다.
            checkbox.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            checkbox.stateChanged.connect(self.checkboxes_to_master)

            # 우측 색버튼: 누르면 색 선택 -> 이 값을 가진 모든 행 색칠 (선택 후 그 색으로 채워 피드백)
            color_btn = QPushButton()
            color_btn.setFixedSize(QSize(16, 16))
            color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            color_btn.setToolTip("Fill rows with this")
            color_btn._value = item
            color_btn._color = None
            self._style_color_button(color_btn, None)
            color_btn.clicked.connect(lambda _checked, b=color_btn: self._pick_color(b))

            # 색버튼을 줄 오른쪽 끝 오버레이로 (텍스트는 그 아래로 깔림). 가시성 토글은 이 줄 기준.
            row = _FilterItemRow(checkbox, color_btn)
            checkbox._row = row
            self.verticalLayout.insertWidget(insert_at, row)
            insert_at += 1
            self.checkboxes.append(checkbox)

        # (Select All) - 3-state 표시, 사용자 클릭만 처리(clicked)
        self.master_checkbox.setTristate(True)
        self.master_checkbox.clicked.connect(self.master_clicked)

        # 검색칸: 글자 있으면 우측에 'x'(지우기) 버튼, 입력마다 일치 항목만 표시
        self.edit_filter_text.setClearButtonEnabled(True)
        self.edit_filter_text.textChanged.connect(self.filter_items)

        # Update widget size based on its content
        scrollbox_height = min(self.widget.sizeHint().height() + 20, 160)
        self.scrollArea.setMinimumHeight(scrollbox_height)

        self.setMaximumSize(QSize(400, 400))

        # 초기 master/Apply 상태를 데이터 기준으로 정확히 설정
        self._refresh_master()

    # 색버튼 외형: 미선택이면 무지개 그라데이션('색 고르기' 암시, gui_viewer.button_more식),
    # 색을 고른 뒤엔 그 색으로 채워 '이 값은 칠했음' 피드백
    def _style_color_button(self, btn, color):
        if color is None:
            fill = ("qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                    " stop:0 #FF0000, stop:0.1 #FF0000, stop:0.3 #FF7F00, stop:0.4 #FFFF00,"
                    " stop:0.5 #00CC00, stop:0.6 #0000FF, stop:0.7 #4B0082, stop:0.9 #9400D3, stop:1 #9400D3)")
        else:
            fill = color.name()
        btn.setStyleSheet(
            "QPushButton {"
            "  border: 1px solid rgb(120, 120, 120); border-radius: 8px;"
            f"  background-color: {fill};"
            "}"
            "QPushButton:hover { border: 1px solid #333; }"
        )

    # 색버튼 클릭 -> 색 선택 대화상자(그림판식). 고르면 (값, 색) 방출 + 버튼을 그 색으로 갱신.
    def _pick_color(self, btn):
        initial = btn._color if btn._color is not None else QColor(255, 255, 0)
        parent = self.parent if self.parent else self
        self._dialog_open = True   # 대화상자 클릭에 팝업이 닫히지 않도록 (eventFilter 가드)
        try:
            color = QColorDialog.getColor(initial, parent, "Select Color")
        finally:
            self._dialog_open = False
        if color.isValid():
            btn._color = color
            self._style_color_button(btn, color)
            self.color_picked.emit(btn._value, color)
        # 대화상자가 닫히면 포커스가 부모창으로 넘어가 팝업이 '비활성'(체크박스가 전부 해제된 듯
        # 회색)으로 보인다. 선택 여부와 무관하게 팝업을 다시 활성화/포커스해 표시를 복원한다.
        self.activateWindow()
        self.raise_()
        self.setFocus()

    # master(Select All) 표시를 '보이는' 체크박스 기준 3-state로 갱신
    def _refresh_master(self):
        visible = [cb for cb in self.checkboxes if not cb._row.isHidden()]
        n_checked = sum(cb.isChecked() for cb in visible)
        if not visible or n_checked == 0:
            state = Qt.CheckState.Unchecked
        elif n_checked == len(visible):
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        self.master_checkbox.blockSignals(True)
        self.master_checkbox.setCheckState(state)
        self.master_checkbox.blockSignals(False)
        # Apply: 적용된 필터 대비 '변동'이 있고, 최소 1개는 체크돼 있을 때만 활성
        changed = any(cb.isChecked() != cb._initial for cb in self.checkboxes)
        any_checked = any(cb.isChecked() for cb in self.checkboxes)
        self.button_apply.setEnabled(changed and any_checked)

    # apply 직후: 현재 상태를 새 기준선으로 삼아 Apply 비활성화
    def mark_applied(self):
        for cb in self.checkboxes:
            cb._initial = cb.isChecked()
        self._refresh_master()

    # 개별 체크박스 변경 -> master 표시 갱신
    def checkboxes_to_master(self, state):
        self._refresh_master()

    # (Select All) 사용자 클릭 -> '보이는' 항목만 전체 선택/해제 (엑셀 Select All Search Results)
    def master_clicked(self, _checked=False):
        visible = [cb for cb in self.checkboxes if not cb._row.isHidden()]
        if not visible:
            return
        target = not all(cb.isChecked() for cb in visible)
        for cb in visible:
            cb.blockSignals(True)
            cb.setChecked(target)
            cb.blockSignals(False)
        self._refresh_master()

    # 검색어와 부분 일치하는 체크박스만 표시 (대소문자 무시) + master 갱신
    def filter_items(self, text):
        keyword = text.strip().lower()
        for cb in self.checkboxes:
            cb._row.setVisible(keyword in cb.text().lower())   # 체크박스가 아닌 '행' 전체를 토글
        self._refresh_master()


class FilterHeaderView(QHeaderView):
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.parent = parent
        self.table_view = parent.table_csv
        self.setSectionsClickable(True)
        self.current_col = None
        self.filter_popup = None
        self.setAutoFillBackground(True)
        self.setStyleSheet("QHeaderView::section { background-color: rgb(240, 240, 240); }")

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

        # 캐스케이딩: 이 열을 제외한 나머지 필터를 통과한 행에서만 후보값 수집
        unique_values = model.column_values_excluding_self(self.current_col)

        # Pop up Filter UI as Dialog
        self.filter_popup = FilterWidget(unique_values, self.parent)
        self.filter_popup.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint)

        # Connect signals for the apply and close buttons
        self.filter_popup.button_apply.clicked.connect(self.apply_filter)
        self.filter_popup.button_close.clicked.connect(self.filter_popup.close)
        self.filter_popup.button_clear.clicked.connect(self.clear_filter)
        # 값 항목의 색버튼 -> 그 값을 가진 모든 행 색칠 (팝업은 계속 열어둬 여러 값 연속 색칠 가능)
        self.filter_popup.color_picked.connect(self.paint_value)

        # 이 열에 적용된 필터가 있을 때만 "Clear Filter" 활성화
        self.filter_popup.button_clear.setEnabled(self.current_col in model.column_filters)

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

        # 캐스케이딩: 지금 드롭다운에 보이지 않던 기존 숨김값은 그대로 보존
        old_hidden = proxy_model.column_filters.get(self.current_col, frozenset())
        new_hidden = (old_hidden - shown) | unchecked

        proxy_model.setFilterForColumn(self.current_col, new_hidden)

        # Apply 직후 필터 유무에 맞춰 "Clear Filter" 활성화 즉시 갱신
        self.filter_popup.button_clear.setEnabled(self.current_col in proxy_model.column_filters)

        # 현재 상태를 새 기준선으로 -> 변동 없으니 Apply 비활성화
        self.filter_popup.mark_applied()

        # Update Search model if visible
        if self.parent.frame_search.isVisible():
            self.parent.search_model.search(self.parent.edit_text_input.text())

    def clear_filter(self):
        # 엑셀 "Clear Filter From [Column]" - 이 열 필터를 한 번에 완전 해제
        proxy_model = self.table_view.model()
        proxy_model.setFilterForColumn(self.current_col, [])   # 빈 입력 → pop → 완전 해제

        self.filter_popup.close()   # 드롭다운 체크 상태가 stale → 닫기

        # Update Search model if visible
        if self.parent.frame_search.isVisible():
            self.parent.search_model.search(self.parent.edit_text_input.text())

    def paint_value(self, value, color):
        # 이 열(current_col)에서 value를 가진 모든 소스 행의 셀 전체를 color로 칠한다.
        # 행 목록은 색 선택 시점에 1회 스캔(lazy). 색칠은 소스 모델의 highlight_cells에 직접 기록.
        proxy_model = self.table_view.model()
        if proxy_model is None:
            return
        source_model = proxy_model.sourceModel()
        if source_model is None:
            return
        source_rows = proxy_model.source_rows_with_value(self.current_col, value)
        source_model.highlight_rows(color, source_rows)


if __name__ == "__main__":
    # from PyQt6.QtWidgets import QApplication
    # app = QApplication(sys.argv)
    # sample_data = {"EIE_0x4001111111111111":True, "EIE_0x4002":True, "EIE_0x4003":True, "EIE_0x4004":True}
    # frame = FilterWidget(sample_data)
    # frame.show()
    # sys.exit(app.exec())
    from collections import defaultdict

    d = defaultdict(lambda: True)
    print(d[None])
