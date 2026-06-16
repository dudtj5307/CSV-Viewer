import sys
from collections import defaultdict

from PyQt6.QtWidgets import QWidget, QCheckBox, QHeaderView, QApplication
from PyQt6.QtCore import Qt, QEvent, QTimer, QSize

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


class FilterWidget(QWidget, Ui_FilterForm):
    def __init__(self, data_set, parent=None):
        super().__init__(parent)
        self.setupUi(self)

        self.parent = parent
        if parent: self.parent.destroyed.connect(self.close)

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
        if event.type() == QEvent.Type.MouseButtonPress:
            # 전역 좌표를 이 위젯 로컬로 변환해 '바깥 클릭'인지 정확히 판정
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
        # master_checkbox 바로 아래(= .ui의 trailing stretch 위)에 체크박스 삽입
        insert_at = self.verticalLayout.indexOf(self.master_checkbox) + 1
        for item, status in sorted(data_set.items(), key=lambda x: _filter_sort_key(x[0])):
            checkbox = QCheckBox()
            checkbox.setText(item)
            checkbox.setChecked(status)
            checkbox._initial = status          # 적용된 필터 기준선(변동 감지용)
            checkbox.stateChanged.connect(self.checkboxes_to_master)
            self.verticalLayout.insertWidget(insert_at, checkbox)
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

    # master(Select All) 표시를 '보이는' 체크박스 기준 3-state로 갱신
    def _refresh_master(self):
        visible = [cb for cb in self.checkboxes if not cb.isHidden()]
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
        visible = [cb for cb in self.checkboxes if not cb.isHidden()]
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
            cb.setVisible(keyword in cb.text().lower())
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
