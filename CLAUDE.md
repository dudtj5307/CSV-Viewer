# CSV Viewer — Claude 참조 문서

## 한 줄 요약
CSV 폴더를 열람하는 **PyQt6 데스크톱 뷰어**. 외부 SW가 `CSV Viewer.exe [CSV폴더경로]`를 실행하면 **독립 프로세스로 뷰어 창 하나**가 뜬다. onedir 빌드라 콜드스타트가 가볍고 **백엔드/IPC가 없는 단순 구조**다. (기존 Packet_Parsing_Software에서 CSV Viewer 부분만 분리 — 패킷 캡처/파싱 기능은 모두 제거됨.)

---

## 개발 환경 / 실행

conda 환경 **`sniff_env`** 에서만 실행/테스트한다 (PyQt6·pyqtgraph·numpy 설치됨).

- 인터프리터: `C:\Users\yslee\anaconda3\envs\sniff_env\python.exe`
- `python`(PATH)=Store 스텁(exit 49), `py`·anaconda base·Python310 → **PyQt6 없음**. 오직 `sniff_env`에만.
- GUI 없이 로직 테스트 시 `QT_QPA_PLATFORM=offscreen` 설정 후 실행.
- ⚠ **offscreen은 폰트 굵기(bold/italic)·GL 렌더를 못 그린다** → 헤더 폰트 스타일·3D 그래프·연필 마커·델리게이트 테두리 등은 **실Windows 육안이 최종 검증**. (좌표/geometry 계산은 offscreen으로 확정 가능.)

```powershell
& "C:\Users\yslee\anaconda3\envs\sniff_env\python.exe" csv_viewer.py            # 인자 없으면 폴더 선택창
& "C:\Users\yslee\anaconda3\envs\sniff_env\python.exe" csv_viewer.py "CSV\raw_250416_174444"
$env:QT_QPA_PLATFORM="offscreen"; & "...\python.exe" <script>.py                # offscreen 로직 테스트
```

---

## 디렉토리 구조

```
Project_CSV_Viewer/
├── csv_viewer.py              # 진입점. 폴더 해석 → ViewerWindow 1개 → app.exec()
├── CSV_Viewer.spec            # PyInstaller 빌드 (onedir, windowed)
├── GUI/
│   ├── gui_viewer.py          # ViewerWindow - CSV 목록 + 테이블 뷰어 (메인 화면)
│   ├── gui_header.py          # FilterHeaderView(가로 헤더: 우클릭 필터·열 마커⋯) + MarkerVHeaderView(세로 헤더: 행 마커︙)
│   ├── gui_filter.py          # FilterWidget - 우클릭 필터 팝업. _FilterItemRow/_filter_sort_key 는 그래프 트랙 목록도 재사용
│   ├── gui_graph.py           # GraphWindow - 3D 궤적 그래프 창(pyqtgraph GLViewWidget). open_graph 에서 지연 import
│   ├── gui_listmark.py        # EditMarkDelegate - CSV 목록 편집/저장 상태 연필 마커
│   ├── gui_delegate.py        # CompareBorderDelegate - Δ 비교/검색 셀 테두리
│   ├── gui_esc.py             # EscCloseToast - ESC 연타 닫기 안내 토스트(ViewerWindow·GraphWindow 공유)
│   ├── res/                   # 아이콘/스피너 리소스 (png·ico·gif)
│   └── ui/                    # pyuic6 자동생성 UI (직접 수정 금지): dialog_viewer·dialog_filter·widget_esc·widget_graph
├── utils/                     # ⚠ viewer/ 가 아니라 utils/ 바로 아래
│   ├── table_model.py         # CSVTableModel (QAbstractTableModel): rows + highlight_cells
│   ├── filter_model.py        # CSVFilterProxyModel: 열 필터 + Δ 가상 열 + 행/열 숨기기
│   ├── search_model.py        # SearchModel (Ctrl+F)
│   ├── csv_loader.py          # CSVLoaderThread (비동기 로딩)
│   ├── edit_history.py        # EditHistory/Memento (Undo/Redo)
│   └── view_state.py          # .viewer 영속화 (저장/로드, QColor↔문자열, 원자적 IO)
└── CSV/                       # 열람 대상 샘플 CSV 폴더들
```

> **미사용 잔여물(PPS 시절)**: `IDL/`, `RAW/`, `test code/`, `settings.conf`, `build/`, `dist/`, `GUI/ui/`의 생성 스크립트. 런타임 무관, 수동 삭제 가능.

---

## 아키텍처: 실행마다 독립 프로세스 + 창 하나

`CSV Viewer.exe` 실행 1회 = 독립 프로세스 1개 = `ViewerWindow` 1개. 단일 인스턴스/IPC/상주 백엔드 **없음**.

```
csv_viewer.py main()
  ├─ setup_std_streams()   : windowed에서 stdout/stderr=None 가드
  ├─ QApplication 생성
  ├─ argv[1] 검사          : 유효 폴더면 folder, 아니면 None
  ├─ ViewerWindow(icon_path, folder|None).show()
  └─ (folder 없을 때) QTimer.singleShot(0, viewer.open_csv_folder)  → app.exec()
```

- **독립성**: 창 N개 = 프로세스 N개(메모리 N배). "한 번에 전부 닫기" 같은 공통 제어점 없음.
- **onefile 금지**: onedir라 압축해제 없어 기동 빠름. onefile은 매 실행 temp 압축해제로 느려짐 (이 단순 구조의 전제).
- ⚠ **windowed(`console=False`) 가드**: frozen 상태에서 `sys.stdout/stderr`가 `None`이라 `print()`가 크래시한다 → `setup_std_streams()`가 None이면 `devnull`로 대체. **이 가드 없이 print 추가 금지.**

---

## 주요 클래스

| 클래스 / 함수 | 파일 | 역할 |
|--------|------|------|
| `main()` | csv_viewer.py | 진입점. 인자 폴더면 즉시 열고, 없으면 빈 창 + 폴더 선택창(취소=빈 화면) |
| `ViewerWindow` | gui_viewer.py | CSV 목록 + 테이블 뷰어. cache 기반 다중 CSV 관리. **생성자=`(icon_path, csv_folder=None)`** (None=빈 상태) |
| `FilterHeaderView` | gui_header.py | 수평 헤더. 우클릭→열 필터 팝업, 열 마커(⋯) 페인트 |
| `MarkerVHeaderView` | gui_header.py | 수직 헤더. 행 마커(︙) 페인트. 숨김 트리거/펼침은 `ViewerWindow`가 처리 |
| `FilterWidget` | gui_filter.py | 체크박스 기반 필터 팝업창 |
| `GraphWindow` | gui_graph.py | 3D 궤적 그래프 창. **생성자=`(icon_path=None, parent=None)`** + `set_data(headers, rows, title)` |
| `CSVTableModel` | utils/table_model.py | QAbstractTableModel. rows + highlight_cells |
| `CSVFilterProxyModel` | utils/filter_model.py | 열 필터 + Δ 가상 열 + 행/열 숨기기. column_filters(소스 열 키), col_map 열 간접화, hidden_rows/cols(소스 좌표)+마커 섹션(⋯/︙) |
| `SearchModel` | utils/search_model.py | Ctrl+F 검색 + 하이라이트. 선택 영역 검색(scoped) 지원 |
| `CSVLoaderThread` | utils/csv_loader.py | 비동기 읽기(utf-8-sig→cp949 폴백). `pyqtSignal(str, object)`로 전달. 캐시 지문(`signature`=stat, `content_hash`=sha256)도 스레드에서 계산 |
| `view_state`(모듈) | utils/view_state.py | `<CSV폴더>\.viewer`(폴더당 1 JSON)에 사용자 입력만 저장/로드. 원자적 쓰기·머지, envelope/version 게이트 |
| `EditHistory` | utils/edit_history.py | Undo/Redo 스택. **CSV별 1개**(cache['history']), 상한 20. `Memento`(highlights/fd/widths/rows 4슬라이스, COW). export·복원 판단은 `ViewerWindow` |

### ViewerWindow 내부 구조

```
ViewerWindow(icon_path, csv_folder=None)   # None → 폴더 미선택 빈 상태
├── icon_path : GUI/res 리소스 경로 (진입점이 resource_dir()로 주입)
├── cache[csv_file_name] = {
│     'table_data'  : raw 2D list (헤더 + 데이터)
│     'table_model' : CSVFilterProxyModel └─ CSVTableModel
│     'last_view'   : (v_scroll, h_scroll)        ← CSV 전환 시 복원
│     'col_widths'/'row_heights' : {인덱스:크기} sparse  ← .viewer 영속
│     'signature'   : (size, mtime_ns)            ← 재진입 변경 감지 1차
│     'content_hash': sha256 hex                  ← size 같고 mtime만 다를 때 타이브레이커
│     'status'      : 'ok'|'empty'|'fail'
│     'history'     : EditHistory                 ← Undo/Redo(CSV별 독립)
│   }
├── FilterHeaderView / CSVLoaderThread(self.loader_threads) / SearchModel / EditHistory
```

CSV 위치는 3단계 독립 관리: `csv_folder_path`(상위 경로) + `csv_folder_name`(폴더명) + `csv_file_name`(현재 파일). 드래그&드롭/폴더버튼/rename은 `_load_folder`·`_rename_folder`.

---

## ⚠ 공통 함정 (반복적으로 발목 잡는 것 — 먼저 읽을 것)

1. **`selectedColumns()`/`selectedRows()`는 대용량에서 수 초 걸린다** (열/행 전체 선택 시 전 행 순회: ≈0.8~2.8s). **절대 쓰지 말고** `selectionModel().selection()`의 **range(QItemSelectionRange) 목록만** 분석해 구간 커버리지(`_spans_cover`)로 판정한다(0.1ms 이하). 적용처: `capture_scope`·`copy_selection`·`_full_selection_sections`·`_anchor_rowcol`.

2. **대용량 cross-thread 시그널은 `object`로 넘긴다.** `pyqtSignal(str, list)`는 PyQt가 중첩 리스트를 QVariantList로 변환·복사해 18만 행에서 수 초 GUI 프리즈. `pyqtSignal(str, object)`(PyQt_PyObject)는 참조 전달(변환 0). 단 emit 후 워커에서 그 데이터 수정 금지.

3. **셀/헤더 폰트는 `setFont`가 아니라 모델 `FontRole`/`paintSection`으로 줘야 한다.** `table_csv`에 스타일시트(`background:white`)가 걸려 `table_csv.setFont()`가 **셀 텍스트에 반영 안 됨**(QStyleSheetStyle 함정). → 일반 셀은 `filter_model.data()`의 `FontRole`, 헤더는 QHeaderView가 FontRole을 **아예 안 읽으므로** `paintSection`에서 painter에 폰트 주입. 세로 헤더(행 번호)도 같은 함정.

