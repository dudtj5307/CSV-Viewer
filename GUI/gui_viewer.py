import os
import sys
import hashlib
from bisect import bisect_right

from PyQt6.QtWidgets import QAbstractItemView, QMainWindow, QTableView, QApplication, QColorDialog, QLabel, QFileDialog, QMessageBox, QHeaderView
from PyQt6.QtGui import QIcon, QPixmap, QBrush, QColor, QMovie, QFont
from PyQt6.QtCore import Qt, QTimer, QSize, QEvent

from utils.table_model import CSVTableModel
from utils.filter_model import CSVFilterProxyModel
from utils.csv_loader import CSVLoaderThread
from utils.search_model import SearchModel
from utils.edit_history import EditHistory, Memento
from utils import view_state

from GUI.ui.dialog_viewer import Ui_ViewerWindow

from GUI.gui_esc import EscCloseToast
from GUI.gui_header import FilterHeaderView, MarkerVHeaderView
from GUI.gui_delegate import CompareBorderDelegate
from GUI.gui_listmark import EditMarkDelegate


class ViewerWindow(QMainWindow, Ui_ViewerWindow):
    # ESC 연타로 창 닫기 기능은 GUI/gui_esc.EscCloseToast 로 분리됨(GraphWindow 와 공유).
    # 간격/토스트 상수는 EscCloseToast.INTERVAL_SEC 참조.

    # Undo/Redo(Ctrl+Z/Ctrl+Y): CSV별로 되돌릴 수 있는 최대 '액션' 수 (baseline 제외).
    MAX_UNDO_STEPS = 20

    # 숨기기 트리거: 섹션을 끌어 '시작 끝(왼쪽/위)'으로부터 이 px 이하까지 좁히면 숨김.
    # ⚠ 열과 행을 다르게: 열은 기본폭(80)의 1/4=20px 라 적당하지만, 행은 기본높이(20)와 임계가
    #   같아 살짝만 끌어도 바로 접혀 빡빡했다 → 행은 기본높이의 1/2=10px 로 낮춰 끄는 여유를 둔다.
    # ⚠ 줌 연동: 절대 px 가 아니라 'ZOOM_COL_WIDTH/ZOOM_ROW_HEIGHT 의 분수'라 배율을 자동으로 따라간다
    #   (아래 HIDE_THRESHOLD_COL/ROW property). 100%: 열=80*1/4=20, 행=20*1/2=10.
    HIDE_THRESH_COL_FRAC = 0.25   # 열: 기본폭의 1/4 이하로 좁히면 숨김
    HIDE_THRESH_ROW_FRAC = 0.5    # 행: 기본높이의 1/2 이하로 좁히면 숨김

    # ---------- 확대/축소(Ctrl + 마우스 휠) ----------
    # 5단계 줌(아주작게/작게/중간/크게/아주크게). 인덱스 2 = 100%(기본, 항상 여기서 시작 — 저장 안 함).
    # 아래 배열들은 '단계별 절대값'이라 직접 수정하며 테스트할 수 있다(행높이/열너비/마커두께/폰트크기).
    # ⚠ 인덱스 2 는 현재(100%) 기본값과 같게 유지할 것: 열=80·행=20·마커=18(폰트는 base 폰트를 그대로 사용).
    ZOOM_PERCENT     = [50, 75, 100, 125, 150]   # 우상단 오버레이에 표시할 숫자(%)
    ZOOM_FONT_PT     = [6,  8,  10,  12,  14]    # 셀/헤더 글자 크기(pt) — 인덱스 2 가 100% 기준
    ZOOM_ROW_HEIGHT  = [13, 16, 20,  24,  32]    # 행 높이(px)  (기본 20)
    ZOOM_COL_WIDTH   = [54, 72, 80,  100, 120]    # 열 너비(px)  (기본 80)
    ZOOM_MARKER_PX   = [12, 15, 18,  23,  28]    # 숨김 마커(⋯/︙) 두께(px) (기본 18) — 델타·숨김열도 줌 적용(req7)
    ZOOM_VHEADER_W   = [34, 44, 48,  62,  72]    # 좌측 행번호(세로 헤더) 칸 폭(px) (기본 48)
    ZOOM_DEFAULT_INDEX = 2

    @property
    def MARKER_SIZE_PX(self):
        # 마커 섹션(⋯/︙) 두께 — 줌 단계에 연동(숨김 열/행이 확대/축소를 함께 따라가게).
        # 예전엔 고정 18 상수였으나 property 로 바꿔 마커를 다루는 모든 호출부가 자동으로 줌을 따른다.
        return self.ZOOM_MARKER_PX[self._zoom_index]

    @property
    def HIDE_THRESHOLD_COL(self):
        # 열 숨기기 임계(px) = 현재 줌의 기본 열너비 × 1/4. 100%=20.
        return self.ZOOM_COL_WIDTH[self._zoom_index] * self.HIDE_THRESH_COL_FRAC

    @property
    def HIDE_THRESHOLD_ROW(self):
        # 행 숨기기 임계(px) = 현재 줌의 기본 행높이 × 1/2. 100%=10(열보다 끄는 여유가 큼).
        return self.ZOOM_ROW_HEIGHT[self._zoom_index] * self.HIDE_THRESH_ROW_FRAC

    def __init__(self, icon_path, csv_folder=None):
        super(ViewerWindow, self).__init__(None)
        self.setupUi(self)

        self.icon_path = icon_path       # GUI/res 리소스 경로 (백엔드가 주입)

        self._zoom_index = self.ZOOM_DEFAULT_INDEX   # 현재 줌 단계(MARKER_SIZE_PX property 가 참조 → 일찍 설정)

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

        # CSV 경로 표시 (edit_csv_path=상위 경로 / edit_csv_path2=폴더명)
        self._set_path_fields()
        # 폴더 버튼: 폴더 선택창이 아니라 상위 경로(edit_csv_path)를 Windows 탐색기 새 창으로 연다
        # (경로가 비었거나 무효하면 실행파일 위치로 대체). 폴더 변경은 아래 경로 텍스트 클릭/드래그&드롭으로.
        self.button_csv_folder.setToolTip("Open folder in Explorer")
        self.button_csv_folder.clicked.connect(self.open_folder_in_explorer)

        # edit_csv_path(상위 경로) 텍스트 클릭은 기존대로 폴더 선택창(다른 CSV 폴더로 변경, readOnly라 클릭 전용)
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
        self.cache = {}     # {csv_file_name: {'table_model', 'table_data', 'last_view', 'col_widths', 'row_heights', 'signature', 'content_hash', 'status'}}
        self.saved_states = {}      # {csv_file_name: 저장된 분석상태} - 폴더 .viewer 에서 로드(각 CSV 최초 열람 시 hash 일치하면 적용)
        self._load_view_states()
        self.load_csv_list()


        # CSV List
        self.list_csv_names.currentItemChanged.connect(self.clicked_csv_list)

        # 편집/저장 상태 연필 마커 — 항목 우측 끝에 white/green/yellow 연필 overlay.
        # _mark_state = {csv명: 'white'|'green'|'yellow'} 영속 출처(리스트 clear/재구성에도 보존).
        self._mark_state = {}
        self._mark_delegate = EditMarkDelegate(self.icon_path, self.list_csv_names)
        self.list_csv_names.setItemDelegate(self._mark_delegate)

        # CSV 이름 검색칸: 입력 문자를 포함하는 항목만 표시 + 우측 'x'(지우기) 버튼
        self.edit_csvname_find.setClearButtonEnabled(True)
        self.edit_csvname_find.textChanged.connect(self._filter_csv_list)

        # ESC 연타로 창 닫기 토스트(둥근 토스트 + 그림자) — EscCloseToast(gui_esc)로 분리, GraphWindow 와 공유.
        self.esc_toast = EscCloseToast(self)

        # Search Widget
        self.search_model = SearchModel(self.table_csv)
        self.search_model.search_widget_update.connect(self.search_gui_update)
        self.button_forward.clicked.connect(self.search_model.previous_match)
        self.button_backward.clicked.connect(self.search_model.next_match)
        self.button_close.clicked.connect(self.search_gui_hide)
        self.frame_search.setVisible(False)

        # Custom horizontal header with filtering
        self.table_csv.setHorizontalHeader(FilterHeaderView(Qt.Orientation.Horizontal, self))
        # Custom vertical header — 행 마커(︙) 페인트 전용(가로 헤더와 대칭). 더블클릭 펼침은 ViewerWindow 가 처리.
        self.table_csv.setVerticalHeader(MarkerVHeaderView(Qt.Orientation.Vertical, self))
        # 헤더 교체로 위에서 건 세로헤더 배경 스타일시트가 사라지므로 새 헤더에 다시 적용
        self.table_csv.verticalHeader().setStyleSheet("QHeaderView::section:vertical { background-color: rgb(240, 240, 240); }")
        # ⚠ 최소 섹션크기는 Qt 기본값을 그대로 둔다(예전엔 5px 로 낮췄으나 너무 작아 복원). 숨기기 감지는
        #   '마우스가 섹션 시작 끝을 넘었는가(geometry)' 기반이라 최소폭과 무관하게 동작한다.
        # 마커 더블클릭 → 그 구간 펼치기 (헤더 섹션 더블클릭 + 본문 셀 더블클릭 둘 다 받음)
        self.table_csv.horizontalHeader().sectionDoubleClicked.connect(self._on_hcol_double_clicked)
        self.table_csv.verticalHeader().sectionDoubleClicked.connect(self._on_vrow_double_clicked)
        self.table_csv.doubleClicked.connect(self._on_cell_double_clicked)

        # Δ 셀 선택 시 비교한 두 부모셀에 테두리(현재=파랑/이전=빨강)를 그리는 delegate
        self.border_delegate = CompareBorderDelegate(self.table_csv)
        self.table_csv.setItemDelegate(self.border_delegate)
        self._wired_sel_model = None     # 핸들러를 연결한 selectionModel 추적(setModel 마다 새로 생겨 중복 연결 방지)

        # CSV table headers - size (줌 인덱스 2 = 100% 기본값 80/20)
        self.table_csv.horizontalHeader().setDefaultSectionSize(self.ZOOM_COL_WIDTH[self.ZOOM_DEFAULT_INDEX])     # cell width
        self.table_csv.verticalHeader().setDefaultSectionSize(self.ZOOM_ROW_HEIGHT[self.ZOOM_DEFAULT_INDEX])      # cell height
        self.table_csv.verticalHeader().setFixedWidth(self.ZOOM_VHEADER_W[self.ZOOM_DEFAULT_INDEX])
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
        # 숨기기 제스처 추적: press 때 리사이즈 그립을 잡고(어느 섹션/시작 끝/원래 크기), release 때 마우스가
        # 그 섹션 시작 끝보다 왼쪽/위로 갔으면(음수 너비) 숨김으로 해석한다. (Qt sectionResized 는 최소폭으로
        # 클램프돼 음수를 안 주므로 마우스 좌표 geometry 로 판정 — 이미 최소폭인 섹션도 press 로 잡아 숨길 수 있음)
        self._resize_grip = None       # (horizontal, idx, lead_px, pre_size) | None
        self._resize_release = None    # (x, y) 뷰포트 좌표 | None
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

        # ---------- 3D 그래프 (button_graph) ----------
        # 현재 CSV 의 열로 x/y/z 3D 궤적을 그리는 별도 창. GraphWindow 인스턴스 1개를 재사용
        # (다시 열면 현재 CSV 로 갱신). pyqtgraph/OpenGL 은 무거워 콜드스타트를 늦추므로 import 는
        # open_graph 에서 지연 로드한다.
        self._graph_window = None
        # 3D 축/큐브 아이콘. 원본 png 가 정사각(669x639)이라 작은 버튼(27x21)에 default iconSize
        # 로는 작게 찍혀, 버튼 내부(테두리 1px 제외)를 채우도록 iconSize 를 명시한다.
        self.button_graph.setIcon(QIcon(os.path.join(self.icon_path, "button_graph.png")))
        self.button_graph.setIconSize(QSize(27, 25))
        self.button_graph.clicked.connect(self.open_graph)

        # ---------- 확대/축소(Ctrl + 마우스 휠) ----------
        # 100%(인덱스 2)가 현재 모습과 픽셀 동일하도록, 줌 폰트는 '지금의 base 폰트'에서 크기만 바꿔 재구성한다.
        # (헤더는 교체된 뒤라 여기서 base 폰트를 캡처한다.)
        self._base_table_font   = self.table_csv.font()
        self._base_hheader_font = self.table_csv.horizontalHeader().font()
        self._base_vheader_font = self.table_csv.verticalHeader().font()
        # 우상단 % 오버레이(잠깐 떴다 사라짐) — 토스트와 유사하나 위치/스타일만 다름
        self.zoom_label = QLabel(self)
        self.zoom_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.hide()
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(self.zoom_label.hide)
        # Ctrl+휠 감지: 마우스 휠 이벤트는 테이블 뷰포트로 온다 → 거기에 eventFilter(헤더와 동일 패턴)
        self.table_csv.viewport().installEventFilter(self)


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
            if proxy_model.is_hidemark_row(pi.row()) or proxy_model.is_hidemark_column(pi.column()):
                continue                                # 마커 셀은 색칠 대상 아님
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
        self._apply_zoom(self.ZOOM_DEFAULT_INDEX, announce=False)     # 줌도 100%로 — raw 뷰는 배율도 기본
        proxy = entry['table_model']
        source = proxy.sourceModel()
        hdr = self.table_csv.horizontalHeader()
        default_w = hdr.defaultSectionSize()                         # 줌 리셋 후 기본 너비(=80)
        src_cols = source.columnCount()
        # 이미 raw(분석 0)면 빈 단계 기록 방지 — no-op. 행높이는 dirty 플래그로 싸게 판정.
        has_analysis = (
            bool(source.highlight_cells)
            or proxy.has_delta_colors()
            or bool(proxy.column_filters)
            or proxy.columnCount() != src_cols                       # Δ 가상열 또는 숨긴 열로 열 수 변동
            or proxy.has_hidden()                                    # 행/열 숨김 존재
            or any(hdr.sectionSize(c) != default_w for c in range(hdr.count()))
            or self._rows_dirty                                      # 행높이가 기본(20)에서 바뀜
        )
        if not has_analysis:
            return
        self.table_csv.clearSelection()
        self._restore_memento(entry, Memento(highlights={}, fd={}, widths=[default_w] * src_cols, rows=None))
        self.record_history({'highlights', 'fd', 'widths', 'rows'})   # 전 슬라이스 1단계 = 가역

    def open_graph(self):
        # button_graph: 현재 CSV 를 3D 그래프 창으로 연다. 데이터는 **proxy 모델 기준**(값 필터·Δ
        # 적용, 단 행/열 '숨기기'는 무시 → 숨긴 것도 포함). graph_dataset() 이 그 (headers, rows) 를 만든다.
        entry = self._current_edit_entry()
        if entry is None:
            self._show_toast("No CSV loaded", success=None)   # 중립(회색) 안내
            return
        headers, rows = entry['table_model'].graph_dataset()
        # GraphWindow 는 pyqtgraph/OpenGL 의존 → 첫 클릭 때 지연 import (콜드스타트 보호)
        from GUI.gui_graph import GraphWindow
        if self._graph_window is None:
            # parent 를 주지 않는다(독립 top-level). 부모를 두면 첫 OpenGL 표시 때 메인 창이
            # 재생성돼 깜빡인다 → 대신 closeEvent 에서 명시적으로 닫는다.
            self._graph_window = GraphWindow(self.icon_path)
        self._graph_window.set_data(headers, rows, self.csv_file_name, self._folder())
        self._graph_window.show()
        self._graph_window.raise_()
        self._graph_window.activateWindow()

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
            self.esc_toast.handle_esc()   # 첫 ESC=안내 토스트, 간격 내 재-ESC=창 닫힘

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

    def open_folder_in_explorer(self):
        # 폴더 버튼: 폴더 선택창이 아니라 edit_csv_path(상위 경로)를 Windows 탐색기 새 창으로 연다.
        # 경로가 비었거나 더 이상 존재하지 않으면 이 실행파일이 있는 디렉터리로 대체한다.
        target = self.csv_folder_path
        if not target or not os.path.isdir(target):
            target = self._app_dir()
        try:
            os.startfile(target)        # Windows 전용: 폴더를 탐색기 새 창으로 연다
        except OSError:
            pass

    def _app_dir(self):
        # 이 앱의 위치 — frozen(exe) 빌드는 실행파일 디렉터리, 개발 실행은 진입 스크립트 디렉터리.
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(sys.argv[0]))

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
        self._mark_state.clear()                                # 새 폴더 → 모든 연필 마커 초기화
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
            self.cache[csv_file_name] = {'table_model': None, 'table_data': None, 'last_view': None,
                                         'col_widths': None, 'row_heights': None,
                                         'signature': None, 'content_hash': None, 'status': None}
        return self.cache[csv_file_name]

    def _scan_overrides(self, header, skip=None):
        # 헤더에서 '기본값과 다른' 섹션만 {인덱스: 크기} sparse 로 추출(열너비/행높이 공용).
        # ⚠ 기준은 하드코딩(80/20)이 아니라 header.defaultSectionSize() — 미수정 섹션은 정의상 이 값을
        #   반환한다. 행 기본높이가 폰트/스타일 최소치로 클램프돼 20이 아니어도(예: 30) '안 바뀐 행'을 안
        #   저장한다. (하드코딩 20 이면 클램프된 기본행이 전부 '변경됨'으로 잡혀 .viewer 에 모든 행이 저장되던 버그.)
        # ⚠ skip = 마커(⋯/︙) 섹션 인덱스 — 마커의 고정 두께(18)를 '열너비 오버라이드'로 저장하면 다음 로드 때
        #   엉뚱한 열에 적용되므로 제외한다(마커 두께는 update_table 이 항상 다시 부여).
        # 한 번 스캔(섹션당 O(1)) — CSV 전환·Ctrl+S 같은 사용자 액션에서만 호출(핫패스 아님).
        default = header.defaultSectionSize()
        skip = skip or ()
        out = {}
        for i in range(header.count()):
            if i in skip:
                continue
            s = header.sectionSize(i)
            if s != default:
                out[i] = s
        return out

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
            # 열너비·행높이도 per-CSV 저장(last_view 와 동일 범주). 기본값과 다른 것만 sparse {인덱스:크기}.
            # 열은 수 적어 항상 스캔, 행은 _rows_dirty 일 때만(아니면 18만 행 스캔 회피). 마커 섹션은 제외.
            pm = prev['table_model']
            prev['col_widths'] = self._scan_overrides(self.table_csv.horizontalHeader(), skip=set(pm.marker_col_positions()))
            prev['row_heights'] = self._scan_overrides(self.table_csv.verticalHeader(), skip=set(pm.marker_row_positions())) if self._rows_dirty else {}

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
        # 사라진 파일의 연필 마커도 정리 (남은 파일 마커는 보존 → 아래에서 항목에 재적용)
        for name in [n for n in self._mark_state if n not in disk_set]:
            self._mark_state.pop(name, None)

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
            if name in self._mark_state:        # 보존된 연필 마커를 재구성된 항목에 다시 적용
                self._set_item_mark(name, self._mark_state[name])
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
        self._mark_after_background_load(csv_file_name, entry)  # 다른 CSV 보는 중 로드 완료 시에도 green 연필

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
            self._refresh_mark(csv_file_name)   # 빈/실패 → 히스토리 없음 → 연필 제거
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

        self._set_zoom_font(self._zoom_index)   # 현재 줌 단계 폰트를 새 모델/헤더에 동기화(Δ 셀 italic 크기 포함)

        # 기본 섹션 크기 = 현재 줌 단계 기준(100%면 80/20). 줌 상태에서 다른 CSV 로 전환해도 동일 배율 유지.
        self.table_csv.horizontalHeader().setDefaultSectionSize(self.ZOOM_COL_WIDTH[self._zoom_index])     # cell width
        self.table_csv.verticalHeader().setDefaultSectionSize(self.ZOOM_ROW_HEIGHT[self._zoom_index])      # cell height
        self._rows_dirty = False        # 모델 부착 시 행높이는 기본으로 리셋됨 → dirty 해제(baseline=기본)

        # Set Column Widths (per-CSV) - 저장된 너비가 있으면 복원.
        # ⚠ 가로 스크롤(last_view)보다 먼저 적용해야 너비가 바꾼 스크롤 범위에 값이 클램프되지 않는다.
        # 저장 포맷 = sparse {인덱스:크기}(기본값과 다른 것만) → 범위 내 인덱스만 적용(Δ 열 수 불일치도 안전).
        hdr = self.table_csv.horizontalHeader()
        for c, w in (entry.get('col_widths') or {}).items():
            if 0 <= c < hdr.count():
                hdr.resizeSection(c, w)
        # 행높이도 동일 복원(열과 parity). 적용되면 _rows_dirty=True → baseline 이 행높이를 스냅샷에 포함.
        vhdr = self.table_csv.verticalHeader()
        row_heights = entry.get('row_heights') or {}
        for r, h in row_heights.items():
            if 0 <= r < vhdr.count():
                vhdr.resizeSection(r, h)
        if row_heights:
            self._rows_dirty = True

        # 마커 섹션(⋯/︙)은 저장된 col_widths/row_heights 에서 제외돼 있으므로 여기서 항상 고정 두께 + Fixed 모드로.
        proxy_disp = entry['table_model']
        for pc in proxy_disp.marker_col_positions():
            if 0 <= pc < hdr.count():
                hdr.resizeSection(pc, self.MARKER_SIZE_PX)
        for pr in proxy_disp.marker_row_positions():
            if 0 <= pr < vhdr.count():
                vhdr.resizeSection(pr, self.MARKER_SIZE_PX)
        self._fix_marker_sections(hdr, proxy_disp.marker_col_positions())     # 마커만 리사이즈 불가(스테일 Fixed 도 정리)
        self._fix_marker_sections(vhdr, proxy_disp.marker_row_positions())

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
        self._refresh_mark(csv_file_name)   # 로드/자동복원/캐시재사용 직후 연필 상태 반영

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
        if hasattr(model, "is_hidemark_column"):
            cols = [c for c in cols if not model.is_hidemark_column(c)]   # 마커 열은 복사에서 제외

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
            if fast and accepted[r] < 0:        # 마커 행 → 소스 데이터 없음(복사 제외)
                continue
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
        # 라이브 캡처: 크기/스크롤은 cache가 'CSV 전환 시'에만 갱신되므로 저장 시점엔 뷰에서 직접 읽는다.
        # 열너비·행높이는 기본값과 다른 것만 {크기:[인덱스]} 그룹으로(행은 _rows_dirty 일 때만 스캔).
        file_state = {
            'csv_sha256': entry.get('content_hash'),
            'csv_size':   sig[0] if sig else None,
            'highlights': source.export_highlights(),
            'col_widths': view_state.pack_sizes(self._scan_overrides(hdr, skip=set(proxy.marker_col_positions()))),
            'row_heights': view_state.pack_sizes(
                self._scan_overrides(self.table_csv.verticalHeader(), skip=set(proxy.marker_row_positions()))
                if self._rows_dirty else {}),
            'scroll':     [self.table_csv.verticalScrollBar().value(),
                           self.table_csv.horizontalScrollBar().value()],
        }
        file_state.update(proxy.export_state())     # column_filters, deltas
        if view_state.save_file_state(self._folder(), name, file_state):
            self.saved_states[name] = file_state     # 인메모리도 동기화(F5 재로드 시 일관)
            hist = entry.get('history')
            if hist is not None:
                entry['clean_memento'] = hist.current()   # 저장점 = 방금 저장한 상태
                entry['has_saved'] = self._saved_state_has_analysis(file_state)   # 유효한 분석을 저장했을 때만 green
            self._refresh_mark(name)
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
            entry['col_widths'] = view_state.unpack_sizes(saved.get('col_widths'), 80)    # sparse {인덱스:너비}(구포맷 배열도 흡수)
            entry['row_heights'] = view_state.unpack_sizes(saved.get('row_heights'), 20)  # sparse {인덱스:높이}
            if saved.get('scroll'):
                entry['last_view'] = tuple(saved['scroll'])         # update_table 꼬리의 스크롤 복원이 사용
            entry['has_saved'] = self._saved_state_has_analysis(saved)   # '유효한 분석'이 담긴 저장본일 때만 green/yellow 대상
        except Exception as e:
            # 저장본이 손상/구버전이어도 CSV 열람은 절대 막지 않음(부분 복원이라도 진행)
            print(f"[ViewState] restore failed for '{name}': {e}")

    # ---------- 편집/저장 상태 연필 마커(목록 우측) ----------
    # 판정은 '저장/불러오기 시점의 Memento 객체(clean_memento)와 현재가 동일한가'의 identity 비교다.
    # 실시간 값 비교가 아니라 Undo 스택 위치 기준 — 그 시점으로 되돌아오면(undo) 같은 객체라 다시 clean 이 된다.
    #   clean_memento : 저장점 Memento. baseline 생성 시 baseline 으로 두고, 저장/자동복원 시 그 시점으로 갱신.
    #   has_saved     : 그 clean 이 .viewer 저장점인지(True=green/yellow 후보) vs 단순 raw 로드 baseline 인지.
    def _compute_mark_state(self, entry):
        # hist = entry.get('history') if entry else None
        # if hist is None:
        #     return None                          # 모델/baseline 없음(미로드·빈·실패) → 연필 없음
        # if entry.get('has_saved'):               # '유효한 분석이 담긴' .viewer 저장점이 있을 때만 green/yellow
        #     return 'green' if hist.current() is entry.get('clean_memento') else 'yellow'
        # else:
        #     return 'None' if hist.current() is entry.get('clean_memento') else 'white'

        if entry is None:   # cache 미생성 - 미전시
            return None

        saved = entry.get('has_saved')
        hist = entry.get('history')
        memento = entry.get('clean_memento')

        if saved:
            if hist and (hist.current() is not memento):
                return 'yellow'
            else:
                return 'green'
        else:
            if hist and (hist.current() is not memento):
                return 'white'
            else:
                return None

    def _saved_state_has_analysis(self, saved):
        # 저장 상태(file_state dict)에 '유효한 분석'이 하나라도 있는지. 빈 .viewer(스크롤만 등)는 green 아님.
        if not saved:
            return False
        return bool(
            saved.get('highlights')                                   # 하이라이트
            or saved.get('column_filters')                            # 열 값 필터
            or saved.get('deltas')                                    # Δ 가상열
            or saved.get('hidden_rows') or saved.get('hidden_cols')   # 행/열 숨김
            or saved.get('col_widths') or saved.get('row_heights')    # 기본과 다른 열너비/행높이
        )

    def _set_item_mark(self, name, state):
        # 영속 dict + 항목 data 동기화(항목 data 변경 → 델리게이트 자동 repaint).
        if state:
            self._mark_state[name] = state
        else:
            self._mark_state.pop(name, None)
        for item in self.list_csv_names.findItems(name, Qt.MatchFlag.MatchExactly):
            item.setData(EditMarkDelegate.STATE_ROLE, state)

    def _refresh_mark(self, name=None):
        # 현재 CSV(또는 지정 CSV)의 연필 상태를 재계산해 반영. 분석 편집/undo/redo/저장/로드 꼬리에서 호출.
        if name is None:
            name = self.csv_file_name
        if not name:
            return
        self._set_item_mark(name, self._compute_mark_state(self.cache.get(name)))

    def _saved_is_applicable(self, name, entry):
        # 이 CSV 에 '지금 내용과 해시 일치 + 유효한 분석이 담긴' .viewer 저장본이 있는가(green 연필 기준).
        saved = self.saved_states.get(name)
        return bool(saved
                    and saved.get('csv_sha256') == entry.get('content_hash')
                    and self._saved_state_has_analysis(saved))

    def _mark_after_background_load(self, name, entry):
        # 백그라운드 로드(다른 CSV 보는 중 로드 완료): update_table 이 early-return 해 마커가 안 떴다.
        # 모델/baseline 은 그 CSV 를 처음 클릭할 때 lazy 생성하고(공유 헤더에서 baseline 너비 오염 회피),
        # 여기선 '유효 저장본 보유' 여부만 정해 green 연필을 띄운다. 현재 CSV 면 update_table 이 이미 처리.
        current = self.list_csv_names.currentItem()
        if current is not None and current.text() == name:
            return
        if entry.get('table_model') is None:        # 아직 모델 미생성(순수 백그라운드 로드)일 때만 추정 세팅
            entry['has_saved'] = self._saved_is_applicable(name, entry)
        self._refresh_mark(name)

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
        self._refresh_mark()                # 분석 변경 → 연필 상태 갱신(white/yellow)

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
            entry['clean_memento'] = entry['history'].current()   # 연필 마커 저장점 기준 = 최초 baseline
            # has_saved 는 _apply_saved_state 가 자동복원 성공 시 True 로 세팅(아니면 raw 로드라 미설정=False).

    def undo(self):
        entry = self._current_edit_entry()
        hist = entry.get('history') if entry else None
        m = hist.undo() if hist else None
        if m is not None:
            self._restore_memento(entry, m)
        self._update_undo_buttons()
        self._refresh_mark()                # 되돌림으로 저장점에 도달/이탈 → 연필 갱신

    def redo(self):
        entry = self._current_edit_entry()
        hist = entry.get('history') if entry else None
        m = hist.redo() if hist else None
        if m is not None:
            self._restore_memento(entry, m)
        self._update_undo_buttons()
        self._refresh_mark()                # 다시실행으로 저장점에 도달/이탈 → 연필 갱신

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
            # 마커 섹션은 항상 고정 두께 + Fixed 모드 — widths/rows 메멘토가 마커를 포함 안 할 수도 있어 명시 적용.
            vhdr = self.table_csv.verticalHeader()
            for pc in proxy.marker_col_positions():
                if 0 <= pc < hdr.count():
                    hdr.resizeSection(pc, self.MARKER_SIZE_PX)
            for pr in proxy.marker_row_positions():
                if 0 <= pr < vhdr.count():
                    vhdr.resizeSection(pr, self.MARKER_SIZE_PX)
            self._fix_marker_sections(hdr, proxy.marker_col_positions())
            self._fix_marker_sections(vhdr, proxy.marker_row_positions())
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
            default = vhdr.defaultSectionSize()           # 하드코딩 20 아닌 실제 기본높이(클램프 대응)
            if self._rows_dirty:                          # 현재 오버라이드가 있을 때만 청소(대용량 무필요 스캔 회피)
                for r in range(n):
                    if vhdr.sectionSize(r) != default:
                        vhdr.resizeSection(r, default)
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
        # 두 헤더 viewport 의 press/release 를 잡아 (1) 리사이즈 그립을 기억하고 (2) '드래그 종료 시
        # 일괄 적용/숨김'을 트리거한다(QHeaderView 엔 종료 신호 없음). ⚠ 마우스 이벤트는 헤더가 아니라
        # viewport 로 오므로 obj 도 viewport 와 비교한다. singleShot(0) 으로 헤더 자체 release 처리 직후
        # (=최종 크기 확정 후) 실행. _pending_*/_resize_grip 둘 다 없으면 no-op 라 단순 클릭/우클릭엔 무영향.
        et = event.type()
        # Ctrl + 마우스 휠 = 확대/축소. 휠 이벤트는 테이블 뷰포트로 온다. Ctrl 없으면 평소대로 스크롤(통과).
        if et == QEvent.Type.Wheel and obj is self.table_csv.viewport():
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if self.table_csv.model() is not None:
                    step = 1 if event.angleDelta().y() > 0 else -1
                    self._apply_zoom(self._zoom_index + step)
                return True              # 줌으로 소비 → 세로 스크롤로 새지 않게
            return super().eventFilter(obj, event)
        hv = self.table_csv.horizontalHeader().viewport()
        vv = self.table_csv.verticalHeader().viewport()
        if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if obj is hv:
                self._capture_resize_grip(True, event)
            elif obj is vv:
                self._capture_resize_grip(False, event)
        elif et == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if obj is hv or obj is vv:
                p = event.position()
                self._resize_release = (p.x(), p.y())     # release 시점 좌표(이후 사라짐) → 미리 캡처
                QTimer.singleShot(0, lambda h=(obj is hv): self._finalize_resize(h))
        return super().eventFilter(obj, event)

    def _capture_resize_grip(self, horizontal, event):
        # press 가 리사이즈 그립(섹션 경계 ±몇 px) 위면 그 섹션·시작끝·원래 크기를 기억. 아니면 None.
        # 그립이 잡는 섹션 = 경계의 '왼쪽/위' 섹션(엑셀과 동일, 그 섹션의 trailing 경계를 끈다).
        header = self.table_csv.horizontalHeader() if horizontal else self.table_csv.verticalHeader()
        p = event.position()
        pos = p.x() if horizontal else p.y()
        idx = self._section_at_grip(header, int(pos))
        if idx is None:
            self._resize_grip = None
            return
        self._resize_grip = (horizontal, idx, header.sectionViewportPosition(idx), header.sectionSize(idx))

    @staticmethod
    def _section_at_grip(header, pos, grip=6):
        # pos(뷰포트 좌표) 근처 리사이즈 경계의 '왼쪽/위' 섹션 인덱스. 경계 ±grip 안일 때만, 아니면 None.
        # 이 앱은 섹션 이동이 없어 visual==logical → logicalIndexAt 로 충분(O(1)).
        li = header.logicalIndexAt(pos)
        if li < 0:
            return None
        left = header.sectionViewportPosition(li)
        right = left + header.sectionSize(li)
        if right - pos <= grip and pos <= right + grip:   # li 의 trailing 경계 근처 → li 를 리사이즈
            return li
        if pos - left <= grip and li > 0:                  # li 의 leading 경계 근처(= li-1 trailing) → li-1
            return li - 1
        return None

    def _finalize_resize(self, horizontal):
        # 드래그 종료. 먼저 '숨기기 제스처'(경계를 그 섹션 시작 끝 너머로 = 음수 너비)인지 판정하고,
        # 아니면 다중 완전선택 동시조정(엑셀)으로 전파한다.
        grip, self._resize_grip = self._resize_grip, None
        rel, self._resize_release = self._resize_release, None
        if horizontal:
            pending, self._pending_h = self._pending_h, None
        else:
            pending, self._pending_v = self._pending_v, None

        # --- 숨기기 판정: 잡은 그립 섹션의 시작 끝(lead)보다 마우스가 임계값 이상 더 갔는가 ---
        if grip is not None and grip[0] == horizontal and rel is not None:
            _, gidx, lead, pre_size = grip
            pos = rel[0] if horizontal else rel[1]
            thresh = self.HIDE_THRESHOLD_COL if horizontal else self.HIDE_THRESHOLD_ROW
            if pos - lead <= thresh:
                self._do_hide_gesture(horizontal, gidx, pre_size)
                return

        # --- 일반 리사이즈: 잡은 섹션이 다중 완전선택의 일원이면 나머지를 같은 크기로 전파 ---
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

    # ---------- 행/열 숨기기 (드래그-to-음수너비 트리거 + 마커 더블클릭 펼침) ----------
    # ⚠ 마커 두께(MARKER_SIZE_PX)는 클래스 상단의 property 로 정의됨(줌 단계에 연동). 여기서 상수로
    #   재정의하면 property 를 덮어써 마커가 줌을 안 따라가니 절대 다시 상수로 두지 말 것.

    def _do_hide_gesture(self, horizontal, idx, pre_size):
        # 경계를 시작 끝 너머로 끈 결과: 잡은 섹션(+다중 완전선택이면 그 집합 전체)을 숨긴다(연속이면 마커 1개).
        # idx 는 드래그로 최소폭까지 줄어 있으므로 '숨기기 전 원래 크기'는 pre_size 를 보관(펼칠 때 원복).
        self._width_timer.stop()
        self._height_timer.stop()
        self._pending_h = None
        self._pending_v = None
        entry = self._current_edit_entry()
        if entry is None:
            return
        proxy = entry['table_model']
        sections = self._full_selection_sections(horizontal)
        targets = sections if (len(sections) >= 2 and idx in sections) else {idx}
        self._suppress_width_record = True
        try:
            if horizontal:
                hdr = self.table_csv.horizontalHeader()
                sizes = {}
                for pc in targets:
                    if proxy.is_delta_column(pc) or proxy.is_hidemark_column(pc):
                        continue                                  # Δ/마커 열은 숨김 대상 아님(ⓓ)
                    sc = proxy.source_column_of(pc)
                    if sc >= 0:
                        sizes[sc] = pre_size if pc == idx else hdr.sectionSize(pc)
                if not sizes:
                    return
                wmap = self._col_width_map(proxy)                 # 펼쳐진(보이는) 열 너비 캡처(소스 기준)
                self.table_csv.clearSelection()
                proxy.hide_columns(set(sizes), sizes=sizes)
                self._apply_col_width_map(proxy, wmap)            # reset 위치이동 보정 + 마커 얇게
            else:
                vhdr = self.table_csv.verticalHeader()
                sizes = {}
                for pr in targets:
                    if proxy.is_hidemark_row(pr):
                        continue
                    sr = proxy.source_row_of(pr)
                    if sr >= 0:
                        sizes[sr] = pre_size if pr == idx else vhdr.sectionSize(pr)
                if not sizes:
                    return
                old_positions, by_src = self._capture_row_layout(proxy)
                self.table_csv.clearSelection()
                proxy.hide_rows(set(sizes), sizes=sizes)            # 숨기기 전 높이 보관(펼칠 때 원복; 열과 대칭)
                self._apply_row_layout(proxy, old_positions, by_src)
        finally:
            self._suppress_width_record = False
        self.record_history({'fd', 'widths'} if horizontal else {'fd', 'rows'})

    def _on_hcol_double_clicked(self, logical):
        proxy = self.table_csv.model()
        if proxy is not None and hasattr(proxy, "is_hidemark_column") and proxy.is_hidemark_column(logical):
            self._unhide_columns(proxy.hidemark_column_run(logical))

    def _on_vrow_double_clicked(self, logical):
        proxy = self.table_csv.model()
        if proxy is not None and hasattr(proxy, "is_hidemark_row") and proxy.is_hidemark_row(logical):
            self._unhide_rows(proxy.hidemark_row_run(logical))

    def _on_cell_double_clicked(self, index):
        # 본문 셀 더블클릭(헤더를 못 맞춘 경우 대비). 마커 셀이면 그 구간 펼치기.
        proxy = self.table_csv.model()
        if proxy is None or not index.isValid():
            return
        if hasattr(proxy, "is_hidemark_column") and proxy.is_hidemark_column(index.column()):
            self._unhide_columns(proxy.hidemark_column_run(index.column()))
        elif hasattr(proxy, "is_hidemark_row") and proxy.is_hidemark_row(index.row()):
            self._unhide_rows(proxy.hidemark_row_run(index.row()))

    def _unhide_columns(self, src_cols):
        entry = self._current_edit_entry()
        if entry is None or not src_cols:
            return
        proxy = entry['table_model']
        hdr = self.table_csv.horizontalHeader()
        self._suppress_width_record = True
        try:
            wmap = self._col_width_map(proxy)               # 펼치기 전 보이는 열 너비
            restored = proxy.unhide_columns(src_cols)       # {source_col: 숨기기 전 원래 너비}
            self._apply_col_width_map(proxy, wmap)          # 기존 보이는 열 위치 보정 + 남은 마커 얇게
            for sc, w in restored.items():                  # 펼쳐진 열은 원래 너비로
                pc = proxy.proxy_column_of(sc)
                if pc >= 0:
                    hdr.resizeSection(pc, w)
        finally:
            self._suppress_width_record = False
        self.record_history({'fd', 'widths'})

    def _unhide_rows(self, src_rows):
        entry = self._current_edit_entry()
        if entry is None or not src_rows:
            return
        proxy = entry['table_model']
        self._suppress_width_record = True
        try:
            old_positions, by_src = self._capture_row_layout(proxy)
            restored = proxy.unhide_rows(src_rows)       # {source_row: 숨기기 전 원래 높이}
            by_src.update(restored)                      # 펼쳐진 행은 원래 높이로(열과 대칭)
            self._apply_row_layout(proxy, old_positions, by_src)
        finally:
            self._suppress_width_record = False
        self.record_history({'fd', 'rows'})

    def _col_width_map(self, proxy):
        # 보이는 열 너비를 위치독립 키로 캡처: {('s',src_col)|('d',base_col): width}. 마커 제외.
        # 숨김/펼침이 beginResetModel 로 섹션을 위치 기준 보존 → 열 수가 바뀌면 뒤 열이 밀려 어긋나므로
        # 소스 기준으로 다시 적용한다(_apply_col_width_map). O(열 수)라 행 수와 무관.
        hdr = self.table_csv.horizontalHeader()
        out = {}
        for pc in range(hdr.count()):
            if proxy.is_hidemark_column(pc):
                continue
            sc = proxy.source_column_of(pc)
            if sc < 0:
                continue
            out[('d', sc) if proxy.is_delta_column(pc) else ('s', sc)] = hdr.sectionSize(pc)
        return out

    def _apply_col_width_map(self, proxy, wmap):
        # 캡처한 너비를 새 레이아웃 위치에 재적용 + 마커는 얇게. (_suppress_width_record 안에서 호출)
        hdr = self.table_csv.horizontalHeader()
        for (kind, sc), w in wmap.items():
            base_pc = proxy.proxy_column_of(sc)
            if base_pc < 0:
                continue                                    # 숨겨진 열 → 보이는 위치 없음
            pc = base_pc + 1 if kind == 'd' else base_pc    # Δ 는 기준 열 바로 오른쪽
            if 0 <= pc < hdr.count():
                hdr.resizeSection(pc, w)
        for pc in proxy.marker_col_positions():
            hdr.resizeSection(pc, self.MARKER_SIZE_PX)
        self._fix_marker_sections(hdr, proxy.marker_col_positions())

    def _fix_marker_sections(self, header, positions):
        # 마커 섹션만 크기 고정(Fixed) — 사용자가 드래그로 리사이즈 못 하게(나머지는 Interactive).
        # ⚠ 리사이즈 모드는 모델 reset 시 '위치 기준'으로 누수된다(펼친 자리 섹션이 Fixed 로 남음) →
        #   글로벌 Interactive 로 한 번에 되돌린 뒤(측정 0.2ms, 18만 행도 무관·크기 보존) 현재 마커만 Fixed 로.
        #   마커 없을 때 호출하면 스테일 Fixed 만 정리(전부 Interactive) → CSV 전환 시에도 안전.
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for p in positions:
            if 0 <= p < header.count():
                header.setSectionResizeMode(p, QHeaderView.ResizeMode.Fixed)

    def _capture_row_layout(self, proxy):
        # reset 전 호출. 반환 (이전 비기본 proxy 행 위치 목록, {src_row: height}).
        # ⚠ 모델 reset 은 섹션 크기를 '위치(proxy 인덱스) 기준'으로 보존한다 → 비기본 크기가 그 위치에 남아,
        #   숨김으로 그 자리에 밀려든 다른 행이 엉뚱한 크기를 물려받는 '전염'이 생긴다(8~10 키우고 숨기면 11이
        #   커지던 버그). 그래서 _apply_row_layout 이 reset 후 이 위치들을 기본으로 청소한다.
        #   마커(18px)는 항상 비기본이라 _rows_dirty 무관하게 포함. 행 오버라이드는 _rows_dirty 일 때만 스캔.
        #   (열은 _col_width_map 이 *모든* 보이는 열을 캡처·재적용해 전염이 원천 차단되므로 별도 청소 불필요.)
        vhdr = self.table_csv.verticalHeader()
        old_positions = list(proxy.marker_row_positions())
        by_src = {}
        if self._rows_dirty:
            default = vhdr.defaultSectionSize()
            for pr in range(vhdr.count()):
                h = vhdr.sectionSize(pr)
                if h != default:
                    old_positions.append(pr)
                    sr = proxy.source_row_of(pr)
                    if sr >= 0:
                        by_src[sr] = h
        return old_positions, by_src

    def _apply_row_layout(self, proxy, old_positions, size_map):
        # reset 후 호출. 전염 청소(이전 비기본 위치→기본) → 소스 기준 높이 재적용 → 마커 18px.
        # size_map = {src_row: height} (유지할 기존 오버라이드 + 펼침 복원분 합본).
        vhdr = self.table_csv.verticalHeader()
        default = vhdr.defaultSectionSize()
        n = vhdr.count()
        vhdr.setUpdatesEnabled(False)
        dirty = False
        try:
            for p in old_positions:
                if 0 <= p < n:
                    vhdr.resizeSection(p, default)
            for sr, h in size_map.items():
                if h == default:
                    continue
                pr = proxy.proxy_row_of(sr)
                if 0 <= pr < n:
                    vhdr.resizeSection(pr, h)
                    dirty = True
            for pr in proxy.marker_row_positions():
                if 0 <= pr < n:
                    vhdr.resizeSection(pr, self.MARKER_SIZE_PX)
            self._fix_marker_sections(vhdr, proxy.marker_row_positions())
        finally:
            vhdr.setUpdatesEnabled(True)
        self._rows_dirty = dirty

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
            # ⚠ 마커 열(⋯)은 선택 가능이라 완전선택에 잡히지만 동시조정/숨김 대상이 아님 → 제외
            #   (안 빼면 전파 resizeSection 이 Fixed 마커까지 늘려버림 — Fixed 는 사용자 드래그만 막음).
            if hasattr(model, "marker_col_positions"):
                cols -= set(model.marker_col_positions())
            return cols
        # 행 전체 선택: 행 경계로 밴드를 나눠, 밴드를 덮는 range들의 열 구간이 모든 열을 덮으면 그 밴드의 행들
        rows = set()
        bounds = sorted({x for (t, b, _, _) in ranges for x in (t, b + 1) if 0 <= x <= row_count})
        for i in range(len(bounds) - 1):
            a, b = bounds[i], bounds[i + 1]
            spans = [(l, r) for (t, bot, l, r) in ranges if t <= a and bot >= b - 1]
            if spans and self._spans_cover(spans, col_count):
                rows.update(range(a, b))
        if hasattr(model, "marker_row_positions"):    # 마커 행(︙) 제외(O(마커 수) — 18만 set 재순회 안 함)
            rows -= set(model.marker_row_positions())
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

    # ---------- 확대/축소 ----------
    def _apply_zoom(self, new_idx, announce=True):
        # 줌 단계를 new_idx 로 바꾼다(0~4 클램프). 폰트는 절대값으로, 셀 크기는 직전 단계 대비 비율로 스케일.
        #  - 열: 개별 resizeSection(델타 가상열 포함) — 사용자 지정 너비도 비율 유지(엑셀식).
        #  - 행: defaultSectionSize 만 바꿔 기본 행 전체를 O(1) 로 스케일(18만 행 안전). 마커는 줌별 절대 두께.
        #  - 마커(숨김 열/행 ⋯/︙): 줌별 절대 두께라 함께 확대/축소된다(req7). MARKER_SIZE_PX property 가 자동 연동.
        # 프로그램적 리사이즈라 _suppress_width_record 로 undo/히스토리에 안 남긴다.
        new_idx = max(0, min(len(self.ZOOM_PERCENT) - 1, new_idx))
        old = self._zoom_index
        if new_idx != old:
            proxy = self.table_csv.model()
            hdr = self.table_csv.horizontalHeader()
            vhdr = self.table_csv.verticalHeader()
            rc = self.ZOOM_COL_WIDTH[new_idx] / self.ZOOM_COL_WIDTH[old]
            # ScrollPerPixel 이라 줌으로 콘텐츠 크기(스크롤 범위)가 바뀌면 같은 절대 픽셀값이 다른 위치를 가리켜
            # 화면이 튄다 → 줌 *전* 스크롤 비율을 잡아 줌 *후* 그 비율로 복원(보던 위치 유지). 확대/축소 대칭.
            vf = self._scroll_fraction(self.table_csv.verticalScrollBar())
            hf = self._scroll_fraction(self.table_csv.horizontalScrollBar())
            self._zoom_index = new_idx
            self._set_zoom_font(new_idx)
            has_marker = proxy is not None and hasattr(proxy, "marker_col_positions")
            marker_cols = set(proxy.marker_col_positions()) if has_marker else set()
            marker_rows = list(proxy.marker_row_positions()) if has_marker else []
            self._suppress_width_record = True
            hdr.setUpdatesEnabled(False)
            vhdr.setUpdatesEnabled(False)
            try:
                # 행: defaultSectionSize 만 바꿔 기본 행 전체를 1회 스케일(O(1), 18만 행 안전).
                vhdr.setDefaultSectionSize(self.ZOOM_ROW_HEIGHT[new_idx])
                # 열(마커 제외): 각 열을 '직전 크기 × 비율'로 1회만 스케일. ⚠ 반드시 hdr.setDefaultSectionSize
                #   보다 *먼저* 돌아야 한다 — 기본값을 먼저 바꾸면 기본 열이 점프한 뒤 루프가 또 곱해 이중 적용된다.
                for c in range(hdr.count()):
                    if c not in marker_cols:
                        hdr.resizeSection(c, max(10, round(hdr.sectionSize(c) * rc)))
                # 위 루프로 일반 열은 모두 명시 크기 → 이후 기본값 변경은 '앞으로 생길 열(Δ 추가 등)'에만 영향.
                hdr.setDefaultSectionSize(self.ZOOM_COL_WIDTH[new_idx])
                # 마커는 기본값 변경 *뒤* 절대 두께로 고정(앞서 두면 setDefaultSectionSize 가 기본값으로 덮어씀).
                for c in marker_cols:
                    if 0 <= c < hdr.count():
                        hdr.resizeSection(c, self.MARKER_SIZE_PX)            # 마커 열 = 줌별 절대 두께
                for pr in marker_rows:
                    if 0 <= pr < vhdr.count():
                        vhdr.resizeSection(pr, self.MARKER_SIZE_PX)          # 마커 행 = 줌별 절대 두께
                self._fix_marker_sections(hdr, marker_cols)                  # 마커 섹션 Fixed 재고정
                self._fix_marker_sections(vhdr, marker_rows)
            finally:
                hdr.setUpdatesEnabled(True)
                vhdr.setUpdatesEnabled(True)
                self._suppress_width_record = False
            self.table_csv.viewport().update()
            # 비율 복원. 새 스크롤 범위가 즉시 갱신 안 됐을 수 있어 즉시 + 레이아웃 정착 후(singleShot) 한 번 더.
            self._apply_scroll_fraction(self.table_csv.verticalScrollBar(), vf)
            self._apply_scroll_fraction(self.table_csv.horizontalScrollBar(), hf)
            QTimer.singleShot(0, lambda: (
                self._apply_scroll_fraction(self.table_csv.verticalScrollBar(), vf),
                self._apply_scroll_fraction(self.table_csv.horizontalScrollBar(), hf)))
        if announce:
            self._show_zoom_overlay(new_idx)

    @staticmethod
    def _scroll_fraction(bar):
        # 스크롤바의 현재 위치 비율(0.0~1.0). 범위가 0이면(스크롤 불필요) 0.
        rng = bar.maximum() - bar.minimum()
        return (bar.value() - bar.minimum()) / rng if rng > 0 else 0.0

    @staticmethod
    def _apply_scroll_fraction(bar, frac):
        rng = bar.maximum() - bar.minimum()
        if rng > 0:
            bar.setValue(round(bar.minimum() + frac * rng))

    def _set_zoom_font(self, idx):
        # 줌 단계 폰트 적용. 인덱스 2(100%)는 base 폰트 그대로(=현재 모습과 픽셀 동일), 그 외는 크기만 교체.
        # Δ 셀 italic 폰트(proxy._italic_font)도 같은 크기로 동기화해 델타 글자도 함께 확대/축소된다.
        if idx == self.ZOOM_DEFAULT_INDEX:
            self.table_csv.setFont(self._base_table_font)
            self.table_csv.horizontalHeader().setFont(self._base_hheader_font)
            self.table_csv.verticalHeader().setFont(self._base_vheader_font)
            pt = None                                                       # 100% = 셀 FontRole 미지정(뷰 기본 폰트 그대로)
        else:
            pt = self.ZOOM_FONT_PT[idx]
            for widget, base in ((self.table_csv, self._base_table_font),
                                 (self.table_csv.horizontalHeader(), self._base_hheader_font),
                                 (self.table_csv.verticalHeader(), self._base_vheader_font)):
                f = QFont(base)
                f.setPointSize(pt)
                widget.setFont(f)
        # 좌측 행번호 칸 폭도 줌에 맞춰 — 큰 글자/행번호가 잘리지 않게(폰트와 함께 다루면 update_table·줌 모두 커버).
        self.table_csv.verticalHeader().setFixedWidth(self.ZOOM_VHEADER_W[idx])
        proxy = self.table_csv.model()
        if proxy is not None and hasattr(proxy, "set_cell_font_point_size"):
            proxy.set_cell_font_point_size(pt)

    def _show_zoom_overlay(self, idx):
        # 우상단에 현재 배율(예: 150%)을 잠깐 띄웠다 _zoom_timer 로 자동 숨김.
        self.zoom_label.setStyleSheet(
            "QLabel { color: white; background-color: rgba(40, 44, 52, 180);"
            " border-radius: 6px; padding: 6px 14px; font-size: 20px; font-weight: 700; }")
        self.zoom_label.setText(f"{self.ZOOM_PERCENT[idx]}%")
        self.zoom_label.adjustSize()
        tr = self.table_csv.mapTo(self, self.table_csv.rect().topRight())
        margin = 15
        x = tr.x() - self.zoom_label.width() - margin
        y = tr.y() + margin - 10
        self.zoom_label.move(max(0, x), max(0, y))
        self.zoom_label.raise_()
        self.zoom_label.show()
        self._zoom_timer.start(800)

    def _show_toast(self, text, ms=1400, success=True):
        # 짧은 알림(table_csv 오른쪽 위 구석, 네모 박스). 성공=초록 / 실패=빨강 / None=회색(중립 안내). ms 후 자동 숨김.
        if success is None:
            bg = "rgba(120, 120, 120, 220)"
        elif success:
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
        # 독립 top-level 그래프 창도 함께 닫는다 (parent 분리라 자동 종료 안 됨 → 안 닫으면
        # 메인 창을 닫아도 그래프 창이 남아 app 이 종료되지 않음)
        if self._graph_window is not None:
            self._graph_window.close()
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