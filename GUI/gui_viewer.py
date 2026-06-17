import os
import time

from PyQt6.QtWidgets import QAbstractItemView, QMainWindow, QWidget, QTableView, QApplication, QColorDialog, QLabel, QFileDialog, QMessageBox
from PyQt6.QtGui import QIcon, QBrush, QColor, QMovie
from PyQt6.QtCore import Qt, QTimer, QSize

from utils.table_model import CSVTableModel
from utils.filter_model import CSVFilterProxyModel
from utils.csv_loader import CSVLoaderThread
from utils.search_model import SearchModel

from GUI.ui.dialog_viewer import Ui_ViewerWindow
from GUI.ui.widget_esc import Ui_WidgetESC

from GUI.gui_filter import FilterHeaderView


class ViewerWindow(QMainWindow, Ui_ViewerWindow):
    def __init__(self, icon_path, csv_folder=None):
        super(ViewerWindow, self).__init__(None)
        self.setupUi(self)

        self.icon_path = icon_path       # GUI/res 리소스 경로 (백엔드가 주입)

        self.setWindowFlags(Qt.WindowType.Window)
        self.setAcceptDrops(True)        # CSV 폴더를 창에 드롭하면 새로 로드

        self.setWindowIcon(QIcon(os.path.join(self.icon_path, "button_csv_view.png")))

        # CSV 위치는 3단계로 독립 관리 (전체 경로는 필요시 조합):
        # csv_folder 가 없으면(인자 없이 실행) 폴더 미선택 '빈 상태'로 시작한다.
        if csv_folder:
            folder_full = os.path.normpath(csv_folder)
            self.csv_folder_path = os.path.dirname(folder_full)  # 선택한 폴더가 들어있는 상위 경로
            self.csv_folder_name = os.path.basename(folder_full) # 선택한 CSV 폴더명
        else:
            self.csv_folder_path = ""                            # 폴더 미선택(빈) 상태
            self.csv_folder_name = ""
        self.csv_file_name = None                                # 현재 선택된 CSV 파일명

        self.setWindowTitle(self._window_title())
        self.table_csv.setStyleSheet("QTableView { background-color: white; }")
        self.table_csv.verticalHeader().setStyleSheet("QHeaderView::section:vertical { background-color: rgb(240, 240, 240); }")

        self.button_none.setIcon(QIcon(os.path.join(self.icon_path, "button_none.png")))
        self.button_csv_folder.setIcon(QIcon(os.path.join(self.icon_path, "button_csv_folder_raw.png")))

        # CSV 경로 표시 (edit_csv_path=상위 경로 / edit_csv_path2=폴더명) + 폴더 변경 버튼
        self._set_path_fields()
        self.button_csv_folder.clicked.connect(self.open_csv_folder)

        # edit_csv_path(상위 경로) 전체를 클릭하면 폴더 선택 버튼과 동일 동작 (readOnly라 클릭 전용으로 사용)
        self.edit_csv_path.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_csv_path.mousePressEvent = lambda event: self.open_csv_folder()

        # 폴더명 변경(rename): 폴더명이 실제와 달라지면 버튼 활성화
        self.button_rename.setEnabled(False)
        self.edit_csv_path2.textChanged.connect(self._on_folder_name_edited)
        self.button_rename.clicked.connect(self._rename_folder)

        # Loading spinner - table_csv 뷰포트 정중앙 (단독, 배경 없음)
        self.spinner = QLabel(self.table_csv.viewport())
        self.spinner.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.spinner_movie = QMovie(os.path.join(self.icon_path, "loading_spinner.gif"))
        self.spinner.setMovie(self.spinner_movie)
        if self.spinner_movie.isValid():
            self.spinner_movie.jumpToFrame(0)
            #self.spinner.resize(self.spinner_movie.currentPixmap().size())  # GIF 원본 크기
            self.spinner.resize(QSize(64, 64))  # GIF 원본 크기
        self.spinner.hide()
        self._spinner_delay = QTimer(self)          # 깜빡임 방지: 150ms 이상 걸릴 때만 표시
        self._spinner_delay.setSingleShot(True)
        self._spinner_delay.setInterval(100)
        self._spinner_delay.timeout.connect(self._show_spinner_now)

        # Message overlay (No Data / Loading Fail) - 스피너와 동일하게 뷰포트 정중앙
        self.message_label = QLabel(self.table_csv.viewport())
        self.message_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setStyleSheet("color: rgb(160, 160, 160); font-size: 24px; font-weight: bold;")
        self.message_label.hide()

        # Load csv list
        self.loader_threads = []
        self.cache = {}     # {csv_file_name: {'table_model', 'table_data', 'last_view'}}
        self.load_csv_list()

        # CSV List
        self.list_csv_names.currentItemChanged.connect(self.clicked_csv_list)

        # CSV 이름 검색칸: 입력 문자를 포함하는 항목만 표시 + 우측 'x'(지우기) 버튼
        self.edit_csvname_find.setClearButtonEnabled(True)
        self.edit_csvname_find.textChanged.connect(self._filter_csv_list)

        # ESC widget for closing this window
        self.last_esc_time = 0
        self.widget_esc = QWidget(self)
        self.ui_esc = Ui_WidgetESC()
        self.ui_esc.setupUi(self.widget_esc)
        self.widget_esc.hide()

        # Search Widget
        self.search_model = SearchModel(self.table_csv)
        self.search_model.search_widget_update.connect(self.search_gui_update)
        self.button_forward.clicked.connect(self.search_model.previous_match)
        self.button_backward.clicked.connect(self.search_model.next_match)
        self.button_close.clicked.connect(self.search_gui_hide)
        self.frame_search.setVisible(False)

        # Custom horizontal header with filtering
        self.table_csv.setHorizontalHeader(FilterHeaderView(Qt.Orientation.Horizontal, self))

        # CSV table headers - size
        self.table_csv.horizontalHeader().setDefaultSectionSize(80)     # cell width
        self.table_csv.verticalHeader().setDefaultSectionSize(20)       # cell height
        self.table_csv.verticalHeader().setFixedWidth(48)
        # CSV table headers - alignment
        self.table_csv.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)
        self.table_csv.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)

        # Cell Highlight - 프리셋 색 (objectName -> QColor / None=해제)
        self.highlight_colors = {
            'button_none':   None,
            'button_white':  QColor("white"),
            'button_red':    QColor(255, 150, 150),
            'button_yellow': QColor(255, 250, 150),
            'button_green':  QColor(185, 255, 163),
            'button_blue':   QColor(121, 220, 255),
            'button_purple': QColor(255, 140, 255),
        }
        for btn_name in self.highlight_colors:
            getattr(self, btn_name).clicked.connect(self.highlight_cell)
        self.button_more.clicked.connect(self.pick_custom_color)
        self.last_custom_color = QColor(255, 255, 0)   # 커스텀 색 대화상자 초기값(최근 선택 기억)


    def _apply_highlight(self, color):
        # color: QColor 적용 / None 이면 전체 해제
        entry = self.cache.get(self.csv_file_name)
        proxy_model = entry['table_model'] if entry else None
        if not proxy_model:
            return
        source_indexes = [proxy_model.mapToSource(pi) for pi in self.table_csv.selectedIndexes()]
        proxy_model.sourceModel().highlight_cell(color, source_indexes)
        self.table_csv.clearSelection()

    def highlight_cell(self, event=None):
        # 프리셋 버튼 -> objectName 으로 색 결정
        self._apply_highlight(self.highlight_colors.get(self.sender().objectName()))

    def pick_custom_color(self):
        # 그림판식 색상 선택 (팔레트 + RGB/HSV/Hex + 사용자 정의 색)
        color = QColorDialog.getColor(self.last_custom_color, self, "Select Color")
        if color.isValid():
            self.last_custom_color = color
            self._apply_highlight(color)

    def keyPressEvent(self, event):
        # Initial Ctrl+F Key Pressed
        if event.key() == Qt.Key.Key_F and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.search_gui_init()
            self.search_gui_show()

        # 'ESC' Key Pressed & Search Widget On
        elif event.key() == Qt.Key.Key_Escape and self.frame_search.isVisible():
            self.search_gui_hide()

        # 'ESC' Key Pressed & Search Widget Off
        elif event.key() == Qt.Key.Key_Escape and not self.frame_search.isVisible():
            if time.time() - self.last_esc_time < 1:  # ESC pressed interval time < 1sec
                self.close()
            self.last_esc_time = time.time()    # Update last esc pressed time
            self.show_esc_message()

        # 'Enter' Key Pressed & Search Widget On
        elif ((event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter)
               and self.frame_search.isVisible() and self.edit_text_input.hasFocus()):
            self.search_model.search(self.edit_text_input.text())

        # 'F2' or 'Shift+F3' Key Pressed -> Previous
        elif event.key() == Qt.Key.Key_F2 or (event.key() == Qt.Key.Key_F3 and event.modifiers() == Qt.KeyboardModifier.ShiftModifier):
            self.search_model.previous_match()

        # 'F3' Key Pressed -> Next
        elif event.key() == Qt.Key.Key_F3:
            self.search_model.next_match()

        # 'Ctrl+C' Key Pressed -> Copy Selection
        elif event.key() == Qt.Key.Key_C and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.copy_selection()

        # 'F5' Key Pressed -> list 포커스: 폴더 CSV 목록 갱신 / 그 외(table 등): 현재 CSV 데이터 갱신
        elif event.key() == Qt.Key.Key_F5:
            if self.list_csv_names.hasFocus():
                self.reload_csv_list()
            else:
                self.reload_current_csv()

        # 'Home' Key Pressed
        elif event.key() == Qt.Key.Key_Home:
            self.table_csv.scrollToTop()
        # 'End' Key Pressed
        elif event.key() == Qt.Key.Key_End:
            self.table_csv.scrollToBottom()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.spinner.isVisible():
            self._center_spinner()
        if self.message_label.isVisible():
            self._center_message()

    def show_esc_message(self):
        pos_x = (self.width() - self.widget_esc.width()) // 2
        pos_y = (self.height() - self.widget_esc.height()) // 2
        self.widget_esc.setGeometry(pos_x, pos_y, self.widget_esc.width(), self.widget_esc.height(),)
        self.widget_esc.show()
        QTimer.singleShot(1000, self.widget_esc.hide)  # 1000 (ms)

    def add_item(self, csv_file_name):
        self.list_csv_names.addItem(csv_file_name)   # 리스트 표시명 = CSV 파일명(.csv 제외)

        # TODO: 구분선 표시...
        # item = QListWidgetItem(csv_file_name)
        # item.setData(Qt.ItemDataRole.UserRole, "border-bottom: 1px solid rgb(225, 225, 225); padding-left: 2px;")
        # self.list_csv_names.addItem(item)

    def paint_list_csv(self, csv_file_name, color):
        for item in self.list_csv_names.findItems(csv_file_name, Qt.MatchFlag.MatchExactly):
            item.setBackground(QBrush(QColor(color[0], color[1], color[2])))

    def load_csv_list(self):
        for file_name in self._safe_listdir():
            if file_name.lower().endswith('.csv'):
                self.add_item(file_name.split('.csv')[0])   # 표시명 = 확장자 제외

    def _filter_csv_list(self, text):
        # CSV 이름 검색: 입력 문자열을 포함하는 항목만 표시 (대소문자 무시)
        keyword = text.strip().lower()
        for i in range(self.list_csv_names.count()):
            item = self.list_csv_names.item(i)
            item.setHidden(keyword not in item.text().lower())

    def _safe_listdir(self):
        # 폴더가 이동/삭제/이름변경됐을 수 있으므로 안전하게 (없으면 빈 목록 → 크래시 방지)
        if not self.csv_folder_name:        # 폴더 미선택(빈) 상태
            return []
        try:
            return os.listdir(self._folder())
        except OSError:
            return []

    # ---------- 경로 헬퍼 (3단계 조합) ----------
    def _window_title(self):
        # 폴더 미선택(빈) 상태면 폴더명 없이 표시
        return f"{self.csv_folder_name} - CSV Viewer" if self.csv_folder_name else "CSV Viewer"

    def _folder(self):
        # 선택된 CSV 폴더의 전체 경로
        return os.path.join(self.csv_folder_path, self.csv_folder_name)

    def _file_path(self, csv_file_name):
        # 특정 CSV 파일의 전체 경로 (표시명 + .csv)
        return os.path.join(self.csv_folder_path, self.csv_folder_name, csv_file_name + ".csv")

    def _set_path_fields(self):
        # edit_csv_path = 상위 경로, edit_csv_path2 = 폴더명
        self.edit_csv_path.setText(self.csv_folder_path)
        self.edit_csv_path2.setText(self.csv_folder_name)
        self.edit_csv_path.setCursorPosition(0)
        self.edit_csv_path2.setCursorPosition(0)

    def _on_folder_name_edited(self):
        # edit_csv_path2(폴더명)가 실제 폴더명과 달라졌고 비어있지 않으면 rename 활성화
        name = self.edit_csv_path2.text().strip()
        self.button_rename.setEnabled(bool(name) and name != self.csv_folder_name)

    def _rename_folder(self):
        new_name = self.edit_csv_path2.text().strip()
        old_folder = self._folder()
        new_folder = os.path.join(self.csv_folder_path, new_name)

        # --- 예외/검증 ---
        if not new_name or new_name == self.csv_folder_name:
            return
        if any(t.isRunning() for t in self.loader_threads):
            QMessageBox.information(self, "Rename Denied", "CSV 로딩 중에는 폴더명을 변경할 수 없습니다.")
            return
        if new_name in ('.', '..') or any(c in new_name for c in '<>:"/\\|?*') or new_name.endswith('.'):
            QMessageBox.warning(self, "Rename Denied", "폴더명에 사용할 수 없는 문자가 포함되어 있습니다.\n \' \" < > : / \\ | ? *")
            return
        # 대소문자만 바꾸는 경우(같은 폴더)는 허용, 그 외 동일 경로 존재 시 차단
        if os.path.exists(new_folder) and os.path.normcase(new_folder) != os.path.normcase(old_folder):
            QMessageBox.warning(self, "Rename Denied", f"경로에 동일한 이름의 폴더가 있습니다.\n {new_name}")
            return
        if not os.path.isdir(old_folder):
            QMessageBox.critical(self, "Rename Denied", "원본 폴더를 찾을 수 없습니다.")
            return

        # --- 실제 변경 (OS 제공 os.rename) ---
        try:
            os.rename(old_folder, new_folder)
        except OSError as e:
            QMessageBox.critical(self, "Rename Denied",
                                 f"폴더명 변경에 실패했습니다.\n폴더 또는 파일이 열려있거나 변경 권한이 없을 수 있습니다.\n\n{e}")
            return

        # --- 성공: 폴더명만 갱신 (경로는 조합식, 캐시는 파일명 키라 그대로 유효) ---
        self.csv_folder_name = new_name
        self.setWindowTitle(self._window_title())
        self._set_path_fields()
        self.button_rename.setEnabled(False)

    def open_csv_folder(self):
        # 상위 경로에서 시작하는 폴더 선택 -> 선택 시 해당 CSV 폴더로 다시 로드
        initial = self.csv_folder_path or self._folder()
        folder = QFileDialog.getExistingDirectory(self, "Select CSV folder", initial)
        if folder:
            self._load_folder(os.path.normpath(folder))

    # --- 창에 CSV 폴더 드롭 -> 새로 로드 (PathLineEdit 방식) ---
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
        folders = [p for p in paths if os.path.isdir(p)]
        if len(folders) == 1:
            self._load_folder(os.path.normpath(folders[0]))

    def _load_folder(self, folder_full):
        # 다른 폴더 선택 -> 3단계 경로 갱신 + 상태 초기화 후 재로드 (캐시는 내용이 달라 비움)
        self.csv_folder_path = os.path.dirname(folder_full)
        self.csv_folder_name = os.path.basename(folder_full)
        self.csv_file_name = None
        self.setWindowTitle(self._window_title())
        self._set_path_fields()

        self._close_ui_overlays()
        self._stop_spinner()
        self.list_csv_names.blockSignals(True)
        self.list_csv_names.clear()
        self.list_csv_names.blockSignals(False)
        self.cache.clear()
        self.table_csv.setModel(None)
        self.load_csv_list()
        self.list_csv_names.scrollToTop()                       # 새 폴더 목록은 항상 맨 위부터
        self.list_csv_names.horizontalScrollBar().setValue(0)   # 가로 스크롤도 초기화
        self.edit_csvname_find.clear()                          # 새 폴더 -> 이름 검색 초기화

    def _close_ui_overlays(self):
        self.search_gui_hide()
        self._hide_message()
        header = self.table_csv.horizontalHeader()
        if hasattr(header, "filter_popup") and header.filter_popup:
            header.filter_popup.close()

    def _ensure_cache(self, csv_file_name):
        if csv_file_name not in self.cache:
            self.cache[csv_file_name] = {'table_model': None, 'table_data': None, 'last_view': None, 'status': None}
        return self.cache[csv_file_name]

    def _center_spinner(self):
        vp = self.table_csv.viewport()
        self.spinner.move((vp.width()  - self.spinner.width())  // 2,
                          (vp.height() - self.spinner.height()) // 2)

    def _start_spinner(self):
        self._spinner_delay.start()       # 빠르게 끝나면 안 뜨도록 지연 표시 예약

    def _show_spinner_now(self):
        if not self.spinner_movie.isValid():
            return
        self._center_spinner()
        self.spinner.raise_()
        self.spinner.show()
        self.spinner_movie.start()

    def _stop_spinner(self):
        self._spinner_delay.stop()        # 예약된(미표시) 표시 취소
        self.spinner_movie.stop()
        self.spinner.hide()

    def _center_message(self):
        vp = self.table_csv.viewport()
        self.message_label.move((vp.width()  - self.message_label.width())  // 2,
                                (vp.height() - self.message_label.height()) // 2)

    def _show_message(self, text):
        self.message_label.setText(text)
        self.message_label.adjustSize()
        self._center_message()
        self.message_label.raise_()
        self.message_label.show()

    def _hide_message(self):
        self.message_label.hide()

    def _start_loading(self, csv_file_name):
        self.table_csv.setModel(None)   # table_csv 초기화
        self._start_spinner()
        self.paint_list_csv(csv_file_name, (255, 255, 225))  # Yellow
        thread = CSVLoaderThread(self._file_path(csv_file_name))
        # 로더는 전체 경로로 읽고, 콜백엔 파일명(식별자)을 넘긴다
        thread.load_complete.connect(lambda path, data, n=csv_file_name: self.csv_load_complete(n, data))
        thread.load_failed.connect(lambda path, n=csv_file_name: self.csv_load_failed(n))
        thread.load_empty.connect(lambda path, n=csv_file_name: self.csv_load_empty(n))
        thread.finished.connect(lambda t=thread: self._cleanup_loader(t))
        self.loader_threads.append(thread)     # 실행 중 참조 유지 (조기 GC 방지)
        thread.start()

    def _cleanup_loader(self, thread):
        if thread in self.loader_threads:
            self.loader_threads.remove(thread)
        thread.deleteLater()

    def clicked_csv_list(self):
        # Save Last View (ScrollBar)
        prev = self.cache.get(self.csv_file_name)
        if prev and prev['table_model']:
            v = self.table_csv.verticalScrollBar().value()
            h = self.table_csv.horizontalScrollBar().value()
            prev['last_view'] = (v, h)

        self._close_ui_overlays()

        current = self.list_csv_names.currentItem()
        if current is None:
            return
        self.csv_file_name = current.text()

        if self.csv_file_name in self.cache:
            self.update_table(self.csv_file_name)
        else:
            self._start_loading(self.csv_file_name)

    def reload_csv_list(self):
        # list 포커스 F5: 디스크의 .csv 목록과 동기화 (추가/삭제만 반영, 나머지 캐시·작업은 보존)
        disk = [f.split('.csv')[0] for f in self._safe_listdir() if f.lower().endswith('.csv')]
        disk_set = set(disk)

        # 사라진 파일의 캐시만 제거 (남은 파일의 필터/하이라이트/스크롤/데이터는 그대로)
        for name in [n for n in self.cache if n not in disk_set]:
            del self.cache[name]

        prev_selected = self.csv_file_name

        # 로드 상태 색 복원용 (기존 paint_list_csv 색과 동일)
        status_color = {'ok': (230, 255, 230), 'empty': (220, 220, 220), 'fail': (255, 230, 230)}

        # 리스트를 디스크 순서로 재구성 (선택 변경 시그널 차단 -> 불필요한 재로딩 방지)
        self.list_csv_names.blockSignals(True)
        self.list_csv_names.clear()
        for name in disk:
            self.add_item(name)
            entry = self.cache.get(name)
            if entry and entry['status'] in status_color:
                self.paint_list_csv(name, status_color[entry['status']])
        # 이전에 보던 CSV가 아직 있으면 조용히 다시 선택 (테이블은 그대로 두어 스크롤·필터 보존)
        if prev_selected in disk_set:
            items = self.list_csv_names.findItems(prev_selected, Qt.MatchFlag.MatchExactly)
            if items:
                self.list_csv_names.setCurrentItem(items[0])
        self.list_csv_names.blockSignals(False)

        # 보던 CSV가 삭제됐으면 화면 정리
        if prev_selected is not None and prev_selected not in disk_set:
            self.csv_file_name = None
            self._close_ui_overlays()
            self._stop_spinner()
            self.table_csv.setModel(None)

        # 검색칸에 입력이 있으면 갱신된 목록에도 동일 필터 재적용
        self._filter_csv_list(self.edit_csvname_find.text())

    def reload_current_csv(self):
        if not self.csv_file_name or self.csv_file_name not in self.cache:
            return

        self._close_ui_overlays()
        del self.cache[self.csv_file_name]

        self.table_csv.setModel(None)
        self._start_loading(self.csv_file_name)

    def csv_load_complete(self, csv_file_name, data):
        # Save in cache
        entry = self._ensure_cache(csv_file_name)
        entry['table_data'] = data
        entry['status'] = 'ok'
        self.update_table(csv_file_name)
        self.paint_list_csv(csv_file_name, (230, 255, 230))  # Green

    def csv_load_empty(self, csv_file_name):
        # 디코딩은 됐지만 데이터 행이 없음 -> No Data
        entry = self._ensure_cache(csv_file_name)
        entry['table_data'] = None
        entry['status'] = 'empty'
        self.update_table(csv_file_name)
        self.paint_list_csv(csv_file_name, (220, 220, 220))  # Gray

    def csv_load_failed(self, csv_file_name):
        # 파일이 사라져서 실패한 경우 -> 목록을 동기화해 없어진 항목 정리 (디코딩 실패는 기존대로 표시)
        if not os.path.isfile(self._file_path(csv_file_name)):
            self.reload_csv_list()
            return
        # Save in cache
        entry = self._ensure_cache(csv_file_name)
        entry['table_data'] = None
        entry['status'] = 'fail'
        self.update_table(csv_file_name)
        self.paint_list_csv(csv_file_name, (255, 230, 230))  # Red

    def update_table(self, csv_file_name=""):
        # Return if not currently selected
        current = self.list_csv_names.currentItem()
        if current is None or current.text() != csv_file_name:
            return

        self._stop_spinner()      # 현재 CSV 화면 갱신 시점 -> 스피너 종료
        self._hide_message()

        # Return if No data to load
        entry = self.cache.get(csv_file_name)
        if not entry or not entry['table_data']:
            self.table_csv.setModel(None)
            if entry and entry['status'] == 'empty':
                self._show_message("No Data")
            elif entry and entry['status'] == 'fail':
                self._show_message("Loading Fail")
            return

        # Look if there is model already created
        if entry['table_model']:
            self.table_csv.setModel(entry['table_model'])
        else:
            model = CSVTableModel(entry['table_data'], csv_file_name)
            model.load_fail.connect(self.csv_load_failed)
            proxy_model = CSVFilterProxyModel()
            proxy_model.setSourceModel(model)
            entry['table_model'] = proxy_model
            self.table_csv.setModel(proxy_model)

        self.table_csv.horizontalHeader().setDefaultSectionSize(80)     # cell width
        self.table_csv.verticalHeader().setDefaultSectionSize(20)       # cell height

        # Set Last View (ScrollBar)
        last_view = entry['last_view']
        if last_view:
            self.table_csv.verticalScrollBar().setValue(last_view[0])
            self.table_csv.horizontalScrollBar().setValue(last_view[1])
        else:
            self.table_csv.scrollTo(self.table_csv.model().index(0, 0), QAbstractItemView.ScrollHint.PositionAtTop)

    def copy_selection(self):
        table = self.table_csv
        model = table.model()
        sel = table.selectionModel()
        if model is None or sel is None:
            return
        indexes = table.selectedIndexes()
        if not indexes:
            return

        rows = sorted({i.row() for i in indexes})
        cols = sorted({i.column() for i in indexes})
        cells = {(i.row(), i.column()): str(i.data() or '') for i in indexes}

        full_cols = bool(sel.selectedColumns())   # 열 전체 선택 → 맨 위에 열 이름 포함
        full_rows = bool(sel.selectedRows())      # 행 전체 선택 → 맨 앞에 행 번호 포함
        source = model.sourceModel() or model     # 열 이름은 '*' 없는 원본 헤더에서
        H, V, DISP = Qt.Orientation.Horizontal, Qt.Orientation.Vertical, Qt.ItemDataRole.DisplayRole

        lines = []
        if full_cols:
            head = [''] if full_rows else []      # 둘 다면 좌상단 코너는 빈칸
            head += [str(source.headerData(c, H, DISP) or '') for c in cols]
            lines.append('\t'.join(head))
        for r in rows:
            line = [str(model.headerData(r, V, DISP) or '')] if full_rows else []
            line += [cells.get((r, c), '') for c in cols]
            lines.append('\t'.join(line))

        QApplication.clipboard().setText('\n'.join(lines))

    def search_gui_show(self):
        self.frame_search.setVisible(True)
        self.edit_text_input.setFocus()

    def search_gui_hide(self):
        self.frame_search.setVisible(False)

    def search_gui_init(self):
        self.edit_text_input.clear()
        self.label_idx_count.clear()
        self.button_backward.setDisabled(True)
        self.button_forward.setDisabled(True)

    def search_gui_update(self, current_idx, total_count):
        self.label_idx_count.setText(f"{current_idx}/{total_count}")
        if total_count <= 1:
            self.button_forward.setDisabled(True)
            self.button_backward.setDisabled(True)
        else:
            self.button_forward.setDisabled(False)
            self.button_backward.setDisabled(False)

    def closeEvent(self, event):
        # 진행 중인 로더 스레드 정리 (실행 중 GC로 인한 크래시 방지)
        for thread in list(self.loader_threads):
            try:
                thread.load_complete.disconnect()
                thread.load_failed.disconnect()
                thread.load_empty.disconnect()
            except TypeError:
                pass
            thread.wait()
        self.deleteLater()  # 창이 닫힐 때 객체를 완전히 삭제
        super().closeEvent(event)