4. **헤더 eventFilter는 `header.viewport()`에 건다 — `header` 자체에 걸면 마우스 이벤트가 안 온다** (QHeaderView=QAbstractScrollArea). 검증도 `QTest.mousePress/Move/Release`로 실제 디스패치를 태워야 함(직접 호출 금지).

5. **18만 행 성능 패턴**: 행 루프 금지(`setDefaultSectionSize`로 O(1) 스케일) · 대량 resizeSection은 `setUpdatesEnabled(False)`로 repaint thrash 차단 · 헤더 repaint가 전 행 스캔하지 않게 `initStyleOptionForIndex` 오버라이드(아래 변경 이력).

---

## 빌드 (PyInstaller)

```powershell
& "...\sniff_env\python.exe" -m PyInstaller --noconfirm CSV_Viewer.spec
# 출력: dist\CSV Viewer\CSV Viewer.exe  (onedir, ~139MB — numpy(openblas)+pyqtgraph 포함)
```

- **onedir**(`COLLECT`) + **`console=False`**(windowed, 콘솔창 안 뜸). 리소스는 png·ico·**gif(스피너)** 포함.
- ⚠ **conda PyQt6 함정**: Qt 런타임 DLL(`Qt6Core/Gui/Widgets.dll`)이 `.pyd` 옆이 아니라 `<env>\Library\bin`에 있어 PyInstaller가 자동수집을 놓친다 → spec에서 `QT_NEEDED` DLL을 **명시적으로 `binaries`에 추가**. Qt 모듈 새로 쓰면(QtNetwork 등) `QT_NEEDED`에 추가.
- ⚠ **3D 그래프(GLViewWidget) 의존성** (빠지면 `Qt6Core.dll` 크래시 `0xc0000409`):
  - `QT_NEEDED`에 **`Qt6OpenGL`·`Qt6OpenGLWidgets`** 추가.
  - `hiddenimports`에 **`PyQt6.QtOpenGL`·`PyQt6.QtOpenGLWidgets`**.
  - PyOpenGL·pyqtgraph는 **PyInstaller 내장 hook**에 위임 — `collect_submodules`로 통째 끌어오지 말 것(과수집).
  - ⚠⚠ **numpy는 nomkl(openblas) 빌드 필수**: conda 기본 numpy는 Intel MKL 링크라 `mkl_*.dll` ~600MB가 딸려와 **750MB**로 폭증. `conda install -n sniff_env nomkl`로 openblas 교체 시 **139MB**. 환경 재설치 시 mkl numpy가 다시 깔리면 또 커짐.
- ⚠ **numpy 2.x `_core` 누락**(frozen 그래프 첫 클릭 시 `ModuleNotFoundError: numpy._core._exceptions` 크래시): PyInstaller 내장 numpy 훅이 numpy 1.x 시절이라 지연 import 순수 모듈을 못 잡음 → spec에서 `collect_submodules('numpy')`로 명시 수집(+`*.tests*`·`f2py`·`distutils`·`mypy_plugin` 등 빌드 전용 필터). `f2py`는 정적분석이 다시 끌어오니 `excludes`에도 추가. UPX 손상 예방 `upx_exclude=['*numpy*','_multiarray*.pyd','libopenblas*.dll','openblas*.dll']`.
- `excludes`로 scapy/tkinter/psutil/pandas/matplotlib 차단(numpy는 pyqtgraph 필수라 제외 안 함).

---

## 배포 (MSI 설치 파일)

WiX 기반 MSI. 정의/스크립트는 `installer/`, 상세는 `installer/README.md`.

```powershell
.\installer\build_msi.ps1          # dist 기반 MSI 빌드 → installer\out\CSV Viewer Setup.msi
.\installer\build_msi.ps1 -Rebuild # PyInstaller 재빌드부터
```

- per-machine 설치(`C:\Program Files\CSV Viewer`), 시스템 환경변수 `CSV_VIEWER_HOME`, 폴더/배경 우클릭 "Open with CSV Viewer(V)"(`"…\CSV Viewer.exe" "%V"`).
- **설치 상태별 3분기**: 미설치=설치 / 옛버전=업그레이드(`MajorUpgrade`) / 같은버전=제거토글(`SetProperty REMOVE=ALL`). 확인창 3종(`confirm_install/upgrade/uninstall.vbs`)이 조건으로 정확히 하나만 뜸. 완료 팝업은 `popup_*.vbs`(`/qn` 무인설치엔 안 뜸).
- ⚠ **WiX v5 고정**: v6/v7은 EULA 게이트가 빌드를 막음. wxs는 v4 네임스페이스라 v5에서 빌드됨(`build_msi.ps1`이 `--version 5.0.2`).
- ⚠ **ProductCode = 버전별 결정 GUID, UpgradeCode = 고정 불변**: `build_msi.ps1`이 wxs의 `Version`을 읽어 `New-DeterministicGuid(UpgradeCode, ver3)`로 ProductCode 산출·주입. **같은 버전→같은 ProductCode(토글), 다른 버전→다른 ProductCode(업그레이드)**.
  - ⚠ Windows Installer 버전 비교는 **앞 3자리만**(4번째 무시) → GUID도 `ver3`(major.minor.build)로 파생. **업데이트 배포 시 앞 3자리 중 하나를 올려야** 업그레이드됨(4번째만 올리면 토글).
  - ⚠ wxs Version 파싱은 **대소문자 구분(`-cmatch`)** — 안 그러면 XML 선언의 소문자 `version="1.0"`을 잡는 버그.
  - ⚠ 확인창 VBScript는 **함수형 + `Return="check"` + type 4102**(반환 2=취소). 완료 팝업은 `ignore`/4198. `ExecuteAction` 전에 스케줄해 No=아무 변경 없음.
- ⚠ **`build_msi.ps1`·`.vbs` 편집 시 UTF-8 BOM 유지 필수**(없으면 PowerShell 5.1이 cp949로 오독).

---

## 알려진 TODO / 미완성
- (현재 없음)
- ⚠ spec의 `console=True`는 numpy 진단용 임시일 수 있음 — 그래프 정상 확인 후 `False`로 원복할 것.

---

## 변경 이력 (최신이 위 — 시간순)

> 형식: **헤드라인** + 살아있는 ⚠ 함정만. 구현 서사·'사용자 합의'·검증 PASS 로그는 코드가 정답이라 생략함. 새 항목도 이 형식으로(헤드라인 한두 줄 + 함정), **항상 이 목록 맨 위에 추가**.

- **3D 그래프 축 가운데에 '선택 열 이름' 라벨(축 바깥쪽·축 방향으로 누움)**(gui_graph.py `_AxisTitleText`·`_add_axis_labels`, 상수 `AXIS_TITLE_OFFSET_FRAC`·`AXIS_TITLE_COLOR`·`AXIS_TITLE_FONT_PT`): 선택한 x/y/z 각 축의 **가운데(L/2)** 에 그 축 열의 헤더 이름을, **큐브 중심 반대 방향(바깥쪽)** 으로 `AXIS_TITLE_OFFSET_FRAC*L` 만큼 밀어 그린다.
  - ⚠⚠ **빌보드(카메라 향함) 대신 '축 방향으로 눕힘' = `_AxisTitleText`**(GLTextItem 서브클래스): 기본 GLTextItem 은 항상 화면 수평으로 나를 향해 돌지만, 이 클래스는 paint 에서 **투영된 축 방향의 화면 각도**(`atan2`, `pos`→`pos+axis_dir` 투영 변위)만큼 QPainter 를 회전해 글자를 축과 나란히 눕힌다 → 카메라를 돌리면 축을 따라 회전(예: z축이 세로면 글자도 세로로 누움, 정면뷰 검증 z=-90°·x=0°). 뒤집힘 방지로 각도를 **[-90°,90°]로 접음**. 글자는 앵커에 가운데 정렬. (`_TickText` 와 동일하게 `compute_projection` 은 paint 안에서만 유효 — offscreen 은 스택 수동 시드해야 각도 검증 가능.)
  - ⚠ **바깥방향 = 큐브 중심(center) → 축 중점(mid) 벡터를 정규화**(3D=대각, 2D=평면 안 수직, 자동으로 선택 부분공간에 머묾). **1축은 mid==center 라 0 벡터 → 폴백**(`fallback_out`: x·y=-z=화면 아래, z=-x=화면 왼쪽).
  - ⚠ **기존 끝점 x/y/z 한 글자 라벨은 그대로 유지**(축 방향 식별용) — 열 이름은 '추가'. 필요하면 후속으로 제거/대체 가능.
  - ⚠ 좌표·투영각 계산은 offscreen 로 확정(위치 + 정면뷰 z 세로/x 가로 각도). **글자 렌더·눕는 모양·눈금 겹침은 실Windows 육안검증**(GLTextItem/QPainter 는 offscreen 미렌더). `_labels` 에 append 라 `_clear_labels`·줌 `_retick` 에서 함께 정리/재생성.

- **3D 그래프 같은 축 재선택 시에도 min/max 범위 리셋**(gui_graph.py `_on_axis_column_changed`·combo_x/y/z 시그널): 사용자가 축 범위칸을 고친 뒤 **같은 축을 콤보에서 다시 골라도** 데이터 min/max 로 되돌아가게 함(전엔 안 됨).
  - ⚠⚠ **원인 = `currentIndexChanged` 는 인덱스가 실제로 바뀔 때만 온다** → 이미 선택된 항목 재선택엔 안 와 핸들러 미호출. **`activated`(사용자 선택이면 같은 항목 재선택에도 발생, 프로그램적 setCurrentIndex 엔 안 옴)를 함께 연결**해 재선택도 리셋. `_clear_axis`/버튼의 프로그램적 `setCurrentIndex(0)` 는 여전히 `currentIndexChanged` 로 처리(그대로 동작).
  - ⚠ **둘 다 오는 '다른 열' 선택의 이중 `_redraw` 방지 = dedup**: 핸들러가 `_axis_col[axis]`(현재 반영된 소스 열) + `_axis_range[axis]` 를 추적해, **열도 같고 범위도 이미 데이터 min/max 그대로면 early-return**. 순서 무관(currentIndexChanged→activated 순서에 의존 안 함). `_reset_ranges` 도 `_axis_col=-1` 로 리셋. 편집(`_on_range_edited`)은 `_axis_range` 만 바꾸고 `_axis_col` 은 그대로라 재선택이 dedup 에 안 걸림.

