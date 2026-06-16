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

        # Find in Column header (row == -1)
        for col in range(col_count):
            header_text = headers[col] if col < len(headers) else ""
            if header_text and needle in str(header_text).lower():
                self.matches.append((-1, col))

        # Find in data cells - 보이는(프록시) 행만 순회하되 source 데이터를 직접 읽음
        if rows is not None and col_count:
            map_to_source = proxy.mapToSource
            proxy_index = proxy.index
            for proxy_row in range(proxy.rowCount()):
                source_row = map_to_source(proxy_index(proxy_row, 0)).row()
                row_data = rows[source_row]
                for col in range(col_count):
                    if needle in row_data[col].lower():
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
