import os
import time
import hashlib
from bisect import bisect_right

from PyQt6.QtWidgets import QAbstractItemView, QMainWindow, QWidget, QTableView, QApplication, QColorDialog, QLabel, QFileDialog, QMessageBox, QGraphicsDropShadowEffect
from PyQt6.QtGui import QIcon, QPixmap, QBrush, QColor, QMovie
from PyQt6.QtCore import Qt, QTimer, QSize, QEvent

from utils.table_model import CSVTableModel
from utils.filter_model import CSVFilterProxyModel
from utils.csv_loader import CSVLoaderThread
from utils.search_model import SearchModel
from utils.edit_history import EditHistory, Memento
from utils import view_state

from GUI.ui.dialog_viewer import Ui_ViewerWindow
from GUI.ui.widget_esc import Ui_WidgetESC

from GUI.gui_header import FilterHeaderView
from GUI.gui_delegate import CompareBorderDelegate


class ViewerWindow(QMainWindow, Ui_ViewerWindow):
    # ESC 연타로 창 닫기: 첫 ESC 후 이 시간(초) 이내 다시 누르면 닫힘.
    # ESC 안내 토스트가 떠 있는 시간도 동일 값으로 묶는다(연타 유효 시간 = 안내 노출 시간).
    ESC_INTERVAL_SEC = 0.5

    # Undo/Redo(Ctrl+Z/Ctrl+Y): CSV별로 되돌릴 수 있는 최대 '액션' 수 (baseline 제외).
    MAX_UNDO_STEPS = 20

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

        self.button_reset.setIcon(QIcon(os.path.join(self.icon_path, "button_reset.png")))
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

        # 짧은 알림 토스트 (Ctrl+S 저장 결과 등) - table_csv 오른쪽 위 구석에 네모 박스, 일정 시간 뒤 자동 숨김.
        # 배경색(성공=초록 / 실패=빨강)은 _show_toast 에서 매번 설정한다.
        self.toast = QLabel(self)
        self.toast.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast.hide)

        # Load csv list
        self.loader_threads = []
        self.cache = {}     # {csv_file_name: {'table_model', 'table_data', 'last_view', 'col_widths', 'signature', 'content_hash', 'status'}}
        self.saved_states = {}      # {csv_file_name: 저장된 분석상태} - 폴더 .viewer 에서 로드(각 CSV 최초 열람 시 hash 일치하면 적용)
        self._load_view_states()
        self.load_csv_list()


        # CSV List
        self.list_csv_names.currentItemChanged.connect(self.clicked_csv_list)

        # CSV 이름 검색칸: 입력 문자를 포함하는 항목만 표시 + 우측 'x'(지우기) 버튼
        self.edit_csvname_find.setClearButtonEnabled(True)
        self.edit_csvname_find.textChanged.connect(self._filter_csv_list)

        # ESC widget for closing this window  (둥근 토스트 + 가장자리로 옅어지는 그림자)
        self.last_esc_time = 0
        self.widget_esc = QWidget(self)
        self.widget_esc.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.ui_esc = Ui_WidgetESC()
        self.ui_esc.setupUi(self.widget_esc)
        self._style_esc_toast()
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

        # Δ 셀 선택 시 비교한 두 부모셀에 테두리(현재=파랑/이전=빨강)를 그리는 delegate
        self.border_delegate = CompareBorderDelegate(self.table_csv)
        self.table_csv.setItemDelegate(self.border_delegate)
        self._wired_sel_model = None     # 핸들러를 연결한 selectionModel 추적(setModel 마다 새로 생겨 중복 연결 방지)

        # CSV table headers - size
        self.table_csv.horizontalHeader().setDefaultSectionSize(80)     # cell width
        self.table_csv.verticalHeader().setDefaultSectionSize(20)       # cell height
        self.table_csv.verticalHeader().setFixedWidth(48)
        # CSV table headers - alignment
        self.table_csv.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)
        self.table_csv.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)

        # Cell Highlight - 프리셋 색 (objectName -> QColor / None=해제)
        self.highlight_colors = {
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
        self.button_reset.clicked.connect(self.reset_analysis)   # 전체 분석 초기화(하이라이트·필터·Δ·열너비·행높이 → 기본값, 가역)
        self.last_custom_color = QColor(255, 255, 0)   # 커스텀 색 대화상자 초기값(최근 선택 기억)

        # ---------- Undo / Redo (Ctrl+Z / Ctrl+Y) ----------
        # 분석 편집(하이라이트·필터·Δ·열너비·행높이)을 CSV별 독립 스택(cache['history'])에 '액션 단위'로 기록.
        # 스냅샷은 .viewer 직렬화(export_*)를 재사용하고, 안 바뀐 슬라이스는 참조 공유(COW)한다.
        self._suppress_width_record = False    # 프로그램적 너비변경(모델부착·복원) 중엔 기록 안 함(아래 함정 참고)
        self.table_csv.horizontalHeader().sectionResized.connect(self._on_section_resized)
        # 열너비 드래그는 연속 신호(sectionResized) → 디바운스로 '드래그 1제스처 = 1 히스토리'로 묶는다.
        # (여러 열을 동시에 늘려도 신호가 여러 번 나지만 이 타이머가 모두 흡수 → 1단계)
        self._width_timer = QTimer(self)
        self._width_timer.setSingleShot(True)
        self._width_timer.setInterval(350)
        self._width_timer.timeout.connect(lambda: self.record_history({'widths'}))
        # 행높이도 동일 패턴(드래그 연속 신호 → 디바운스 1단계). 열너비와 대칭으로 Undo 슬라이스 'rows' 기록.
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.setInterval(350)
        self._height_timer.timeout.connect(lambda: self.record_history({'rows'}))

        # ---------- 엑셀형 다중선택 동시조정 (열너비 / 행높이) ----------
        # 다중 선택(완전 선택된 열 N개 또는 행 N개) 중 하나의 경계를 드래그하면, 손을 떼는 순간(release)에
        # 나머지 선택분이 같은 크기로 스냅된다. 드래그 중엔 잡은 하나만 실시간(=Qt 기본), 종료 시 일괄 적용.
        #  - 열: 기존 너비 저장/Undo 그대로(전파 후 위 _width_timer 가 피어 포함 1단계로 기록).
        #  - 행: 세션 한정(저장·Undo 없음) — verticalHeader 의 sectionResized 만 추적해 전파.
        # release 감지는 두 헤더의 viewport 에 설치한 eventFilter 가 담당(QHeaderView 엔 'resize 종료'
        # 신호가 없음). ⚠ QHeaderView 는 QAbstractScrollArea 라 마우스 이벤트는 헤더 객체가 아니라 그
        # viewport() 로 전달된다 → 반드시 viewport 에 필터를 걸어야 release 를 받는다(헤더에 걸면 안 옴).
        self._propagating = False      # 동시조정 전파 중 — 전파가 다시 쏘는 sectionResized 재귀/재기록 차단
        self._pending_h = None         # 드래그 중인 (열, 새너비). release 때 나머지 선택 열에 전파 후 None
        self._pending_v = None         # 드래그 중인 (행, 새높이). release 때 나머지 선택 행에 전파 후 None
        self._rows_dirty = False       # 행높이가 기본(20)에서 변경됐는지 — Memento 'rows' 캡처를 sentinel(None)로 건너뛰는 dirty 플래그
        self.table_csv.verticalHeader().sectionResized.connect(self._on_row_resized)
        self.table_csv.horizontalHeader().viewport().installEventFilter(self)
        self.table_csv.verticalHeader().viewport().installEventFilter(self)

        # Undo/Redo 버튼: 활성/비활성 전용 아이콘 4종을 미리 만들어 두고, _update_undo_buttons 가
        # 현재 CSV 히스토리(can_undo/can_redo)에 따라 enabled + 아이콘을 교체한다.
        # ⚠ 비활성 버튼은 Qt 가 아이콘을 Disabled 모드로 '한 번 더' 흐리게 렌더 → 전용 disable 이미지를
        #   그 모드에 직접 등록해 추가 페이드 없이 그림 그대로 보이게 한다(안 그러면 이중으로 흐려짐).
        def _disabled_icon(fn):
            pm = QPixmap(os.path.join(self.icon_path, fn))
            ic = QIcon()
            ic.addPixmap(pm, QIcon.Mode.Disabled)
            ic.addPixmap(pm, QIcon.Mode.Normal)
            return ic
        self._icon_undo = QIcon(os.path.join(self.icon_path, "button_undo.png"))
        self._icon_redo = QIcon(os.path.join(self.icon_path, "button_redo.png"))
        self._icon_undo_off = _disabled_icon("button_undo_disable.png")
        self._icon_redo_off = _disabled_icon("button_redo_disable.png")
        self.button_undo.clicked.connect(self.undo)
        self.button_redo.clicked.connect(self.redo)
        self._update_undo_buttons()        # 초기 상태(모델 없음 → 비활성 + disable 아이콘) 반영


    def _apply_highlight(self, color):
        # color: QColor 적용 / None 이면 전체 해제
        entry = self.cache.get(self.csv_file_name)
        proxy_model = entry['table_model'] if entry else None
        if not proxy_model:
            return
        source_model = proxy_model.sourceModel()
        # 선택 셀을 실제 셀(소스)과 Δ 셀로 분리. Δ 셀은 소스가 없어 프록시에 (기준열, 소스행)으로 저장.
        accepted = proxy_model.accepted_rows()
        source_indexes = []
        delta_targets = []
        for pi in self.table_csv.selectedIndexes():
            if proxy_model.is_delta_column(pi.column()):
                delta_targets.append((proxy_model.source_column_of(pi.column()), accepted[pi.row()]))
            else:
                si = proxy_model.mapToSource(pi)
                if si.isValid():
                    source_indexes.append(si)
        if not source_indexes and not delta_targets:
            return                              # 색칠 대상 없음(빈 선택) → 기록 안 함
        source_model.highlight_cell(color, source_indexes)
        proxy_model.set_delta_cell_colors(color, delta_targets)
        self.table_csv.clearSelection()
        self.record_history({'highlights', 'fd'})   # 여러 셀이라도 1단계(루프 밖, 꼬리 1회)

    def highlight_cell(self, event=None):
        # 프리셋 버튼 -> objectName 으로 색 결정
        self._apply_highlight(self.highlight_colors.get(self.sender().objectName()))

    def pick_custom_color(self):
        # 그림판식 색상 선택 (팔레트 + RGB/HSV/Hex + 사용자 정의 색)
        color = QColorDialog.getColor(self.last_custom_color, self, "Select Color")
        if color.isValid():
            self.last_custom_color = color
            self._apply_highlight(color)

    def reset_analysis(self):
        # button_reset: 현재 CSV의 모든 분석을 초기값으로 — 하이라이트·Δ색·열필터·Δ열·열너비(80)·행높이(20).
        # .viewer 자동복원/Undo 와 동일 경로(_restore_memento)로 raw 적용 후 record_history → Undo 1단계로 가역.
        entry = self._current_edit_entry()
        if entry is None:
            return
        self._flush_size_debounce()      # 보류 중 크기 드래그를 자기 단계로 먼저 확정 → reset 은 깨끗이 1단계
        proxy = entry['table_model']
        source = proxy.sourceModel()
        hdr = self.table_csv.horizontalHeader()
        src_cols = source.columnCount()
        # 이미 raw(분석 0)면 빈 단계 기록 방지 — no-op. 행높이는 dirty 플래그로 싸게 판정.
        has_analysis = (
            bool(source.highlight_cells)
            or proxy.has_delta_colors()
            or bool(proxy.column_filters)
            or proxy.columnCount() != src_cols                       # Δ 가상열 존재
            or any(hdr.sectionSize(c) != 80 for c in range(hdr.count()))
            or self._rows_dirty                                      # 행높이가 기본(20)에서 바뀜
        )
        if not has_analysis:
            return
        self.table_csv.clearSelection()
        self._restore_memento(entry, Memento(highlights={}, fd={}, widths=[80] * src_cols, rows=None))
        self.record_history({'highlights', 'fd', 'widths', 'rows'})   # 전 슬라이스 1단계 = 가역

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
            if time.time() - self.last_esc_time < self.ESC_INTERVAL_SEC:  # ESC 연타 간격(초)
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

        # 'Ctrl+S' Key Pressed -> 현재 CSV 분석상태를 폴더 .viewer 에 저장
        elif event.key() == Qt.Key.Key_S and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.save_view_state()

        # 'Ctrl+Z' Key Pressed -> Undo (분석 편집 되돌리기)
        elif event.key() == Qt.Key.Key_Z and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.undo()

        # 'Ctrl+Y' or 'Ctrl+Shift+Z' Key Pressed -> Redo (다시 실행)
        elif (event.key() == Qt.Key.Key_Y and event.modifiers() == Qt.KeyboardModifier.ControlModifier) \
             or (event.key() == Qt.Key.Key_Z
                 and event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
            self.redo()

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

    def _style_esc_toast(self):
        # 투박한 회색 박스 → 둥근 알약 + 위→아래 옅은 그라데이션 + 가장자리로 번지는 부드러운 그림자.
        # (GUI/ui/widget_esc.py 는 자동생성이라 건드리지 않고 여기서 다시 입힌다.)
        label = self.ui_esc.label_esc
        # 첫 ESC는 안내만 띄우고 1초 내 다시 누르면 닫힘 → "다시 누르면 나간다"는 뜻을 간결히 전달
        label.setText("Press ESC again to exit")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # 알약 폭은 문구 길이에 맞춰 자동 산정 (문구가 바뀌어도 잘리지 않게)
        PAD_H, PILL_H, MARGIN = 28, 64, 34     # PAD_H=좌우 안쪽 여백, MARGIN=그림자 번질 여백
        PILL_W = label.fontMetrics().horizontalAdvance(label.text()) + PAD_H * 2
        label.setFixedSize(PILL_W, PILL_H)
        label.move(MARGIN, MARGIN)
        label.setStyleSheet(
            "QLabel {"
            "  color: rgba(248, 250, 252, 235);"
            "  background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            "      stop:0 rgba(118, 137, 176, 200), stop:1 rgba(82, 95, 122, 200));"
            "  border-radius: 18px;"
            "}"
        )
        # 가장자리로 갈수록 옅어지는 그림자(글로우) → 박스가 화면 위에 부드럽게 떠 보이게
        shadow = QGraphicsDropShadowEffect(self.widget_esc)
        shadow.setBlurRadius(26)
        shadow.setColor(QColor(20, 25, 40, 110))
        shadow.setOffset(0, 6)
        label.setGraphicsEffect(shadow)
        # 그림자 여백까지 포함한 크기 (show_esc_message 가 이 크기로 창 가운데 정렬)
        self.widget_esc.resize(PILL_W + 2 * MARGIN, PILL_H + 2 * MARGIN)

    def show_esc_message(self):
        pos_x = (self.width() - self.widget_esc.width()) // 2
        pos_y = (self.height() - self.widget_esc.height()) // 2
        self.widget_esc.setGeometry(pos_x, pos_y, self.widget_esc.width(), self.widget_esc.height(),)
        self.widget_esc.show()
        QTimer.singleShot(int(self.ESC_INTERVAL_SEC * 1000), self.widget_esc.hide)  # 토스트 표시 시간 = 연타 유효 시간

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

    def _stat_sig(self, csv_file_name):
        # 파일 메타데이터 지문 (size, mtime_ns). 재진입 신선도 1차 게이트(≈0.007ms). 접근 불가 시 None.
        try:
            s = os.stat(self._file_path(csv_file_name))
        except OSError:
            return None
        return (s.st_size, s.st_mtime_ns)

    def _content_hash(self, csv_file_name):
        # 파일 내용 해시(sha256). size 같고 mtime만 다른 드문 경우의 타이브레이커로만 호출.
        h = hashlib.sha256()
        try:
            with open(self._file_path(csv_file_name), 'rb') as f:
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    h.update(chunk)
        except OSError:
            return None
        return h.hexdigest()

    def _cache_is_fresh(self, csv_file_name):
        # 캐시된 CSV가 디스크 파일과 같은지 판정 (다른 CSV 보다 돌아올 때마다 호출).
        #  1) stat(size+mtime_ns) 같으면 신선 — 해시 안 함 (절대다수 경로, ≈7µs, 파일 크기 무관).
        #  2) size 다르면 내용도 무조건 다름 → 해시 생략하고 폐기.
        #  3) size 같고 mtime만 다르면 그때만 내용 해시 비교: 같으면(touch/동일 재저장) 캐시 유지+sig 갱신, 다르면 폐기.
        entry = self.cache[csv_file_name]
        old_sig = entry.get('signature')
        new_sig = self._stat_sig(csv_file_name)
        if old_sig is None or new_sig is None:
            return False                        # 기준 없음 또는 파일 접근 불가 → 안전하게 재로드
        if new_sig == old_sig:
            return True                         # 메타데이터 동일 → 신선
        if new_sig[0] != old_sig[0]:
            return False                        # size 다름 → 내용 변경 확정 → 폐기
        old_hash = entry.get('content_hash')
        new_hash = self._content_hash(csv_file_name)
        if old_hash is not None and new_hash is not None and new_hash == old_hash:
            entry['signature'] = new_sig        # 내용 동일(메타만 변경) → sig 갱신해 다음 재진입은 빠른 경로로
            return True
        return False

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
        self._load_view_states()                                # 새 폴더의 .viewer 로드
        self.table_csv.setModel(None)
        self._update_undo_buttons()                             # 폴더 변경 → 히스토리 없음(버튼 비활성)
        self.load_csv_list()
        self.list_csv_names.scrollToTop()                       # 새 폴더 목록은 항상 맨 위부터
        self.list_csv_names.horizontalScrollBar().setValue(0)   # 가로 스크롤도 초기화
        self.edit_csvname_find.clear()                          # 새 폴더 -> 이름 검색 초기화

    def _close_ui_overlays(self):
        # CSV 전환/폴더변경 직전: 디바운스 대기 중인 너비변경이 있으면 '아직 현재인' 이 CSV 에 먼저 확정
        # (record_history 는 _current_edit_entry=현재 CSV 기준 → 전환 전이라 올바른 CSV 에 기록).
        self._flush_size_debounce()      # 보류 중 너비/행높이 디바운스를 '아직 현재인' 이 CSV 에 먼저 확정
        self.search_gui_hide()
        self._hide_message()
        header = self.table_csv.horizontalHeader()
        if hasattr(header, "filter_popup") and header.filter_popup:
            header.filter_popup.close()

    def _ensure_cache(self, csv_file_name):
        if csv_file_name not in self.cache:
            self.cache[csv_file_name] = {'table_model': None, 'table_data': None, 'last_view': None, 'col_widths': None,
                                         'signature': None, 'content_hash': None, 'status': None}
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
        self._update_undo_buttons()     # 로딩 중엔 모델 없음 → undo/redo 비활성(완료 시 update_table 이 재반영)
        self._start_spinner()
        self.paint_list_csv(csv_file_name, (255, 255, 225))  # Yellow
        thread = CSVLoaderThread(self._file_path(csv_file_name))
        # 로더는 전체 경로로 읽고, 콜백엔 파일명(식별자) + 캐시 무효화용 지문(signature/content_hash)을 넘긴다.
        # t.signature/t.content_hash 는 run() 종료(emit) 시점에 세팅돼 있으므로 큐드 슬롯에서 읽어도 안전.
        thread.load_complete.connect(lambda path, data, n=csv_file_name, t=thread: self.csv_load_complete(n, data, t.signature, t.content_hash))
        thread.load_failed.connect(lambda path, n=csv_file_name, t=thread: self.csv_load_failed(n, t.signature))
        thread.load_empty.connect(lambda path, n=csv_file_name, t=thread: self.csv_load_empty(n, t.signature))
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
            # 열 너비도 per-CSV로 저장 (last_view 와 동일 범주의 뷰 상태). O(열 수)라 행 수 무관.
            hdr = self.table_csv.horizontalHeader()
            prev['col_widths'] = [hdr.sectionSize(c) for c in range(hdr.count())]

        self._close_ui_overlays()

        current = self.list_csv_names.currentItem()
        if current is None:
            return
        self.csv_file_name = current.text()

        if self.csv_file_name in self.cache:
            if self._cache_is_fresh(self.csv_file_name):
                self.update_table(self.csv_file_name)
            else:
                # 디스크 파일이 캐시된 내용과 달라짐 → 그 CSV의 캐시(필터/하이라이트/Δ/스크롤 등) 전부 폐기 후 새로 로드
                del self.cache[self.csv_file_name]
                self._start_loading(self.csv_file_name)
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
            self._update_undo_buttons()         # 보던 CSV 삭제 → 모델 없음(버튼 비활성)

        # 검색칸에 입력이 있으면 갱신된 목록에도 동일 필터 재적용
        self._filter_csv_list(self.edit_csvname_find.text())

    def reload_current_csv(self):
        # F5(표 포커스): 현재 CSV 캐시(히스토리 포함)를 비우고 디스크에서 재로드 → 로드 후 .viewer 저장 분석 자동복원.
        # 캐시 폐기 후 새 baseline 으로 시작하므로 F5 자체는 Undo 비대상(비가역). raw 로 가려면 button_reset(초기화) 사용.
        if not self.csv_file_name or self.csv_file_name not in self.cache:
            return

        self._close_ui_overlays()
        del self.cache[self.csv_file_name]

        self.table_csv.setModel(None)
        self._start_loading(self.csv_file_name)

    def csv_load_complete(self, csv_file_name, data, signature=None, content_hash=None):
        # Save in cache (signature/content_hash = 재진입 시 변경 감지용 지문)
        entry = self._ensure_cache(csv_file_name)
        entry['table_data'] = data
        entry['signature'] = signature
        entry['content_hash'] = content_hash
        entry['status'] = 'ok'
        self.update_table(csv_file_name)
        self.paint_list_csv(csv_file_name, (230, 255, 230))  # Green

    def csv_load_empty(self, csv_file_name, signature=None):
        # 디코딩은 됐지만 데이터 행이 없음 -> No Data
        entry = self._ensure_cache(csv_file_name)
        entry['table_data'] = None
        entry['signature'] = signature          # 빈 파일도 sig 기록 → 안 바뀌면 재진입 시 불필요한 재로드 방지
        entry['content_hash'] = None
        entry['status'] = 'empty'
        self.update_table(csv_file_name)
        self.paint_list_csv(csv_file_name, (220, 220, 220))  # Gray

    def csv_load_failed(self, csv_file_name, signature=None):
        # 파일이 사라져서 실패한 경우 -> 목록을 동기화해 없어진 항목 정리 (디코딩 실패는 기존대로 표시)
        if not os.path.isfile(self._file_path(csv_file_name)):
            self.reload_csv_list()
            return
        # Save in cache (디코딩 실패: sig 기록 → 파일이 바뀌면 재진입 시 재시도, 그대로면 재시도 안 함)
        entry = self._ensure_cache(csv_file_name)
        entry['table_data'] = None
        entry['signature'] = signature
        entry['content_hash'] = None
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
            self._update_undo_buttons()     # 모델 없음 → undo/redo 비활성
            return

        # 모델 부착·기본너비·너비복원 동안 발생하는 sectionResized 는 '프로그램적'이라 undo 기록 대상 아님
        self._suppress_width_record = True

        # Look if there is model already created
        if entry['table_model']:
            self.table_csv.setModel(entry['table_model'])
        else:
            model = CSVTableModel(entry['table_data'], csv_file_name)
            model.load_fail.connect(self.csv_load_failed)
            proxy_model = CSVFilterProxyModel()
            proxy_model.setSourceModel(model)
            self._apply_saved_state(csv_file_name, entry, proxy_model, model)   # .viewer 자동 복원(내용 해시 일치 시)
            entry['table_model'] = proxy_model
            self.table_csv.setModel(proxy_model)

        self._wire_selection_signals()      # 새 selectionModel 에 Δ 비교 테두리 핸들러 연결 + 이전 마크 정리

        self.table_csv.horizontalHeader().setDefaultSectionSize(80)     # cell width
        self.table_csv.verticalHeader().setDefaultSectionSize(20)       # cell height
        self._rows_dirty = False        # 모델 부착 시 행높이는 기본(20)으로 리셋됨 → dirty 해제(baseline=기본)

        # Set Column Widths (per-CSV) - 저장된 너비가 있으면 복원.
        # ⚠ 가로 스크롤(last_view)보다 먼저 적용해야 너비가 바꾼 스크롤 범위에 값이 클램프되지 않는다.
        # ⚠ 길이 가드 = Δ 열 안전장치(열 수 불일치 시 기본값 80 유지). 저장값 없으면(첫 열람) 기본값 그대로.
        hdr = self.table_csv.horizontalHeader()
        col_widths = entry['col_widths']
        if col_widths and len(col_widths) == hdr.count():
            for c, w in enumerate(col_widths):
                hdr.resizeSection(c, w)

        self._width_timer.stop()                 # 셋업 중 시작됐을 수 있는 디바운스 취소
        self._height_timer.stop()                # 행높이 디바운스도 동일
        self._suppress_width_record = False      # 여기부터의 너비/높이 변경(=사용자 드래그)만 기록 대상
        self._ensure_baseline_history(entry)     # 모델 최초 표시 직후 baseline 1개 생성(이미 있으면 보존)

        # Set Last View (ScrollBar)
        last_view = entry['last_view']
        if last_view:
            self.table_csv.verticalScrollBar().setValue(last_view[0])
            self.table_csv.horizontalScrollBar().setValue(last_view[1])
        else:
            self.table_csv.scrollTo(self.table_csv.model().index(0, 0), QAbstractItemView.ScrollHint.PositionAtTop)

        self._update_undo_buttons()         # CSV 표시 직후: 이 CSV 히스토리 기준으로 버튼 상태 반영

    def _wire_selection_signals(self):
        # selectionModel 은 setModel 마다 새로 생긴다 → 그때 Δ 비교 테두리 핸들러를 (재)연결.
        # 모델 교체 시 이전 테두리 마크는 비우고, 같은 selectionModel 엔 중복 연결하지 않는다.
        self.border_delegate.set_marks(None, None)
        self.border_delegate.set_search_mark(None)    # CSV 전환(모델 교체) 시 검색 테두리도 초기화
        sm = self.table_csv.selectionModel()
        if sm is None or sm is self._wired_sel_model:
            return
        sm.currentChanged.connect(self._on_delta_selection)
        sm.selectionChanged.connect(self._on_delta_selection)
        self._wired_sel_model = sm

    def _on_delta_selection(self, *args):
        # 현재 셀이 'Δ 데이터 셀'이면 비교한 두 부모셀(현재=파랑/이전=빨강)에 테두리, 아니면 해제.
        proxy = self.table_csv.model()
        sm = self.table_csv.selectionModel()
        blue = red = None
        if proxy is not None and sm is not None and sm.hasSelection() and hasattr(proxy, "delta_compare_cells"):
            cur = sm.currentIndex()
            if cur.isValid():
                info = proxy.delta_compare_cells(cur.column(), cur.row())
                if info is not None:
                    base_pcol, cur_prow, prev_prow = info
                    blue = (cur_prow, base_pcol)
                    red = (prev_prow, base_pcol) if prev_prow is not None else None
        if self.border_delegate.set_marks(blue, red):
            self.table_csv.viewport().update()

    def copy_selection(self):
        table = self.table_csv
        model = table.model()
        sel = table.selectionModel()
        if model is None or sel is None:
            return

        # ⚠ 대용량(18만 행) 함정 회피: selectedIndexes()/selectedColumns()/selectedRows()는
        #   내부적으로 전 행을 순회해 수 초 걸린다(전 열/행 복사 시 멈춘 듯 보임). 선택 '범위'
        #   (QItemSelectionRange)는 항상 소수이므로 범위만 보고, 셀 값은 소스 rows / Δ 스냅샷을
        #   '직접' 읽어 per-cell data() 호출(18만+ 회)도 피한다.
        ranges = [(r.top(), r.bottom(), r.left(), r.right()) for r in sel.selection()]
        if not ranges:
            return
        row_count, col_count = model.rowCount(), model.columnCount()

        cols = sorted({c for (_t, _b, l, r) in ranges for c in range(l, r + 1)})

        # 헤더행 포함(열 전체 선택) / 행번호열 포함(행 전체 선택) 여부 - 느린 selected*() 대신 구간 커버리지
        full_cols = any(self._spans_cover([(t, b) for (t, b, l, r) in ranges if l <= c <= r], row_count)
                        for c in cols)
        full_rows = False
        bounds = sorted({x for (t, b, _l, _r) in ranges for x in (t, b + 1)})
        for i in range(len(bounds) - 1):
            a, nxt = bounds[i], bounds[i + 1]
            col_spans = [(l, r) for (t, b, l, r) in ranges if t <= a and b >= nxt - 1]
            if col_spans and self._spans_cover(col_spans, col_count):
                full_rows = True
                break

        # 열별 선택 행 구간(병합) → (row,col) 선택 여부를 bisect로 빠르게 (분리/희소 선택 대비)
        col_span = {}
        for c in cols:
            merged = []
            for t, b in sorted((t, b) for (t, b, l, r) in ranges if l <= c <= r):
                if merged and t <= merged[-1][1] + 1:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], b))
                else:
                    merged.append((t, b))
            col_span[c] = ([m[0] for m in merged], [m[1] for m in merged])

        def is_sel(r, c):
            starts, ends = col_span[c]
            i = bisect_right(starts, r) - 1
            return i >= 0 and r <= ends[i]

        rows_set = set()
        for (t, b, _l, _r) in ranges:
            rows_set.update(range(t, b + 1))
        rows = sorted(rows_set)

        # 빠른 데이터 접근(프록시면): 소스 rows + Δ 스냅샷 직접
        src_cols = model.source_columns() if hasattr(model, "source_columns") else None
        accepted = model.accepted_rows() if hasattr(model, "accepted_rows") else None
        source = model.sourceModel() or model
        srows = getattr(source, "rows", None)
        delta_snap = {}
        if src_cols is not None and hasattr(model, "delta_snapshot"):
            for c in cols:
                if src_cols[c] < 0:                        # Δ 열
                    delta_snap[c] = model.delta_snapshot(model.source_column_of(c))
        fast = srows is not None and accepted is not None and src_cols is not None

        H, V, DISP = Qt.Orientation.Horizontal, Qt.Orientation.Vertical, Qt.ItemDataRole.DisplayRole

        lines = []
        if full_cols:
            head = [''] if full_rows else []               # 둘 다면 좌상단 코너는 빈칸
            # 헤더는 프록시 열→소스 열 매핑이 반영된 '⧩ 없는' 라벨(Δ 열은 Δ[원본헤더])로
            if hasattr(model, "column_label"):
                head += [str(model.column_label(c) or '') for c in cols]
            else:
                head += [str(source.headerData(c, H, DISP) or '') for c in cols]
            lines.append('\t'.join(head))

        for r in rows:
            line = [str(model.headerData(r, V, DISP) or '')] if full_rows else []
            if fast:
                sr = accepted[r]
                srow = srows[sr]
                for c in cols:
                    if not is_sel(r, c):
                        line.append('')
                    elif src_cols[c] < 0:                  # Δ 열 → 스냅샷 값
                        line.append(delta_snap.get(c, {}).get(sr, ''))
                    else:
                        line.append(srow[src_cols[c]])
            else:
                for c in cols:
                    line.append(str(model.index(r, c).data() or '') if is_sel(r, c) else '')
            lines.append('\t'.join(line))

        QApplication.clipboard().setText('\n'.join(lines))

    @staticmethod
    def _spans_cover(spans, total):
        # 구간 [(lo,hi)]들이 [0, total-1]을 빈틈없이 덮는가 (search_model._spans_cover 와 동일 로직).
        if total <= 0:
            return False
        nxt = 0
        for lo, hi in sorted(spans):
            if lo > nxt:
                return False
            if hi >= nxt:
                nxt = hi + 1
            if nxt >= total:
                return True
        return nxt >= total

    def search_gui_show(self):
        self.frame_search.setVisible(True)
        self.edit_text_input.setFocus()

    def search_gui_hide(self):
        self.frame_search.setVisible(False)
        # 검색바를 닫으면 범위 해제 + placeholder 원복 + 검색 테두리 제거
        self.search_model.reset_scope()
        self.edit_text_input.setPlaceholderText("Search whole table (Enter)")
        if self.border_delegate.set_search_mark(None):
            self.table_csv.viewport().update()

    def search_gui_init(self):
        # 검색바를 열 때 현재 선택(열/행 전체)을 범위로 캡처하고, 그에 맞춰 placeholder 표시
        self.search_model.capture_scope()
        if self.search_model.scope_active:
            self.edit_text_input.setPlaceholderText("Search in selected (Enter)")
        else:
            self.edit_text_input.setPlaceholderText("Search whole table (Enter)")
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
        self._update_search_mark()      # 검색 현재 셀 회색 테두리 갱신(매치 없으면 해제)

    def _update_search_mark(self):
        # 검색 현재 매치 셀에 회색 테두리. 헤더 매치(행 -1)는 셀 테두리 대상이 아니라 None.
        sm = self.search_model
        cell = None
        if sm.matches and 0 <= sm.current_index < len(sm.matches):
            row, col = sm.matches[sm.current_index]
            if row >= 0:
                cell = (row, col)
        if self.border_delegate.set_search_mark(cell):
            self.table_csv.viewport().update()

    # ---------- 분석상태 영속화(.viewer): Ctrl+S 저장 + 재진입 자동복원 ----------
    def _load_view_states(self):
        # 폴더의 .viewer 를 1회 읽어 메모리에 보관. 각 CSV 최초 모델 생성 시 hash 일치하면 적용.
        self.saved_states = view_state.load_folder_states(self._folder()) if self.csv_folder_name else {}

    def save_view_state(self):
        # 현재 보고 있는 CSV의 분석상태(하이라이트·필터·Δ·열너비·스크롤)를 폴더 .viewer 에 저장.
        # 'ok' 로 로드된 CSV만(빈/실패/미로드는 저장할 분석이 없음). 현재 CSV 한 개만 기록(다른 CSV 저장본 보존).
        name = self.csv_file_name
        entry = self.cache.get(name) if name else None
        if not entry or entry.get('status') != 'ok' or not entry.get('table_model'):
            return
        proxy = entry['table_model']
        source = proxy.sourceModel()
        if source is None:
            return
        hdr = self.table_csv.horizontalHeader()
        sig = entry.get('signature')
        # 라이브 캡처: col_widths/scroll 은 cache가 'CSV 전환 시'에만 갱신되므로 저장 시점엔 뷰에서 직접 읽는다.
        file_state = {
            'csv_sha256': entry.get('content_hash'),
            'csv_size':   sig[0] if sig else None,
            'highlights': source.export_highlights(),
            'col_widths': [hdr.sectionSize(c) for c in range(hdr.count())],
            'scroll':     [self.table_csv.verticalScrollBar().value(),
                           self.table_csv.horizontalScrollBar().value()],
        }
        file_state.update(proxy.export_state())     # column_filters, deltas
        if view_state.save_file_state(self._folder(), name, file_state):
            self.saved_states[name] = file_state     # 인메모리도 동기화(F5 재로드 시 일관)
            self._show_toast("Result saved! (.viewer)", success=True)
        else:
            self._show_toast("Save failed! Permission denied", success=False)

    def _apply_saved_state(self, name, entry, proxy, source):
        # 저장된 .viewer 상태가 '현재 파일 내용 해시'와 일치할 때만 복원(모델 생성 직후·뷰 부착 전).
        # 순서: 필터·Δ 먼저 → 그 다음 하이라이트 → (이어서 update_table 이 col_widths → 스크롤 복원).
        saved = self.saved_states.get(name)
        if not saved or saved.get('csv_sha256') != entry.get('content_hash'):
            return
        try:
            proxy.restore_state(saved)                              # 열 값 필터 + Δ(Option2 재현)
            source.restore_highlights(saved.get('highlights', {}))  # 셀 하이라이트(소스 좌표)
            if saved.get('col_widths'):
                entry['col_widths'] = list(saved['col_widths'])     # update_table 꼬리의 너비 복원이 사용
            if saved.get('scroll'):
                entry['last_view'] = tuple(saved['scroll'])         # update_table 꼬리의 스크롤 복원이 사용
        except Exception as e:
            # 저장본이 손상/구버전이어도 CSV 열람은 절대 막지 않음(부분 복원이라도 진행)
            print(f"[ViewState] restore failed for '{name}': {e}")

    # ---------- Undo / Redo (Ctrl+Z / Ctrl+Y) — CSV별 독립 스택 ----------
    def _current_edit_entry(self):
        # 기록/되돌리기가 가능한 '현재 CSV' cache 엔트리(ok 로드 + 모델). 아니면 None.
        name = self.csv_file_name
        entry = self.cache.get(name) if name else None
        if not entry or entry.get('status') != 'ok' or not entry.get('table_model'):
            return None
        return entry

    def _capture_slice(self, entry, slice_name):
        # 한 슬라이스만 라이브 모델/뷰에서 새로 추출 (.viewer 직렬화 형식 그대로 재사용).
        proxy = entry['table_model']
        if slice_name == 'highlights':
            return proxy.sourceModel().export_highlights()
        if slice_name == 'fd':
            return proxy.export_state()
        if slice_name == 'widths':
            hdr = self.table_csv.horizontalHeader()
            return [hdr.sectionSize(c) for c in range(hdr.count())]
        # 'rows' — 행높이. 전부 기본(20)이면 sentinel(None)로 비용 0; 변경됐을 때만 보이는 행 전체 캡처.
        if not self._rows_dirty:
            return None
        vhdr = self.table_csv.verticalHeader()
        return [vhdr.sectionSize(r) for r in range(vhdr.count())]

    def _make_memento(self, entry, changed, prev):
        # COW: changed 에 든 슬라이스만 새로 추출, 나머지는 prev(직전 Memento)의 객체를 참조 그대로 재사용.
        #      prev=None(baseline)이면 셋 다 새로 추출. (Memento 의 값은 불변 취급이라 참조 공유가 안전)
        def take(name):
            if prev is None or name in changed:
                return self._capture_slice(entry, name)
            return getattr(prev, name)
        return Memento(take('highlights'), take('fd'), take('widths'), take('rows'))

    def record_history(self, changed):
        # 사용자 액션 '꼬리'에서 1회만 호출(셀/열 루프 안 금지 → 일괄 변경도 1단계). changed=바뀐 슬라이스 집합.
        entry = self._current_edit_entry()
        if entry is None:
            return
        hist = entry.get('history')
        if hist is None:
            return                          # baseline 아직 없음(모델 표시 전) → 기록 스킵
        if 'widths' in changed:
            self._width_timer.stop()        # 명시적 너비 기록 시 보류 중 디바운스는 흡수(중복 단계 방지)
        if 'rows' in changed:
            self._height_timer.stop()       # 행높이도 동일(보류 디바운스 흡수 → 중복 단계 방지)
        hist.push(self._make_memento(entry, changed, hist.current()))
        self._update_undo_buttons()         # 새 액션 → undo 가능 + redo 가지 폐기(redo 버튼 비활성)

    def _flush_size_debounce(self):
        # 보류 중 너비/행높이 디바운스를 즉시 1단계로 확정(직전 드래그를 자기 단계로 — CSV 전환·reset 전에 호출).
        if self._width_timer.isActive():
            self._width_timer.stop()
            self.record_history({'widths'})
        if self._height_timer.isActive():
            self._height_timer.stop()
            self.record_history({'rows'})

    def _ensure_baseline_history(self, entry):
        # 모델 최초 표시 직후(너비/스크롤 복원까지 끝난 시점) baseline 1개로 히스토리 생성.
        # 이미 있으면(캐시 재사용 경로) 건드리지 않아 세션 편집을 보존한다.
        if entry.get('history') is None:
            entry['history'] = EditHistory(self._make_memento(entry, None, None), cap=self.MAX_UNDO_STEPS)

    def undo(self):
        entry = self._current_edit_entry()
        hist = entry.get('history') if entry else None
        m = hist.undo() if hist else None
        if m is not None:
            self._restore_memento(entry, m)
        self._update_undo_buttons()

    def redo(self):
        entry = self._current_edit_entry()
        hist = entry.get('history') if entry else None
        m = hist.redo() if hist else None
        if m is not None:
            self._restore_memento(entry, m)
        self._update_undo_buttons()

    def _restore_memento(self, entry, m):
        # .viewer 자동복원과 동일 경로: highlights(소스) → state(프록시 reset) → 너비. 그 후 마크/검색 정리.
        proxy = entry['table_model']
        source = proxy.sourceModel()
        if source is None:
            return
        self._suppress_width_record = True       # 아래 resizeSection 이 sectionResized 를 다시 emit → 재귀 기록 방지
        try:
            source.restore_highlights(m.highlights)   # dict 교체(emit 없음)
            proxy.restore_state(m.fd)                  # reset → 새 하이라이트·열·필터로 보이는 셀 전체 리페인트
            hdr = self.table_csv.horizontalHeader()
            if m.widths and len(m.widths) == hdr.count():
                for c, w in enumerate(m.widths):
                    hdr.resizeSection(c, w)
            self._restore_row_heights(m.rows)          # 행높이 복원(열너비와 대칭, 같은 suppress 가드 안)
        finally:
            self._suppress_width_record = False
        # Δ/검색 비교 테두리는 좌표가 어긋날 수 있어 초기화
        self.border_delegate.set_marks(None, None)
        self.border_delegate.set_search_mark(None)
        self.table_csv.viewport().update()
        # 행 집합이 바뀌었을 수 있으니 검색바가 열려 있으면 재검색(apply_filter 와 동일 패턴)
        if self.frame_search.isVisible():
            self.search_model.search(self.edit_text_input.text())

    def _restore_row_heights(self, rows):
        # Memento 'rows' 복원(열너비와 대칭). rows=None → 전부 기본 20, 리스트 → 위치별 적용.
        # ⚠ 반드시 _suppress_width_record 가드 안에서 호출(아래 resizeSection 이 _on_row_resized 를 재발동).
        # ⚠ restore_state(필터) 뒤에 호출해야 보이는 행 수가 캡처 시점과 일치(positional 복원).
        vhdr = self.table_csv.verticalHeader()
        n = vhdr.count()
        if rows is None:
            if self._rows_dirty:                          # 현재 오버라이드가 있을 때만 청소(대용량 무필요 스캔 회피)
                for r in range(n):
                    if vhdr.sectionSize(r) != 20:
                        vhdr.resizeSection(r, 20)
                self._rows_dirty = False
            return
        if len(rows) == n:                                # 길이 불일치(필터 어긋남)면 미적용(안전장치)
            for r, h in enumerate(rows):
                if vhdr.sectionSize(r) != h:
                    vhdr.resizeSection(r, h)
            self._rows_dirty = True

    def _on_section_resized(self, idx, _old, new):
        # 사용자 열너비 드래그 → 디바운스 후 1회 기록. 프로그램적 변경(모델부착·복원)은 억제 플래그로,
        # 동시조정 전파로 인한 재귀 신호는 _propagating 으로 무시한다.
        if self._suppress_width_record or self._propagating:
            return
        if self._current_edit_entry() is None:
            return
        self._pending_h = (idx, new)     # release 때 나머지 선택 열에 전파할 대상/크기
        self._width_timer.start()

    def _on_row_resized(self, idx, _old, new):
        # 사용자 행높이 드래그 → release 때 나머지 선택 행에 전파 + 디바운스 후 1회 'rows' 기록(열너비와 대칭).
        if self._suppress_width_record or self._propagating:
            return
        if self._current_edit_entry() is None:
            return
        self._pending_v = (idx, new)     # release 때 나머지 선택 행에 전파할 대상/크기
        self._rows_dirty = True          # 행높이가 기본에서 바뀜 → 다음 Memento 는 'rows' 를 실제 리스트로 캡처
        self._height_timer.start()

    def eventFilter(self, obj, event):
        # 두 헤더 viewport 의 마우스 release 를 잡아 '드래그 종료 시 일괄 적용'을 트리거(QHeaderView 엔
        # 종료 신호 없음). ⚠ 마우스 이벤트는 헤더가 아니라 viewport 로 오므로 obj 도 viewport 와 비교한다.
        # singleShot(0) 으로 헤더 자체 release 처리 직후 실행(=최종 크기 확정 후). _pending_* 가 없으면 no-op
        # 라 단순 헤더 클릭/우클릭(우클릭=필터 팝업)엔 영향 없다.
        if (event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton):
            if obj is self.table_csv.horizontalHeader().viewport():
                QTimer.singleShot(0, lambda: self._finalize_resize(True))
            elif obj is self.table_csv.verticalHeader().viewport():
                QTimer.singleShot(0, lambda: self._finalize_resize(False))
        return super().eventFilter(obj, event)

    def _finalize_resize(self, horizontal):
        # 드래그 종료: 잡았던 섹션이 '다중 완전선택'의 일원이면 나머지를 같은 크기로 맞춘다(엑셀 동작).
        # 열은 전파 후 _width_timer 가 잠시 뒤 1단계로 기록(피어 포함), 행은 세션 한정이라 기록/저장 없음.
        if horizontal:
            pending, self._pending_h = self._pending_h, None
        else:
            pending, self._pending_v = self._pending_v, None
        if pending is None:
            return
        idx, new = pending
        sections = self._full_selection_sections(horizontal)
        if len(sections) < 2 or idx not in sections:
            return                       # 단일/비선택 섹션 드래그 → 전파 없음(엑셀 동일)
        hdr = self.table_csv.horizontalHeader() if horizontal else self.table_csv.verticalHeader()
        self._propagating = True         # 아래 resizeSection 이 쏘는 sectionResized 는 위 핸들러에서 무시됨
        hdr.setUpdatesEnabled(False)     # 대량(행 전체선택=수만) 전파 시 매 섹션 repaint thrash 차단 → 끝나고 1회 갱신
        try:
            for s in sections:
                if s != idx:
                    hdr.resizeSection(s, new)
        finally:
            hdr.setUpdatesEnabled(True)
            self._propagating = False

    def _full_selection_sections(self, horizontal):
        # 선택에서 '완전히 선택된' 열(horizontal=True) 또는 행 집합을 selection 범위 분석으로 구한다.
        # ⚠ selectedColumns()/selectedRows()는 18만 행 열/행 전체선택 시 수 초 → 금지. range 직접 분석으로
        #   판정한다(search_model.capture_scope·copy_selection 과 동일 철학, _spans_cover 재사용 → O(range)).
        sel = self.table_csv.selectionModel()
        model = self.table_csv.model()
        if sel is None or model is None:
            return set()
        row_count, col_count = model.rowCount(), model.columnCount()
        ranges = [(r.top(), r.bottom(), r.left(), r.right()) for r in sel.selection()]
        if horizontal:
            # 열 전체 선택: 그 열을 덮는 (쪼개졌을 수 있는) 행 구간들이 [0, row_count-1]를 모두 덮으면 그 열
            cols = set()
            for c in range(col_count):
                spans = [(t, b) for (t, b, l, r) in ranges if l <= c <= r]
                if spans and self._spans_cover(spans, row_count):
                    cols.add(c)
            return cols
        # 행 전체 선택: 행 경계로 밴드를 나눠, 밴드를 덮는 range들의 열 구간이 모든 열을 덮으면 그 밴드의 행들
        rows = set()
        bounds = sorted({x for (t, b, _, _) in ranges for x in (t, b + 1) if 0 <= x <= row_count})
        for i in range(len(bounds) - 1):
            a, b = bounds[i], bounds[i + 1]
            spans = [(l, r) for (t, bot, l, r) in ranges if t <= a and bot >= b - 1]
            if spans and self._spans_cover(spans, col_count):
                rows.update(range(a, b))
        return rows

    def _update_undo_buttons(self):
        # 현재 CSV 히스토리에 따라 undo/redo 버튼 활성/비활성 + 아이콘 교체 (모델/히스토리 없으면 둘 다 비활성).
        entry = self._current_edit_entry()
        hist = entry.get('history') if entry else None
        can_undo = bool(hist) and hist.can_undo()
        can_redo = bool(hist) and hist.can_redo()
        self.button_undo.setEnabled(can_undo)
        self.button_redo.setEnabled(can_redo)
        self.button_undo.setIcon(self._icon_undo if can_undo else self._icon_undo_off)
        self.button_redo.setIcon(self._icon_redo if can_redo else self._icon_redo_off)

    def _show_toast(self, text, ms=1400, success=True):
        # 짧은 알림(table_csv 오른쪽 위 구석, 네모 박스). 성공=초록 / 실패=빨강. ms 후 자동 숨김.
        if success:
            bg = "rgba(54, 186, 101, 220)"
        else:
            bg = "rgba(200, 55, 55, 200)"
        self.toast.setStyleSheet(
            "QLabel { color: white; background-color: %s; border-radius: 3px;" 
            "padding: 8px 14px; font-size: 14px; font-weight: 600; }" % (bg))
        self.toast.setText(text)
        self.toast.adjustSize()
        # table_csv 오른쪽 위 구석을 토스트 부모(self) 좌표계로 변환해 안쪽 여백만큼 들여 배치
        tr = self.table_csv.mapTo(self, self.table_csv.rect().topRight())
        margin = 15
        x = tr.x() - self.toast.width() - margin
        y = tr.y() + margin - 5
        self.toast.move(max(0, x), max(0, y))
        self.toast.raise_()
        self.toast.show()
        self._toast_timer.start(ms)

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