- **3D 그래프 창 껐다 켜면 락온/오버레이 초기화**(gui_graph.py `set_data`·`_hide_hover`·`_hide_lock`): GraphWindow 는 **인스턴스 1개 재사용**(`open_graph` 가 닫힌 창에 `set_data` 후 `show`)이라 이전 락온 앰버 링/라벨이 그대로 남던 버그. `set_data` 초입에서 `_hide_lock()`·`_hide_hover()` 명시 호출로 정리(콤보 (none)·범위·타임라인·재생·`_lock`=None 은 원래도 리셋).
  - ⚠⚠ **`_hide_*` 가드는 `isVisible()` 이 아니라 `isHidden()`**: 재오픈은 **창이 hidden 인 상태에서** `set_data` 를 타므로 `isVisible()` 가 False → `.hide()` 스킵 → 이후 `show()` 가 낡은 오버레이를 되살림. `isHidden()`(위젯 자신의 명시적 숨김상태, 조상 무관)으로 판정해야 실제로 숨겨진다. (GL 링은 `visible()` = GL 자체 플래그라 조상 무관, 그대로.)
  - ⚠ **`set_data` 가 `_lock=None` 을 `_redraw` 보다 먼저** 세팅해, `_redraw` 의 '축 없음' 분기가 부르는 `_release_lock` 의 `if self._lock is not None` 가드가 안 타 `_hide_lock` 이 호출되지 않던 것도 같은 증상 → `set_data` 에서 직접 숨김.

- **3D 그래프 호버/락온 정보의 시간·트랙 라벨 = 선택 열 이름**(gui_graph.py `_format_point_lines`·`_header_name`·`_update_hover` 선 히트): 오버레이의 고정 라벨 `t:`/`Track:` 을 각각 combo_time/combo_track 에서 고른 **실제 열 헤더 이름**으로(예: `seq: 1`, `grp: A`). ⚠ 시간 열은 `_time_active` 여도 파싱 실패 시 행 인덱스 폴백이지만 그 경우도 열은 선택돼 있어(`_combo_col(combo_time) >= 0`) 헤더 이름 유효. 트랙은 `value is not None`(트랙 열 선택됨)일 때만 라벨링 → 열 인덱스도 `>= 0`. 둘 다 안전장치로 인덱스 `< 0`이면 옛 `t`/`Track` 폴백. 축(x/y/z) 라벨은 원래부터 헤더 이름이라 그대로.

- **3D 그래프 락온 중에도 호버 정보 동시 표시**(gui_graph.py): 락온(앰버) 유지 중에도 다른 점에 마우스를 올리면 예전처럼 **흰색(회색) 호버 오버레이가 그대로 뜬다**. 다른 점 클릭 시 락온 이동은 기존 `_handle_click` 그대로.
  - ⚠⚠ **호버와 락온이 GL 링·오버레이 라벨을 공유하던 것을 분리**: 옛 구조는 `_hover_ring`/`_hover_label` **하나**를 둘이 돌려써서 `_update_hover` 초입에 `if self._lock is not None: return` 가드가 있었음(락 중 호버 죽음). → **락온 전용 `_lock_ring`(앰버)·`_lock_label`(앰버 QSS 고정) 신설**해 호버(`_hover_ring`·`_hover_label` 회색)와 별개로 동시에 뜨게 함. 그 가드 제거.
  - ⚠ **순서 = 흰색 호버가 앰버 락온보다 위**(사용자 지정): GL 링 depthValue **호버 12 > 락온 11 > 마커 10** / QLabel z-order 은 `_show_overlay` 에서 호버=`raise_()`(맨 위), 락온=`stackUnder(self._hover_label)`(호버 아래) — 갱신 순서(호버=mouseMove, 락온=frameSwapped)와 무관하게 호버가 항상 위.
  - ⚠ **hide 경로도 분리**: `_hide_hover`=호버 위젯만 / `_hide_lock`=락 위젯만. `_release_lock`·`_refresh_lock`의 마커부재/범위밖 숨김은 `_hide_lock`. `_gl_leave`(커서 이탈)는 **항상 `_hide_hover`**(락 오버레이는 점에 고정돼 유지). `_show_overlay(locked=)`는 이제 QSS를 스왑하지 않고 **라벨만 선택**(각 라벨 QSS는 생성 시 고정, `_overlay_locked_style` 폐기).
  - ⚠ **이미지 캡처 합성 대상에 `_lock_label` 추가**(`_grab_graph_image` — 링은 GL이라 자동 캡처, 라벨만 painter 합성). 링 렌더·투영 위치·실시간 피킹은 offscreen 미렌더 → **실Windows 육안검증**(락온+호버 동시 표시, 다른 점 클릭 시 락 이동).

- **3D 그래프 `button_time_play`(▶/⏸) 글자 중앙정렬**(widget_graph.ui 스타일시트): 재생/일시정지 글리프가 21×21 버튼 안에서 치우쳐 보여 QPushButton 기본 규칙에 `text-align: center; padding: 0px;` 추가(상하·좌우 중앙). ⚠ Qt 스타일시트 `text-align: center` = AlignCenter(수평+수직). ⚠ 이 스타일은 Designer 재저장 시 사라질 수 있음(위 트랙 검색창 항목의 함정 참조).

- **3D 그래프 트랙 목록에 (Select All) + 검색창**(dialog_filter 유사; gui_graph.py `_build_track_list`·`_refresh_track_master`·`_track_master_clicked`·`_filter_track_items`·`_visible_track_checkboxes`, widget_graph.ui `lineEdit_track_search`): 트랙 목록을 필터 팝업처럼 **(Select All) 3-state 마스터 + 이름 검색창**으로. 색버튼은 기존대로.
  - ⚠ **즉시 반영 — Apply/Close 없음**(사용자 합의): 필터 팝업은 Apply 로 모아 반영하지만 트랙 목록은 상시 패널이라 체크/색 변경 즉시 `_redraw`. (Select All)·검색만 가져오고 Apply/Close/Clear 는 안 만듦.
  - ⚠ **검색 = '목록 좁히기'지 필터 아님**: 검색으로 숨긴 행은 `_row.setVisible(False)`(목록에서만 감춤), 트랙의 그래프 가시성(`_track_visible`)은 안 건드림(dialog_filter 와 동일). 마스터·전체토글은 **'보이는(검색 통과)' 항목만** 대상(`_visible_track_checkboxes` = `not cb._row.isHidden()`).
  - ⚠ **마스터 = `clicked`(사용자 클릭만)** — 3-state 표시 갱신(`setCheckState`)은 blockSignals(재귀 방지). 전체 토글은 개별 체크박스 blockSignals 후 **마지막에 `_redraw` 1회**(N번 방지). 마스터는 트랙 목록 있을 때만 생성 → 트랙 열 없음·MAX_TRACKS 초과 폴백 시 `_track_master_cb=None` + 검색창 disable. 새 트랙 열 rebuild 마다 검색어 초기화(blockSignals).
  - ⚠⚠ **빌드 직후 마스터 초기 Checked 는 `_refresh_track_master()` 로 계산시키면 안 되고 `setCheckState(Checked)` 로 직접 세팅**: `_build_track_list` 시점의 행들은 아직 show 전이라 `_row.isHidden()` 이 **일시적으로 True**(창이 이미 떠 있는 상태에서 combo_track 변경 시 재현) → `_visible_track_checkboxes()` 가 빈 목록 → Unchecked 로 오판. offscreen 미표시 테스트에선 isHidden 이 False라 안 걸리니 **`show()` 후 검증**해야 재현됨. 개별 토글·검색 시점의 `_refresh_track_master` 는 창이 안정돼 있어 isHidden 신뢰 가능.
  - ⚠ **`button_track`/`button_time` = 그 콤보를 '(none)'(index 0)으로 초기화**(button_x/y/z `_clear_axis` 와 동일 패턴, `setCurrentIndex(0)` 이 currentIndexChanged 태워 목록/타임라인 리셋; 이미 0이면 무동작). **combo_time 첫 항목 라벨 `(row #)` → `(none)`** 로 통일(x/y/z/track 과 동일 표기, 의미는 그대로 col<0=시간 비활성=전체).
  - ⚠ **widget_graph.ui 에 QLineEdit `lineEdit_track_search` 추가 → pyuic6 재생성**(생성본 수기수정 금지). clearButtonEnabled·textChanged 배선은 gui_graph 에. 외형(검색창·마스터 배치)은 offscreen 미렌더 → 실Windows 육안검증. ⚠ 검색창 위젯명/배치는 사용자가 Designer 로 직접 편집 → **Designer 재저장이 코드측 스타일 수기수정(예: button_time_play `text-align:center`)을 되돌릴 수 있으니** .ui 편집 후엔 재생성·코드 참조명 재확인 필요.

- **3D 그래프 눈금 점을 축선 위에 그림**(gui_graph.py `_tick_dots` 생성부): 눈금 점이 같은 위치의 축선(GLLinePlotItem)에 가려지지 않게 마커와 동일한 `_ON_TOP_GL`(깊이검사 끔) + `setDepthValue(5)` 적용. ⚠ **순서 = 축선·데이터(depthValue 0) 위, 마커(10)·호버링(11) 아래**(마커/호버 강조는 눈금 점보다 위에 유지). 모든 x/y/z 눈금 점에 공통 적용.

- **3D 그래프 0 눈금 강조**(gui_graph.py `_add_axis_labels`·`_rebuild_tick_dots`): 부호가 넘어가는 축(또는 여백에 0이 뜨는 축)에서 원점 눈금이 다른 회색 눈금 사이에 묻히지 않게 **라벨=밝은 흰색(`ZERO_LABEL_COLOR`=240)**, **점=크게(`ZERO_DOT_SIZE`=8, 일반 5)+흰색(`ZERO_DOT_COLOR`)**. 판정=`val == 0`(nice_ticks 값이 step 배수라 정확히 0.0, eps 불필요). ⚠ **볼드는 시도했다 취소**(사용자 피드백: 볼드체 별로) — 강조는 색·크기만. ⚠ **`_rebuild_tick_dots` 는 이제 점별 color/size 배열**(`np.array(colors)`/`np.array(sizes)`)로 setData — 옛 스칼라 `size=`+`np.tile(color)` 아님(0 점만 다르게 주려면 점별 배열 필수). ⚠ **색 대비는 offscreen 미렌더 → 실Windows 육안 검증**(강조가 부족하면 후속으로 0-교차 격자선 추가 예정). re-center(축을 0중심 정규화)는 매핑이 호버/gizmo/마커/범위편집에 얽혀 리스크·공간낭비라 채택 안 함.

- **3D 그래프 타임스탬프에 진행률(%) 추가**(gui_graph.py `_update_timestamp`): `plainTextEdit_timestamp` 를 `현재 / 총 (진행%)` 형식으로(예: `13:00:50 / 13:30:50 (30%)`). ⚠ **진행% 는 시각(_current_sample_time) 비율이 아니라 슬라이더(bar_time) 위치 기준** = `round(100 * value / maximum)` → 슬라이더 해상도(SLIDER_STEPS=1000)라 항상 정수로 딱 떨어짐. 시각 비율로 하면 시각 간격이 불균등해 정수로 안 떨어짐. 시간 비활성이면 빈칸(기존과 동일).

- **ESC 연타 닫기 = `EscCloseToast`(gui_esc.py)로 분리해 ViewerWindow·GraphWindow 공유**: 첫 ESC=화면 가운데 둥근 안내 토스트, 간격 내 재-ESC=`host.close()`. 이전엔 `ViewerWindow`에 인라인(`widget_esc`/`_style_esc_toast`/`show_esc_message`/`last_esc_time`/`ESC_INTERVAL_SEC`)이던 것을 통째로 새 클래스로 이동. 사용법=`self.esc_toast = EscCloseToast(self)` + ESC 트리거에서 `self.esc_toast.handle_esc()`. 상수는 `EscCloseToast.INTERVAL_SEC`(옛 `ViewerWindow.ESC_INTERVAL_SEC` 폐기).
  - ⚠⚠ **GraphWindow 는 ESC 를 `keyPressEvent` 로 잡으면 안 된다 → `QShortcut`(Ctrl+S/Ctrl+C 와 동일 패턴)**: 그래프 창은 `graph_area`(GLViewWidget)/콤보/`lineEdit` 등 포커스 가능한 자식이 ESC 를 먼저 먹어 top-level 창의 keyPressEvent 까지 안 올라온다(그래서 처음 keyPressEvent 로 넣었더니 '전혀 작동 안 함'). `QShortcut(QKeySequence(Qt.Key.Key_Escape), self)`(WindowShortcut=창 활성 시 포커스 무관) 로 잡아야 동작. ViewerWindow 는 QMainWindow 라 기존 keyPressEvent 그대로(검색창 열림 여부로 분기).
  - ⚠ **콜드스타트 유지**: `gui_esc` 는 `widget_esc`(경량 PyQt)만 import → gui_viewer 가 gui_esc 를 top-import 해도 pyqtgraph/numpy 안 딸려옴(검증: `import GUI.gui_viewer` 후 `sys.modules` 에 없음). GraphWindow 지연 import 전제도 그대로.
  - ⚠ **연타 유효 간격 = 토스트 노출 시간**(둘을 `INTERVAL_SEC` 하나로 의도적으로 묶음, `int(INTERVAL_SEC*1000)`ms). 독립값으로 분리하지 말 것. 토스트 스타일(둥근 알약/그림자)은 offscreen 미렌더라 실Windows 육안검증.

- **3D 그래프 방향 gizmo = 축 1개 이상 선택 시에만 표시**(gui_graph.py `_update_gizmo_visibility`): x/y/z 전부 '(none)'이면 gizmo 숨김, 1개라도 고르면 표시. 토글 단일 출처 = `_redraw`(모든 선택 변경·`set_data`가 거치는 중앙 통로, `_axis_cols` 계산 직후 호출). ⚠ **초기화도 가려야** 하므로 `_setup_gizmo` 끝의 `show()` 를 `_update_gizmo_visibility()`(전부 none→숨김)로 교체 — 시작 시 데이터 없어 gizmo 가 뜨던 것 방지. gizmo 숨김 중엔 `frameSwapped→update()` 가 자연 무동작(Qt 가 hidden 위젯 repaint 안 함).

- **3D 그래프 `button_x/y/z` = 그 축 선택 초기화**(gui_graph.py `_clear_axis`): x/y/z 버튼 클릭 시 해당 축 콤보를 '(none)'(index 0)으로 되돌림. `setCurrentIndex(0)` 이 `currentIndexChanged`→`_on_axis_column_changed` 를 태워 **락 해제·범위 편집칸 비움·재그리기까지 자동 처리**(별도 정리 코드 불필요). 이미 '(none)'이면 시그널이 안 와 무동작(올바름). 버튼은 이미 `widget_graph.ui` 에 존재(텍스트 x/y/z).

- **3D 그래프 Ctrl+휠 줌에서도 눈금 세분되게 수정**(gui_graph.py `_apply_tick_density`): ⚠⚠ **pyqtgraph `GLViewWidget.wheelEvent` 은 Ctrl+휠이면 `distance`(거리)가 아니라 `fov`(시야각)를 줄여 줌한다** → 거리 비율(`base_distance/cur`)만 보던 눈금 세분이 Ctrl 줌에선 배율 1로 남아 안 먹혔음. **줌 배율 = 거리 줌 × fov 줌**으로 합산(`tan(base_fov/2)/tan(cur_fov/2)`, 원근투영의 화면배율은 tan(fov/2)에 반비례)해 양쪽 다 반영. `_base_fov`(기본 60°)는 `__init__`에서 캡처, `_frame_scene`이 거리 리셋과 대칭으로 `opts['fov']`도 `_base_fov`로 복귀(시점초기화/구조 재그리기 시 Ctrl 줌도 풀림). 클램프는 그대로 [1,2] 2단계. **육안 눈금 밀도는 실Windows 검증**.

- **메인 뷰어 `button_graph` 에 3D 축 아이콘 적용**(gui_viewer.py): `GUI/res/button_graph.png`(3D x/y/z 축+큐브) 을 `setIcon` + `setIconSize(QSize(23,18))` 로 입힘. ⚠ **원본 png 가 정사각(669x639)이라 default iconSize 로는 작게 찍힘** → 버튼 내부(27x21, 테두리 1px 제외)를 채우도록 iconSize 명시. 다른 작은 버튼(reset/folder/undo)은 iconSize 없이 setIcon 만 쓰지만 이 아이콘은 디테일이 많아 예외. **육안 크기감은 실Windows 검증**.

- **3D 그래프 화면 저장(Ctrl+S)/복사(Ctrl+C)**(gui_graph.py): 그래프 창에서 **현재 graph_area 화면 그대로** PNG 저장(파일 선택창)·클립보드 복사. `_grab_graph_image`(캡처)·`save_graph_image`·`copy_graph_image`·`_show_toast`(코드 생성 알림). 단축키는 `QShortcut`(WindowShortcut)이라 **별도 top-level 창이라 메인 뷰어 Ctrl+S/Ctrl+C 와 충돌 없음**.
  - ⚠ **저장 기본 파일명 = `<폴더명>-<csv명>.png`**(예: `250503_124533-EIE_0x0306.png`), **저장창 시작 경로 = 그 CSV 폴더**. `open_graph` 가 `set_data(headers, rows, csv_file_name, _folder())` 로 폴더 전체경로를 넘기고, GraphWindow 가 `self._folder_path`(시작경로)와 `os.path.basename`(폴더명 접두)로 분해해 씀. 폴더명·csv명 빈 조각은 빼 트레일링 `-` 방지(`"-".join(parts)`), 파일명 금지문자 치환. 폴더경로 없거나 부재면 파일명만(cwd 시작).
  - ⚠ **GL 씬은 `graph_area.grabFramebuffer()`(QOpenGLWidget) 로만 캡처된다 — `QWidget.grab()`/`self.grab()` 은 GL 내용이 검게 빠질 수 있음**. grabFramebuffer 는 **GL 아이템(궤적·점·마커·축/눈금 GLTextItem)만** 잡고, graph_area 위 오버레이(방향 gizmo·호버 라벨)는 GL 이 아니라 별도 렌더라 **빠진다 → grab 위에 `_gizmo`·`_hover_label` 만 painter 로 합성**해야 화면과 같아짐. **시점초기화 버튼은 합성 제외**(배경 alpha=0 라 화면상 안 보임 — grab 하면 불투명 박스로 찍혀 오히려 화면과 달라짐).
  - ⚠ **DPR 처리 = 반환 이미지 실제배율로 박기**: grabFramebuffer 는 device px(고DPI) 라 `scale = img.width()/area.width()` 를 `img.setDevicePixelRatio(scale)` 로 박아 둬야 이후 painter 가 **논리좌표(=자식 위젯 `pos()`)** 로 정확히 합성된다(Qt 버전 따라 grab 이 DPR 을 안 박는 경우 대비).
  - ⚠ **offscreen 은 실제 GL 컨텍스트가 없어 grabFramebuffer 가 null QImage** → 캡처 검증은 실Windows 육안(저장 PNG/붙여넣기에 **축 눈금·gizmo 까지** 나오는지 확인). 코드는 null 가드(`img.isNull()`→실패 토스트)로 offscreen 에서도 크래시 없음.
  - ⚠ **편집 가능한 `lineEdit_*` (축 범위칸)에 포커스 중 Ctrl+C 는 텍스트 복사**(QLineEdit 의 ShortcutOverride)로 이미지 복사 안 됨 — 의도된 동작(그 외 포커스에선 이미지 복사).

- **3D 그래프 마커/점 클릭 락온(고정 전시 + 추종)**(gui_graph.py): 마커(또는 점)를 클릭하면 그 대상에 **락온**되어 마우스를 떼도 오버레이가 점에 붙어 계속 뜬다. **빈 곳 클릭=해제**, 다른 마커 클릭=전환. 락 표시는 **앰버 링/테두리**(호버=흰색과 구분). `_handle_click`·`_refresh_lock`·`_release_lock`·`_project_one`(단일점 투영)·`_norm_one`(단일행 정규화)·`mousePress/Release` 인스턴스 교체.
  - ⚠ **락 2모드**: 자취(시간 활성)=**`marker`**(트랙 *현재 마커*를 추종 → 재생/스크럽으로 마커가 다음 행으로 가면 오버레이도 그 행으로 갱신) / 전체(시간 비활성)=**`point`**(클릭한 행 고정, 카메라 회전 시 재투영해 점에 붙어 따라감). 마커 행 식별 = `_stack_rows`(살아남은 원본행 반환, 마커=솔리드 prefix 마지막 유한점) → 매 draw 시 `_marker_info[track]=(좌표,행)` 갱신.
  - ⚠ **클릭 vs 드래그(회전) 구분 = press~release 이동량(`CLICK_MOVE_TOL2`)**: `mouseMoveEvent` 에서 버튼 눌린 채 임계 초과 이동하면 `_press_moved=True` → release 때 클릭으로 안 침. (GLViewWidget 은 좌드래그가 orbit 이라 클릭만 골라내야 함.)
  - ⚠⚠ **`frameSwapped → _refresh_lock` 무한루프 방지**: 카메라가 돌면 오버레이를 재투영해 따라가야 하는데, **링 `setData` 는 좌표가 바뀔 때만**(`_lock_pt_cache` 비교) 호출 — `setData`→GL repaint→`frameSwapped`→재진입 루프를 막는다. 링은 GL 아이템이라 카메라 변화엔 **자동 추종**(setData 불필요), 회전 시엔 **오버레이 QLabel 만 `move`**(QWidget move 는 paintGL 을 안 불러 안전). 추종 갱신처 = 매 draw(`_draw_lines_and_markers` 끝) + `frameSwapped`.
  - ⚠ **락온 중엔 호버 무시**(`_update_hover` 초입 가드), **leave 해도 락 오버레이 유지**(`_gl_leave` 가 락이면 안 숨김). 구조 변경(축/시간/트랙 열 변경·새 CSV·축 전부 해제)은 `_release_lock`. 마커가 일시적으로 없으면(미등장/트랙 숨김) 표시만 숨기고 **락은 유지**(다시 나타나면 재표시). **링/오버레이 외형·커서 정렬은 실Windows 육안검증**.

- **3D 그래프 마우스 호버 정보(점=좌표/트랙/시간/행, 선=트랙명)**(gui_graph.py): `graph_area` 위에서 마우스를 데이터 점/마커에 올리면 다크 오버레이 라벨에 그 점의 **원본 x/y/z 값 + 트랙명 + 시간 + 행번호**가 뜨고 점에 강조 헤일로 링(`_hover_ring`, `_ON_TOP_GL`)이 붙는다. 점이 아닌 선분 위면 **트랙명만**. `_update_hover`(호버 핸들러)·`_ensure_hover_index`(피킹 인덱스)·`_project_screen`(투영)·`_nearest_segment`(선 거리)·`_show_point_hover`/`_show_overlay`(전시).
  - ⚠⚠ **GLViewWidget 은 3D 피킹이 없다 → '화면 투영 + 최근접'으로 직접 판정**: 그려진 점들을 현재 카메라로 화면픽셀에 투영해 커서와의 거리 `argmin`(점 `PICK_RADIUS_PT`=13px) / 점-선분 거리(선 `PICK_RADIUS_LINE`=8px). **점마다 `QMatrix4x4.map()` 루프 금지**(18만 점 프리즈) → 행렬 1개를 numpy 로 일괄 적용.
  - ⚠⚠ **투영 행렬 = `_marker_label.compute_projection()`(= `ndc_to_viewport × mvpMatrix`, world→논리 화면픽셀)**. `QMatrix4x4.data()` 는 **열우선(OpenGL)** 이라 `reshape(4,4)` 가 (표준행렬)ᵀ → 행벡터 점들에 대해 **`pts_h @ data` 가 곧 M·p**(전치 불필요). `map()` 과 3.7e-5px 일치(offscreen 행렬검증). 카메라 앞만(`w>1e-6`).
  - ⚠⚠ **`compute_projection` 의 projection/modelview 스택은 `paintGL` 에서 `clear()+append()`(pop 안 함)** → **첫 paint 이후엔 paint 밖(호버=마우스이벤트 핸들러)에서도 `currentProjection/currentModelView` 가 유효**(이 설계의 전제). **offscreen 은 실제 paintGL 이 안 돌아 스택이 비어 `IndexError`** → 단위테스트는 `graph_area.setProjection(region,region)`+`setModelview()` 로 paint 와 동일하게 수동 시드해야 투영 검증 가능. (런타임 첫 호버가 paint 보다 빠른 희박한 경우는 try/except 로 None→숨김.)
  - ⚠ **좌표계는 둘 다 '논리 픽셀'이라 DPR 일치**: `compute_projection` 은 `view().rect()`(논리), `ev.position()` 도 논리. projection 은 aspect-only 라 DPR 무관. → 커서와 투영점이 같은 좌표계.
  - ⚠ **성능 = lazy/dirty**: `_draw_lines_and_markers` 는 그린 트랙별 idx(`_drawn_tracks`, prefix 는 view 라 무복사)만 기록하고 `_hover_dirty=True` 만 세움 → 피킹 인덱스(`_hover_pts/rows/tid`·세그먼트 `_seg_*`)는 **첫 호버 때 1회**(`_ensure_hover_index`) 재구성 → 재생/스크럽 핫패스 비용 0. 투영·점선거리 전부 벡터화. (드래그=버튼 눌림 중엔 호버 억제.)
  - ⚠ **호버 대상은 '솔리드로 그린' 점/선만**(ghost=미도달 전체경로 제외). 마커(현재 위치)도 솔리드 prefix 의 마지막 점이라 호버됨. 단일 궤적(트랙 열 없음)은 선 히트 시 표시할 트랙명이 없어 숨김. 세그먼트는 트랙 내부 연속쌍만(트랙 경계 안 이음).
  - ⚠ **GL 아이템 가시성은 `visible()`**(QWidget `isVisible()` 아님 — `_hover_ring` 등). 오버레이 라벨·링은 **코드로만 생성**(`.ui` 무수정, gizmo 패턴), 라벨은 `WA_TransparentForMouseEvents`(호버 자체를 안 가림). `mouseMoveEvent`/`leaveEvent` 인스턴스 교체 + `setMouseTracking(True)`(버튼 없이 이동 이벤트). **링 렌더·오버레이 외형·커서 정렬은 실Windows 육안검증**(offscreen 미렌더).

- **3D 그래프 카메라 방향 gizmo(좌상단 `_AxisGizmo` 오버레이)**: reset 버튼 자리에 실제 그래프 축과 동일한 각도로 도는 작은 x(빨강)/y(초록)/z(파랑) 축 표시기.
  - ⚠⚠ **원근 투영(perspective)으로 그린다 — 정사영(`mapVector`)이 아님**: 씬과 동일한 `projectionMatrix(viewport) × viewMatrix` 로 world 원점(0,0,0)→각 축끝(+`AXIS_LEN`)을 화면에 투영하고, 그 화면 변위(`tip−origin`)를 코너에 축소(`_project_axes`). **회전만 쓰면(`mapVector`) '축이 화면 어디에 있느냐'에 따른 원근 전단(shear)을 놓쳐 실제 축과 ≈10~12° 어긋난다** — 씬의 축은 화면 중앙이 아니라 큐브 코너(원점, center=(L/2,…)에서 벗어남)에서 뻗으므로 foreshortening 이 각도를 바꾼다. 같은 파이프라인이라 gizmo 각도/원근단축이 씬과 **구조적으로 일치**(offscreen 행렬검증: gizmo 각도 == origin→L 씬 축 각도). 길이는 '가장 긴 축=반경 R' 공통배율로 축소해 상대 단축 보존.
  - ⚠⚠ **실시간 추종 = `graph_area.frameSwapped` → `_gizmo.update`**(매 GL 프레임 swap 후): 카메라가 바뀌면 GL 이 반드시 재렌더하므로 드래그/휠/`setCameraPosition` 등 **출처와 무관하게 항상 화면과 동일 카메라**로 다시 그린다. **마우스 이벤트만 후킹하면 일부 경로에서 갱신을 놓쳐 gizmo 가 옛 각도에 멈춤**. `gizmo.update()` 는 자식 위젯 repaint 라 `paintGL` 을 안 불러 재렌더 루프 없음. (`GLViewWidget`=`QOpenGLWidget` 이라 `frameSwapped` 보유.)
  - ⚠ **앞/뒤 페이드는 위치무관 축방향 eye z**(`mapVector(축).z()`, 음수=뒤로 향함 → 알파 흐리게 + 앞축이 위로 오게 depth 정렬). 원근투영의 화면각도와 분리(페이드는 부호만 필요).
  - ⚠ **클릭은 gizmo 가 아니라 아래 버튼이 받는다**: gizmo 는 `WA_TransparentForMouseEvents` 마우스-투과 + `raise_()`(위라 항상 보임), 버튼을 gizmo 크기(31→58px)로 `setGeometry` 키워 전 영역 클릭=시점초기화. **geometry 는 코드로만 조정**(`_setup_gizmo`) → `ui/` 생성본은 손대지 않음. **선/폰트 렌더는 실Windows 육안검증**(offscreen 미렌더).
- **3D 그래프 시점 초기화 버튼(`button_graph_reset`)**: 좌상단(`graph_area` 자식 오버레이) 버튼 → `_reset_view` 가 현재 선택 축 기준 **초기 시점**(각도 `_view_for`·기본 거리 `_base_distance`·큐브 중심)으로 카메라 복귀(회전/휠 줌/팬 해제). 시간·자취·트랙 선택은 유지. ⚠ **`_last_sel=None` 후 `_redraw`** 호출해야 같은 축 조합이어도 각도가 강제 재설정됨(`_frame_scene` 은 `sel` 이 바뀔 때만 각도 적용). 텍스트/아이콘은 추후 결정(현재 빈 텍스트). `.ui` 에 버튼 추가 후 `_ui_py_generate.py`/pyuic6 로 `widget_graph.py` 재생성(생성본 수기수정 금지).

- **3D 그래프 시간축 재설계: 공유 타임라인 + 트랙별 자취(trail) + 재생**(gui_graph.py): combo_time 열을 고르면 **자취 모드**(각 트랙을 시간순 정렬해 시각 T 까지의 prefix 만 그리고, **트랙별 색 마커**가 같은 T 에 동기화돼 이동), `(row #)`/미선택이면 **전체 모드**(궤적 전부·마커/재생 없음). ▶ 재생(`button_time_play` 토글)+배속(`button_time_speed` 0.5~4×)을 QTimer 로 구동. 현재 시각은 `plainTextEdit_timestamp` 에 '현재/총' 표시(옛 `lineEdit_time` 은 UI 에서 삭제됨).
  - ⚠ **표시 모드는 토글 위젯이 아니라 'time 열 선택 여부'로 결정**(자취 vs 전체). 꼬리(window) 모드는 일부러 없음(사용자 합의).
  - ⚠ **자취 모드 = 전체 경로 ghost + 도달 구간 솔리드 2겹**: 미도달 부분도 형태가 보이도록 트랙 전체 경로를 **흐린 색**(`_ghost_color`=배경↔트랙색 `GHOST_FRACTION` 보간, 가는 선 `_add_ghost_line`, 점 없음)으로 먼저 깔고 그 위에 T 까지의 자취(진한 색+점)+마커를 얹는다. 첫 표본 시각보다 T 가 이른 트랙은 **ghost 만**(staggered 등장). 프레이밍(`all_pts`)은 trail prefix 가 아니라 **전체 경로 기준**이라 재생 중 카메라가 안 흔들림. (ghost 도 매 틱 재생성 → 큰 데이터 시 비용 2배, 위 성능 메모 참조.)
  - ⚠ **시간 파서 `parse_time_value`(순수함수, offscreen 테스트 가능)**: 순수 숫자(ms 카운트)·`H:M:S(.f)`·`M:S(.f)`·날짜+시간(`:` 토큰만)·뒤따르는 `;`/`,`/공백 모두 허용 → float(초). 열이 파싱되면 실제 시간값 타임라인, 열을 골랐지만 파싱 실패면 **행 인덱스 폴백**(여전히 자취), `(row #)`면 시간 비활성(전체). 시계표기 여부(`_col_looks_clock`)로 타임스탬프 포맷 분기(`format_clock`).
  - ⚠ **표시 시각은 '실제 표본 시각'으로 스냅**(보간 금지): 슬라이더는 `0..STEPS` 를 `[t0,t1]` 연속 T 로 매핑하는데 이 T 는 데이터에 없는 보간값 → `_current_time`(연속, **내부용** 자취 cutoff/마커 frontier)과 `_current_sample_time`(전 트랙 합친 실제 표본 시각 `_time_sorted_all` 에서 `searchsorted` 로 T 이하 최근값) 을 분리. 타임스탬프 표시는 **반드시 후자**(`_current_time` 을 표시에 쓰면 `00:04.750` 같은 가짜 시각이 찍힘).
  - ⚠ **자취 prefix = `np.searchsorted` O(log n)**: 트랙×시간 정렬 캐시(`_track_sorted`/`_track_sorted_times`, `_rebuild_track_order`)를 **트랙/시간 열 바뀔 때만** 갱신. `argsort(kind='stable')` 라 NaN 은 끝으로 가 자취에서 자연 제외. 마커=prefix 마지막 유한 점. 트랙 첫 표본 시각보다 T 가 이르면(k≤0) 그 트랙은 아직 안 나타남(staggered 등장).
  - ⚠ **재생/스크럽 핫패스 = `_draw_lines_and_markers`(선·마커만)** — 축·그리드·카메라는 안 건드림. 구조 변경(축·범위·트랙·시간 열·새 CSV)만 `_redraw`(=`_draw_lines_and_markers`+`_frame_scene`). 슬라이더 `0..SLIDER_STEPS(1000)` 분수로 T 매핑, 재생은 `_pos` float 누산기(정수 슬라이더 끊김 방지; **`sliderMoved` 로 사용자 드래그만** 누산기 동기화, 프로그램적 `setValue` 는 제외).
  - ⚠ **마커 = 트랙별 색**(옛 노랑 단일 `MARKER_COLOR` 폐기). `GLScatterPlotItem` 1개에 N점(트랙 수). 다중 트랙이라 3D 좌표 라벨은 생략(현재 시각은 `plainTextEdit_timestamp` 로).
  - ⚠ **축/그리드는 그릴 점이 없어도 유지**: `_frame_scene` 은 `sel`(선택 축)+축 범위(정규화 큐브)로만 축·그리드·눈금·카메라를 그린다 → 트랙 전부 해제·전부 NaN·재생 시작 전이라도 축/그리드가 남는다. (옛 `if not all_pts: return` 가드가 이걸 통째로 숨겨 버그였음 → 제거, `all_pts` 파라미터도 삭제. 마커 표시/숨김은 `_update_markers` 가 단독 책임.)
  - ⚠ **데이터 점이 흰색으로 번지던 버그 = `GLScatterPlotItem` 기본 블렌딩이 가산(additive, `GL_SRC_ALPHA,GL_ONE`)**: 어두운 배경서 인접 점이 겹치며 밝기 누적 → 중심이 흰색. 해결=`_add_line` 의 점에 `setGLOptions('translucent')` + 색을 `_dot_color`(트랙색×`DOT_DARKEN=0.62`)로 살짝 어둡게. (선도 기본 additive 지만 얇아서 티 덜 남 — 점만 손봄.)
  - ⚠ **위젯은 `widget_graph.ui` 에 사용자가 추가**(`button_time_play`/`button_time_speed`/`plainTextEdit_timestamp`) → `ui/` 생성본 직접수정 금지, 로직만 gui_graph 에. 큰 데이터(18만 행) 단일 트랙 재생 시 매 틱 prefix `_stack` 비용이 큼 — 필요 시 축/시간 시그니처 키로 정규화 점 캐시 도입 여지(현재 미적용).

- **3D 그래프: 각 데이터 포인트에 작은 점 표시**(gui_graph.py `_add_line`): 궤적 선(`line_strip`)과 함께 같은 색 `GLScatterPlotItem`(`POINT_SIZE=3.5`px, 선폭 2.0보다 약간 큼)을 찍는다. ⚠ **이유 = 트랙 데이터가 1개뿐이면 line_strip 이 아무것도 안 그려 아예 안 보임**. 점은 `_line_items`에 같이 넣어 `_clear_lines`가 함께 지움.

- **빌드 그래프 크래시: numpy 2.x `_core` 누락** → 위 "빌드" 섹션에 통합(`collect_submodules('numpy')` + UPX 제외). frozen 그래프 첫 클릭 시 `ModuleNotFoundError: numpy._core._exceptions` 크래시였음.

- **3D 그래프 축 범위 편집칸 + nice-number 눈금 + 축별 독립 정규화** (gui_graph.py): 우측 패널 축마다 `lineEdit_{x,y,z}_{min,max}` 6개. 축 선택 시 데이터 min/max 자동 채움, `editingFinished`에서만 반영(숫자 아님/`min≥max`→직전 값 복원).
  - ⚠ **축 정규화 = 축별 독립**(등비율 폐기): 각 축을 자기 `[min,max]`→`[0, AXIS_LEN=10]` 큐브로(`_norm_axis`). 카메라는 데이터가 아니라 큐브(0~L)에 프레이밍 → 범위 좁히면 그 구간이 큐브를 채워 확대. **범위 밖 점은 클리핑 안 함**(큐브 밖으로 선이 뻗음).
  - ⚠ **축 눈금 = nice-number(Heckbert) 라운드 값**: `nice_ticks`/`format_tick_value` 순수함수(offscreen 테스트 가능). 자리수는 step에서 도출, 원점 여백(`ORIGIN_OFFSET_FRAC=0.1`, `_axis_pos`가 단일 좌표 출처). **휠 줌 연동 눈금 세분(딱 2단계)**: `_base_distance` 대비 줌 배율을 `[1,2]`로 클램프해 `_tick_target` 갱신(2배 줌=step 절반, 4·8배는 더 안 세분) → `_retick`(카메라 안 건드림, `_redraw`는 줌 풀리니 금지). 데이터 min 아래 라운드 눈금은 원점 여백 절반(5%)까지만 표시(`nice_ticks(lo=)`).
  - ⚠ **현재 위치 마커**: `MARKER_SIZE=9`, 깊이검사 끈 GL 옵션(`_ON_TOP_GL`)+`setDepthValue(10)`으로 최상단. 위쪽에 실제 x/y/z 값 라벨(`_TickText`=회전/멀티라인 지원 `GLTextItem` 서브클래스, 눈금 45° 회전·절반 폰트·축별 오프셋).

- **행/열 숨기기 임계(drag threshold) 열·행 분리 + 줌 연동**: 옛 공통 상수(20)를 분수로 — `HIDE_THRESHOLD_COL`=col폭×1/4(100%=20), `HIDE_THRESHOLD_ROW`=row높이×1/2(100%=10). ⚠ 행 기본높이(20)와 임계가 같으면 살짝만 끌어도 접혀, 1/2로 낮춰 최소폭(16)까지 리사이즈 여유.

- **3D 궤적 그래프 창(`button_graph`)**: 현재 CSV의 proxy 데이터로 별도 창에 `GLViewWidget` 3D 궤적. `combo_x/y/z`로 축 선택(**숫자 열만**, 비숫자 disable), 고른 축 수=차원(안 고른 축=0). `bar_time` 슬라이더로 현재 행 마커(노랑) 이동, `combo_track`으로 그룹별 궤적 분리+체크박스/색버튼 목록(gui_filter 재사용).
  - ⚠ **콜드스타트 보호 = 지연 import**: pyqtgraph/numpy/OpenGL은 무거워 `gui_viewer`가 top에서 import 안 하고 `open_graph` 안에서 지연 import. 검증: `import GUI.gui_viewer` 후 `sys.modules`에 pyqtgraph/numpy 없음.
  - ⚠ **그래프 창 = 부모 없는 독립 top-level**: 메인 창을 parent로 두면 `GLViewWidget` 첫 OpenGL 컨텍스트 생성 때 부모 네이티브 윈도우까지 재생성돼 메인 창이 깜빡임. → parent 없이 생성, 대신 메인 `closeEvent`에서 `self._graph_window.close()` 명시 호출(안 닫으면 app 미종료).
  - ⚠ **데이터 출처 = proxy 모델**(`graph_dataset()`): 값 필터(열·Δ)는 적용하되 **행/열 숨기기는 무시**. Δ 첫 행 안내문구만 빈값 예외처리해 숫자 판정 통과. `set_data(headers,rows,title)`로 재오픈마다 갱신(인스턴스 1개 재사용).
  - ⚠ **숫자 판정 = 빈칸 허용**(비어있지 않은 셀이 전부 float + 숫자 셀≥1). NaN/inf 점은 `np.isfinite` 마스크 제외. **트랙 상한 `MAX_TRACKS=60`** 초과 시 단일 궤적 폴백.
  - ⚠ **시야 = 선택 축 수에 따라 고정**(`_view_for`): 1·2축 잠금(좌클릭 회전 무시, `graph_area.mouseMoveEvent` 인스턴스 교체), 3축만 자유 회전. 휠 줌은 항상 허용.
  - ⚠ **빌드 의존성은 위 "빌드" 섹션 참고**(Qt6OpenGL·hiddenimports·numpy nomkl). 옛 spec으로 빌드하면 그래프 실행 시 `Qt6Core.dll` 크래시(`0xc0000409`).

- **CSV 목록 편집/저장 상태 연필 마커(3색)**: 좌측 목록 항목 우측 끝에 연필(`gui_listmark.EditMarkDelegate`, `super().paint()` 위 overlay). **white**=로드됐고 유효 .viewer 저장본 없음 / **green**=유효 분석 .viewer 불러온/저장 직후 / **yellow**=green 이후 또 바꿈+미저장. 미로드=연필 없음.
  - ⚠ **판정 = 저장점 Memento 객체 identity**(값 비교 아님): `entry['clean_memento']`와 `hist.current()`를 `is` 비교 → undo로 그 시점 복귀 시 같은 객체라 green. `entry['has_saved']`(유효 분석 저장본 보유)가 green/yellow vs white를 가름. 빈 .viewer는 green 안 침(`_saved_state_has_analysis` 게이트). 줌 등 비기록 동작은 히스토리에 없어 자연 무시.
  - ⚠ **백그라운드 로드(다른 CSV 보는 중)에서도 green**: `update_table`이 현재 CSV 아니면 early-return → `csv_load_complete` 꼬리 `_mark_after_background_load`가 모델/baseline 없이 `has_saved`만 추정 세팅 후 `_refresh_mark`. **마커 영속 출처 = `self._mark_state` dict**(F5 재구성에도 보존). 아이콘 `GUI/res/image_pencil_*.png`(spec `*.png` 글롭 자동 포함).

- **표 확대/축소(Ctrl+휠, 5단계 50~150%)**: 셀크기·글자·마커를 단계별 절대값 배열(`ZOOM_*` 클래스 상수)로 줌. 저장 안 함(항상 100% 시작). 트리거=뷰포트 `eventFilter`의 Wheel+Ctrl.
  - ⚠ **셀 크기 = '직전 단계 대비 비율'**(엑셀식). 열=각 열 `resizeSection(현재×rc)`, 행=`setDefaultSectionSize`만 바꿔 O(1).
  - ⚠⚠ **순서 함정 2개**: ① 열 비율 스케일 루프는 `setDefaultSectionSize`보다 **먼저**(뒤면 이중 적용) · ② 마커 절대 두께는 `setDefaultSectionSize` **뒤에**(앞이면 기본값에 덮임).
  - ⚠ **`MARKER_SIZE_PX`는 줌 연동 property**(고정 상수로 되돌리면 줌 안 따라감). 셀 텍스트 폰트는 모델 `FontRole`(공통 함정 #3). 줌 리사이즈는 `_suppress_width_record` 안 → undo/.viewer 무영향. **줌 전후 스크롤은 비율 유지**(`_scroll_fraction`→`_apply_scroll_fraction`).

- **MSI 설치 상태별 3분기(설치/업그레이드/제거토글)** → 위 "배포" 섹션에 통합. 핵심: ProductCode를 버전별 결정 GUID로 만들어 옛 버전 위 실행 시 업그레이드, 같은 버전 재실행 시 제거토글. 확인창 3종이 조건으로 정확히 하나만 뜸.

- **행/열 숨기기(엑셀식)**: 값 필터와 직교하는 '위치 기반' 상태(`hidden_rows`/`hidden_cols`, 소스 좌표). 숨긴 연속 구간마다 마커 섹션 1개(열=`⋯`, 행=`︙`, 18px) 합성. 트리거=섹션 경계를 시작 끝 너머로 드래그(음수 너비), 펼침=마커 더블클릭.
  - ⚠ **트리거 = 마우스 geometry**(Qt `sectionResized`는 `minimumSectionSize`로 클램프돼 음수 안 줌) → press 때 그립 잡고 release 때 `마우스−섹션시작끝 ≤ 임계`면 숨김. **반드시 헤더 `viewport()`에 eventFilter**(공통 함정 #4).
  - ⚠ **모델 reset은 섹션 크기를 '위치(proxy 인덱스) 기준'으로 보존** → 열 수 변동 시 숨긴 자리 크기를 밀려든 섹션이 물려받는 '전염' 버그. 해결: 숨김/펼침 시 **소스 기준 캡처·복원**. 열=`_col_width_map` 전수, 행=18만 전수 불가라 `_capture_row_layout`이 reset 전 비기본 위치 기억→reset 후 청소+소스 기준 재적용.
  - ⚠ **마커 = Fixed 리사이즈 모드**(사용자 드래그 방지). 리사이즈 모드도 reset 시 위치 누수 → 글로벌 `setSectionResizeMode(Interactive)`로 되돌린 뒤 현재 마커만 Fixed. Fixed는 프로그램적 `resizeSection`은 못 막아 다중선택 전파가 마커를 늘리던 엣지 → `_full_selection_sections`가 마커 위치 차집합 제외.
  - ⚠ **펼침 = 열·행 모두 원래 크기 복원**(숨길 때 press 시점 `pre_size`를 `hidden_*_sizes`에 보관, .viewer 저장). **필터∧숨김 직교**: 보임=필터통과∧비숨김. **Δ는 소스 따라감**(base 숨기면 Δ도 숨김, Δ 자체 드래그-숨김은 무시). 검색/복사는 마커 열(`source_columns()`=-1)·마커 행(`accepted<0`) 스킵.

- **폴더 버튼 = 폴더 선택창 → 탐색기 열기**: `button_csv_folder` 클릭 시 `edit_csv_path`(상위 경로)를 `os.startfile`로 탐색기에. 경로 비었거나 없으면 `_app_dir()`(frozen=exe 디렉터리, 개발=argv[0] 디렉터리).
  - ⚠ **`open_csv_folder`(선택창)는 유지** — 빈 창 시작·`edit_csv_path` 텍스트 클릭이 사용. 폴더 변경 수단=(a)경로 텍스트 클릭=선택창 (b)드래그&드롭, **버튼만** 탐색기로. 여는 건 **상위 경로**(형제 CSV 폴더 보임).

- **필터 팝업 Apply/Close 가려짐 해결**: 창 최소높이를 매직 상수(`scrollbox+120`)→`self.layout().minimumSize().height()` 실측으로. ⚠ 명시적 `minimumHeight`가 layout 최소보다 작으면 그 작은 값이 우선(자동 floor 아님) → 반드시 실측값 이상. `.ui`의 `setMinimumSize`도 코드에서 덮어야 함. max는 `create_items`의 400.

- **열/행 크기 저장 = sparse 그룹 포맷 + 행높이 영속(열과 parity)**: `.viewer`의 `col_widths`를 전체 배열→`{크기:[인덱스]}` 그룹, `row_heights` 신설. 인메모리는 `{인덱스:크기}` sparse. `view_state.pack_sizes`/`unpack_sizes`.
  - ⚠ **그룹 포맷 이유**: 다중선택 동기조정으로 수만 행 같은 높이 바꿔도 파일 안 부풂. 구포맷(v1 배열) 하위호환(list면 위치=인덱스).
  - ⚠ **'안 바뀐 것' 기준 = `header.defaultSectionSize()`**(하드코딩 80/20 아님): 행 기본높이는 폰트/스타일 최소치로 클램프돼 20이 아닐 수 있어, 하드코딩 20과 비교하면 안 바뀐 행이 전부 '변경됨'으로 잡혀 모든 행 저장되던 버그(열은 80이라 멀쩡). reset도 `defaultSectionSize()` 사용.

- **초기화 재설계: `button_reset`=전체 분석 초기화(가역) + F5=정상 재오픈**: reset은 모든 분석을 초기값(하이라이트·Δ·필터 없음·열너비80·행높이20)으로. 가역=`_restore_memento(raw)` + `record_history` 1회(Undo 1단계). no-op 가드로 이미 raw면 빈 단계 안 쌓음.
  - ⚠ **행높이가 Undo/.viewer 대상**(reset 가역화 위해). `Memento` 4번째 슬라이스 `rows`, 열너비와 대칭. **18만 행 비용 회피 = dirty-flag(`_rows_dirty`) + sentinel**(안 건드리면 캡처 None). 좌표=보이는(proxy) 행 위치.
  - ⚠ **F5(`reload_current_csv`)=정상 재오픈**(cache 폐기 후 재로드 → 해시 일치 시 .viewer 자동복원). cache 폐기로 새 baseline → F5 자체는 Undo 비대상. raw로 가려면 `button_reset`.

- **Δ 열 필터 팝업에서 첫 행 안내문구(`r(n)-r(n-1)`) 제외**: 후보값을 2번째 보이는 행부터 수집(`delta_values_excluding_self`에서 `v == _FIRST_LABEL` 스킵). ⚠ 스냅샷엔 라벨이 남아 표시·색칠·검색 무영향(드롭다운 후보에서만 제외) → 첫 행은 Δ값 필터로 못 숨김(의도된 동작).

- **엑셀형 다중선택 동시조정(열너비/행높이)**: 완전 선택 N개 중 하나 드래그→손 뗄 때 나머지 스냅. `_finalize_resize`·`_full_selection_sections`·`_propagating` 플래그.
  - ⚠ **release 감지 = 헤더 viewport eventFilter + `singleShot(0)`**(QHeaderView엔 resize 종료 신호 없음, `sectionResized`는 드래그 중 연속). *어느 섹션을 어떤 크기로*는 `_pending_h/v` 캡처, *언제*는 mouse-release. **viewport에 걸 것**(공통 함정 #4).
  - ⚠ **`_propagating` 가드 필수**(전파의 resizeSection이 다시 sectionResized emit→재귀). 기존 `_suppress_width_record`(프로그램적 변경 억제)와 **별개** — 전파는 사용자 제스처라 그쪽으로 억제 금지. **대량 행 전파 = `setUpdatesEnabled(False)`**(6만 행 929ms→43ms). 완전선택 판정은 range 분석(공통 함정 #1). 열은 디바운스 너비기록으로 1단계 Undo.

- **18만 행 열 헤더 선택 렉 제거(헤더 repaint 1,600ms→0.3ms)**: 열 헤더 선택 시 그 열 전체 선택 → Qt 기본 `initStyleOptionForIndex`가 `selectedPosition` 계산하려 `isColumnSelected()`→전 행 순회(헤더 호버·스크롤·포커스마다 재실행). 해결: `FilterHeaderView.initStyleOptionForIndex` 오버라이드(virtual이라 super().paintSection에도 반영)해 super 도는 동안만 프록시 `rowCount`를 0 위장(`filter_model._fast_header_paint`), 선택 상태(`State_On`)는 selection range로 O(range) 직접 판정(`_column_intersects_selection`).
  - ⚠ **`setHighlightSections(False)`로는 안 줄어든다**(이건 선택 강조가 아니라 인접 섹션 연결선 계산이라서). rowCount 0 위장은 가로 헤더 기하가 행 무관이라 픽셀 diff=0, 동기 구간이라 안전. State_On 직접 판정은 Qt `columnIntersectsSelection`과 동치(Bold 보존).

- **열 헤더 폰트 스타일(선택/필터→Bold, Δ→Italic)**: `FilterHeaderView.paintSection`에서 상태별 폰트만 painter에 주입하고 `super().paintSection()` 위임(배경·말줄임·스타일시트 유지). 판정: 선택=`State_On`, 필터=`src in column_filters`(Δ는 `has_delta_filter`), Δ=`is_delta_column`.
  - ⚠ **진짜 원인 = `highlightSections`**: 커스텀 헤더는 기본 `False`라 가로 헤더만 안 굵어짐(세로는 QTableView가 자동 True) → `setHighlightSections(True)`. **QHeaderView는 헤더 FontRole을 안 읽음** → 헤더 폰트는 paintSection으로만(공통 함정 #3). 굵기는 offscreen 미렌더.

- **`FilterHeaderView` 모듈 분리(`GUI/gui_header.py`)**: 가로 헤더는 필터 팝업(`FilterWidget`)과 별개라 분리. 의존 단방향(`gui_header`→`gui_filter.FilterWidget`). **`utils/`가 아니라 `GUI/`에 둔 이유**: `utils/`는 비시각 모델/스레드 전용(QWidget 없음), `FilterHeaderView`는 시각 위젯. 순수 이동(로직 무변경).

- **복사(`copy_selection`) 대용량**: 전 열/행 복사가 18만 행에서 수 초 멈추던 것 → `selection()` range만 보고(`_spans_cover`), 셀 값은 소스 `rows`+Δ 스냅샷 직접 읽기(`accepted_rows()` 벌크 매핑·`source_columns()`·`delta_snapshot()`). 측정 10만 행 ≈0.04s. (공통 함정 #1)

- **Δ(행간 차이) 가상 열**: 필터창 `☰🡫Δ` 버튼 → 그 열 오른쪽에 `Δ [헤더]` 가상 열. 각 행=(그 행)−(윗행), 첫 행=`R(n)-R(n-1)`, 비숫자=`=`/`≠`. `add_delta_column/remove_delta_column`.
  - ⚠ **스냅샷(고정)**: `add_delta_column` 시점의 보이는 행 순서로 1회 계산(`_delta_snap[base]={source_row:문자열}`). 이후 필터 바뀌어도 재계산 안 함(숨겨졌던 행은 키 없어 빈칸). 포맷은 `_format_delta` 한 곳.
  - ⚠ **열 간접화(col_map)**: '프록시 열==소스 열' 가정을 깸. `_col_kind/_col_src/_src_to_pcol`로 매핑. `column_filters`·`setFilterForColumn`·`column_values_excluding_self` 등은 전부 **소스 열 키**. 헤더뷰가 클릭 열을 `source_column_of`로 변환해 넘김. Δ 열은 `mapToSource` 무효.
  - ⚠ **원본 모델에 실제 열 삽입 금지**(18만 `row.insert` 프리즈 + 필터/하이라이트 키 재색인). 추가=`beginInsertColumns` + col_map 재구성(뷰는 보이는 셀만 다시 그림). Δ에서 깨지던 곳: `search_model`은 `source_columns()`로 변환·Δ 스킵, `copy_selection` 헤더는 `column_label(c)`, `_apply_highlight`는 무효 소스 인덱스 스킵.
  - ⚠ **Δ 셀 색칠 = 프록시 `_delta_color`에 별도 저장**(소스 셀 없음). `data()` BackgroundRole이 *사용자 색 > 첫 행 옅은 회색 > 없음* 순. 3경로(`set_delta_cell_colors`·`color_delta_rows`·`clear_all_delta_colors`). `_emit_delta_bg`가 변경 Δ 열 전 행 1회 dataChanged.
  - ⚠ **Δ 셀 italic = `data()` FontRole**(셀은 delegate가 honor), **Δ 헤더 italic·헤더 배경(223)은 `paintSection`**(헤더 FontRole 안 읽음).
  - ⚠ **Δ 열 필터 = Δ값(스냅샷) 기준**(별도 `delta_filters`, `column_filters`와 분리). `_row_passes(i, exclude_src, exclude_delta)` 두 필터 AND. Δ 열 삭제 시 그 필터도 해제.
  - ⚠ **Δ 비교셀 테두리 + 툴팁**: Δ 셀 선택 시 비교한 두 부모셀에 테두리(R(n)=파랑, R(n-1)=빨강, `gui_delegate.CompareBorderDelegate`). '이전 행'=스냅샷 시점 이전 보이는 행(`_delta_prev`)이라 화면 윗행과 다를 수 있고, 필터로 숨겨졌으면 빨강 생략. 툴팁은 `ToolTipRole`(hover 시 1셀만 조회→18만 행 무비용). selectionModel은 setModel마다 새로 생기니 `_wire_selection_signals`로 재연결.

- **필터창 값별 행 색칠**: 필터창 각 값 우측 색버튼 → 그 값 가진 모든 행 색칠. `source_rows_with_value`(lazy O(N) 1회) → `table_model.highlight_rows`.
  - ⚠ **별도 row-color 레이어 없이 `highlight_cells`(셀 단위)에 직접 기록**(수동 색칠과 동일 저장소). `highlight_rows`는 셀별 QModelIndex 없이 좌표 기록 후 bounding box 1회 dataChanged.
  - ⚠ **색버튼은 줄 우측 오버레이**(`_FilterItemRow.resizeEvent`, 체크박스 폭 `Ignored`) — 레이아웃에 넣으면 긴 텍스트가 줄 폭 늘려 버튼이 화면 밖. `QColorDialog` 띄우는 동안 `_dialog_open` 가드로 팝업 자동닫힘 방지, 닫힌 뒤 포커스 복원.

- **MSI 설치/제거 '시작 전' Yes/No 확인창** → 위 "배포" 섹션에 통합(함수형 VBScript `Return="check"` type 4102, `ExecuteAction` 전 스케줄해 No=아무 변경 없음).

- **분석 결과 저장/자동복원(`.viewer`)**: 하이라이트·열필터·Δ·열너비·행높이·스크롤을 **CSV 폴더당 1개 `<폴더>\.viewer`(JSON)**에 Ctrl+S 저장, 다음에 그 CSV를 **처음 열 때 내용 해시 일치하면** 자동 복원. `utils/view_state.py`.
  - ⚠ **proxy/cache 통째 저장 금지**(피클=본문 중복+Qt 버전의존). **사용자 입력만** 평면 JSON 추출, 파생(`_accepted`·col_map·Δ값)은 재계산. 파일 본문은 저장 안 함(해시로 동일성만).
  - ⚠⚠ **하이라이트: 저장 포맷 `{색:{열:[행]}}` ↔ 메모리 `{(행,열):색}` 분리가 핵심.** 메모리를 색→열→행으로 바꾸지 말 것 — `data()` BackgroundRole이 스크롤마다 `get((row,col))` O(1)인 핫패스라, 바꾸면 18만 행 렌더가 느려짐. `restore_highlights`는 int/str 열키·구포맷 `[[행,열]]` 모두 처리.
  - ⚠ **Δ는 '행 리스트' 아니라 '스냅샷 당시 열필터'만 저장**(`_delta_snap_filter[base]`, 보통 빈 dict). 복원 때 그 필터 재적용→`_compute_snapshot`으로 값 재생(무필터 Δ가 range(18만) 정수 저장되는 것 회피).
  - ⚠ **복원 시점 = 모델 최초 생성 분기에서만**(`update_table` else, 뷰 부착 전). 순서: 필터·Δ(`restore_state`)→하이라이트→col_widths→스크롤. **저장=read-modify-write 머지**(현재 CSV 항목만 교체, 원자적 재기록). 깨진/구버전은 envelope·version 게이트+try/except로 조용히 무시(CSV 열람 절대 안 막음).
  - ⚠ **JSON = 커스텀 `_pretty`**(구조는 들여쓰고 잎 배열은 한 줄). `json.dump(indent=2)`로 바꾸지 말 것(수만 행 색칠 시 파일 수십 배 팽창). dict 키는 `json.dumps(str(k))`로 수동 문자열화(int 열키→`"5":`). 해시 비용 0(loader가 계산한 `content_hash` 재활용).

- **전체검색 시작 위치 = 내 현재 위치(앵커)부터**(범위검색은 1/N 그대로). 앵커=마지막 선택 셀 유효하면 그 (행,열), 없으면 최상단 보이는 행. `matches`가 (row,col) 오름차순이라 튜플 비교=행우선·열다음 → `bisect_left` O(log N), 헤더 매치(-1,*)는 자동 제외. `currentIndex()`+`rowAt(0)` O(1)만(공통 함정 #1).

- **검색 현재 셀 회색 테두리**: next/prev 시 현재 매치에 회색 2px. 기존 `CompareBorderDelegate`(Δ 테두리)에 검색 마크 추가(`set_search_mark`·`GRAY`, 같은 셀이면 회색 위). 델리게이트 set/clear는 GUI 레이어(`search_gui_update`→`_update_search_mark`), 해제 3곳(빈검색·검색바 닫기·CSV 전환). 헤더 매치(행<0)는 셀 테두리 없음.

- **선택 영역 검색(scoped search)**: Ctrl+F 범위는 **검색바 열 때의 선택 상태**(`capture_scope`, 검색바 *열기 전* 선택해야 함). **열/행 '전체 선택'만** 범위로 인정(셀 클릭/드래그=전체검색). 범위는 닫힐 때까지 sticky(`reset_scope`). 헤더(행 -1)는 전체검색일 때만 포함. 열+행 동시=합집합. **range 직접 분석으로 판정**(공통 함정 #1).

- **CSV 재진입 시 파일 변경 감지 → 캐시 무효화**: `clicked_csv_list`에 `_cache_is_fresh` 게이트. **stat 게이트 + 해시 타이브레이커**: ① stat(size+mtime_ns) 같으면 신선(해시 안 함, ≈7µs) · ② size 다르면 폐기 · ③ **size 같고 mtime만 다를 때만** sha256 비교.
  - ⚠ **전체해시를 기본으로 안 쓰는 이유**: 재진입마다 GUI 스레드 동기인데 105MB warm 41~130ms라 매 탭전환 끊김. `os.stat`(0.007ms)이 1만 배 싸고 외부 재생성은 mtime/size로 잡힘. **변경 감지 시 그 CSV 뷰 상태(필터·하이라이트·Δ·스크롤·열너비) 전부 초기화**(내용 달라지면 값 기준 필터 무의미).

- **ESC 연타 닫기 간격 상수화**: `ESC_INTERVAL_SEC=0.5`(→ 현재는 `EscCloseToast.INTERVAL_SEC`로 이동, 위 gui_esc 분리 항목 참조). ⚠ **연타 유효 간격 = ESC 안내 토스트 노출 시간**(둘을 같은 상수에 의도적으로 묶음, 토스트=`int(상수×1000)`ms). 독립값으로 분리하지 말 것.

- **열 너비 per-CSV 저장/복원**: CSV 전환 시 너비 기본값(80) 리셋 → `last_view`와 동일 패턴으로 cache에 `col_widths` 키. ⚠ **proxy가 아니라 cache에 저장**(너비=뷰 기하). 복원은 **가로 스크롤보다 먼저**(너비가 스크롤 범위 바꿔 클램프 방지). `len==header.count()` 가드(Δ 열 안전).
