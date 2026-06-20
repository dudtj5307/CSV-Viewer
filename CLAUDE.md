# CSV Viewer — Claude 참조 문서

## 한 줄 요약
CSV 폴더를 열람하는 **PyQt6 데스크톱 뷰어**. 외부 응용 SW가 `CSV Viewer.exe [CSV폴더경로]` 를 실행하면 **독립 프로세스로 뷰어 창 하나**가 뜬다. 여러 번 실행하면 서로 독립적인 창이 여러 개 뜬다. onedir 빌드라 압축해제가 없어 매 실행 콜드스타트가 가볍다 → **백엔드/IPC가 없는 단순 구조**.

> 이 프로젝트는 기존 Packet_Parsing_Software(PPS)에서 **CSV Viewer 부분만 분리**한 것이다. 패킷 캡처/파싱/CSV 생성(scapy·IDL·parser·creator) 기능은 모두 제거됐다.

---

## 개발 환경 / 실행

conda 환경 **`sniff_env`** 에서 실행/테스트한다. (PyQt6 설치됨. scapy는 더 이상 불필요)

- 인터프리터: `C:\Users\yslee\anaconda3\envs\sniff_env\python.exe`
- `python` (PATH) → Windows Store 스텁이라 실행 안 됨 (exit 49)
- `py` 런처 · anaconda base · Python310 → **PyQt6 없음**. 오직 `sniff_env`에만 설치됨
- GUI 없이 로직 스모크 테스트 시 → `QT_QPA_PLATFORM=offscreen` 설정 후 실행

```powershell
# 앱 실행 (인자 없으면 폴더 선택창, 폴더 경로를 주면 그 폴더를 바로 연다)
& "C:\Users\yslee\anaconda3\envs\sniff_env\python.exe" csv_viewer.py
& "C:\Users\yslee\anaconda3\envs\sniff_env\python.exe" csv_viewer.py "CSV\raw_250416_174444"

# offscreen 로직 테스트
$env:QT_QPA_PLATFORM="offscreen"; & "C:\Users\yslee\anaconda3\envs\sniff_env\python.exe" <script>.py
```

---

## 디렉토리 구조

```
Project_CSV_Viewer/
├── csv_viewer.py              # 진입점. 폴더 해석 → ViewerWindow 하나 표시 → app.exec()
├── CSV_Viewer.spec            # PyInstaller 빌드 (onedir, windowed)
├── GUI/
│   ├── gui_viewer.py          # ViewerWindow - CSV 목록 + 테이블 뷰어 (메인 화면)
│   ├── gui_header.py          # FilterHeaderView - 테이블 가로 헤더(우클릭→필터 팝업 띄움)
│   ├── gui_filter.py          # FilterWidget - 우클릭 필터 팝업창(체크박스 필터 UI)
│   ├── res/                   # 아이콘/스피너 리소스 (png · ico · gif)
│   └── ui/                    # pyuic6 자동생성 UI 파일 (직접 수정 금지)
│       ├── dialog_viewer.py   # Ui_ViewerWindow
│       ├── dialog_filter.py   # Ui_FilterForm
│       └── widget_esc.py      # Ui_WidgetESC (ESC 안내 위젯)
├── utils/                     # ⚠ viewer/ 하위가 아니라 utils/ 바로 아래에 있음
│   ├── table_model.py         # CSVTableModel (QAbstractTableModel)
│   ├── filter_model.py        # CSVFilterProxyModel (열 필터 프록시 + Δ 가상 열)
│   ├── search_model.py        # SearchModel (Ctrl+F 검색 로직)
│   └── csv_loader.py          # CSVLoaderThread (비동기 CSV 로딩)
└── CSV/                       # 열람 대상 CSV 폴더들 (샘플/테스트 데이터)
```

> **정리 잔여물(미사용)**: `IDL/`, `RAW/`, `test code/`, `settings.conf`, `build/`, `dist/` 와 `GUI/ui/`의 생성 스크립트는 PPS 시절 데이터/도구다. 런타임과 무관하므로 보존했으며 수동 삭제 가능하다.

---

## 아키텍처: 실행마다 독립 프로세스 + 창 하나

`CSV Viewer.exe` 실행 1회 = 독립 프로세스 1개 = `ViewerWindow` 1개. 단일 인스턴스/IPC/상주 백엔드가 **없다**.

```
csv_viewer.py main()
  ├─ setup_std_streams()        : windowed에서 stdout/stderr=None 가드
  ├─ QApplication 생성
  ├─ argv[1] 검사             : 유효 폴더면 folder, 없거나 무효면 None
  ├─ ViewerWindow(icon_path, folder|None).show()  : 폴더 None이면 빈 창으로 시작
  └─ (folder 없을 때) QTimer.singleShot(0, viewer.open_csv_folder)  : 빈 창 위에 폴더 선택창 → app.exec()
       └─ 선택 시 _load_folder / 취소 시 빈 화면 유지
```

- **독립성**: 각 창이 완전히 별개 프로세스 → 서로 영향 없음(하나 닫아도 나머지 유지). 여러 번 실행하면 누른 횟수만큼 창이 뜬다.
- **속도**: onedir라 매 실행 압축해제가 없어 기동이 빠름. **onefile은 매 실행마다 temp 압축해제 → 느려지므로 쓰지 말 것** (이 단순 구조의 전제).
- **트레이드오프**: 창 N개 = 프로세스 N개(메모리도 N배). "한 번에 전부 닫기" 같은 공통 제어점은 없다. (필요해지면 단일 인스턴스 백엔드 + QLocalServer 구조로 되돌릴 수 있음 — git 히스토리 참고)
- **windowed(`console=False`) 주의**: frozen 상태에서 `sys.stdout/stderr`가 `None`이라 코드 곳곳의 `print()`(예: csv_loader)가 크래시한다 → `setup_std_streams()`가 None이면 `devnull`로 대체한다. **이 가드 없이 print 추가 금지.**

---

## 주요 클래스

| 클래스 / 함수 | 파일 | 역할 |
|--------|------|------|
| `main()` | csv_viewer.py | 진입점. 인자 폴더면 바로 열고, 없으면 빈 창 + 폴더 선택창(취소 시 빈 화면 유지) |
| `ViewerWindow` | gui_viewer.py | CSV 목록 + 테이블 뷰어. cache 기반 다중 CSV 관리. **생성자 = `(icon_path, csv_folder=None)`** (None=빈 상태) |
| `FilterHeaderView` | gui_header.py | 커스텀 수평 헤더. 우클릭 → 열 필터 팝업 (`FilterWidget` 사용) |
| `FilterWidget` | gui_filter.py | 체크박스 기반 필터 팝업창 |
| `CSVTableModel` | utils/table_model.py | QAbstractTableModel. rows + highlight_cells |
| `CSVFilterProxyModel` | utils/filter_model.py | 열별 필터 프록시 + Δ 가상 열. column_filters(소스 열 키), 헤더 `⧩` 표시, 열 간접화(col_map) (아래 ⚠) |
| `SearchModel` | utils/search_model.py | Ctrl+F 검색 + 하이라이트. **선택 영역 검색**(아래 ⚠) 지원 |
| `CSVLoaderThread` | utils/csv_loader.py | 비동기 CSV 읽기 (utf-8-sig → cp949 폴백). 읽은 데이터는 `pyqtSignal(str, object)`로 전달 (아래 ⚠) |

> **⚠ 선택 영역 검색(scoped search)**: Ctrl+F 검색은 **검색바를 열 때의 선택 상태**로 범위가 정해진다(`SearchModel.capture_scope`, `gui_viewer.search_gui_init`에서 호출). **열/행 '전체 선택'만** 범위로 인정하고, 셀 클릭·셀범위 드래그는 **전체검색**이 된다. 범위는 검색바가 닫힐 때까지 유지(sticky)되며 닫으면 `reset_scope`로 해제된다(검색어를 바꿔 재검색해도 범위 유지). 규칙: **헤더(행 -1)는 전체검색일 때만 포함**(범위검색 시 제외) · 열+행 동시 선택은 **합집합**(선택 열 OR 선택 행). placeholder는 범위검색이면 `"Search selected area"`, 전체면 `"Find Text (Enter)"`. ⚠ 범위는 *열 때* 캡처되므로 **열/행을 먼저 선택한 뒤 Ctrl+F** 해야 한다(검색바를 먼저 연 뒤 선택하면 범위 안 잡힘 → 다시 열어야 함).
>
> **⚠ 함정 — `selectedColumns()`/`selectedRows()`는 대용량에서 수 초 걸린다**: `capture_scope`는 의도적으로 `QItemSelectionModel.selectedColumns()/selectedRows()`를 **쓰지 않는다**. 열 전체 선택(18만 행을 덮는 범위)에서 이 두 API는 내부적으로 전 행을 순회한다 — 측정값으로 `selectedColumns()`≈0.8s, `selectedRows()`≈1~2.8s라 **Ctrl+F가 2~3초 얼어붙었다**. 대신 `selectionModel().selection()`의 **range(QItemSelectionRange) 목록만** 보고 구간 커버리지(`_spans_cover`)로 '열/행 전체 선택'을 직접 판정한다(범위 수는 항상 소수 → 0.1ms 이하, 1000배+). 혼합 선택 시 Qt가 범위를 쪼개도(예: `[(0,1,0,0),(3,N,0,0),(2,2,0,5)]`) 구간 합집합으로 정확히 잡는다. → **선택 기반 판정은 selectedColumns/Rows 대신 range 직접 분석으로.** (`gui_viewer.copy_selection`도 같은 방식으로 고쳤다 — 아래 변경 이력 참고.)

> **⚠ 함정 — 대용량 데이터 cross-thread 시그널은 `object`로 넘긴다**: `CSVLoaderThread.load_complete`를 `pyqtSignal(str, list)`로 선언하면, 워커→GUI 큐드 연결에서 PyQt가 중첩 리스트를 **QVariantList로 변환·복사**한다. 이 비용이 워커 `emit`과 **수신 GUI 스레드의 역변환(슬롯 호출 *전*에 GUI 스레드에서 수행)** 양쪽에서 발생해 18만 행 기준 **수 초간 GUI가 얼어붙는다**(읽는 동안 다른 CSV 클릭이 안 먹히던 원인. 프록시 부착(≈80ms)은 무관했음). `pyqtSignal(str, object)`(PyQt_PyObject)는 파이썬 객체를 **참조로** 넘겨 변환·복사가 0이다. 단 참조 전달이라 **emit 후 워커에서 그 데이터를 수정하면 안 된다**(현재는 emit 직후 `return`이라 안전). → 큰 데이터를 스레드 시그널로 넘길 땐 `list`/`dict` 대신 `object`.

### ViewerWindow 내부 구조

```
ViewerWindow(icon_path, csv_folder=None)   # csv_folder=None → 폴더 미선택 '빈 상태'(제목 "CSV Viewer", 목록·경로칸 비움)
├── icon_path : GUI/res 리소스 경로 (진입점이 resource_dir()로 주입)  ← PPS의 parent.icon_path 결합 제거
├── cache[csv_file_name] = {
│     'table_data'  : raw 2D list (헤더 + 데이터 행)
│     'table_model' : CSVFilterProxyModel  └─ CSVTableModel (rows / highlight_cells)
│     'last_view'   : (v_scroll, h_scroll)  ← CSV 전환 시 위치 복원
│     'status'      : 'ok' | 'empty' | 'fail'
│   }
├── FilterHeaderView  (수평 헤더 - 우클릭 열 필터)
├── CSVLoaderThread   (self.loader_threads 로 추적/정리)
└── SearchModel       (self.search_model)
```

CSV 폴더 위치는 3단계로 독립 관리: `csv_folder_path`(상위 경로) + `csv_folder_name`(폴더명) + `csv_file_name`(현재 파일). 드래그&드롭 / 폴더 버튼 / rename은 `_load_folder`·`_rename_folder`로 처리.

---

## 빌드 (PyInstaller)

```powershell
& "C:\Users\yslee\anaconda3\envs\sniff_env\python.exe" -m PyInstaller --noconfirm CSV_Viewer.spec
# 출력: dist\CSV Viewer\CSV Viewer.exe   (onedir = 폴더 통째로 배포, ~89MB)
```

- **onedir** (`COLLECT`): 실행 시 압축해제가 없어 프로세스 기동이 빠름. (onefile 금지 — 위 "아키텍처" 참고)
- **`console=False`** (windowed): 외부 SW에서 실행 시 콘솔창이 뜨지 않음.
- 리소스는 png·ico·**gif(로딩 스피너)** 모두 포함(과거 PPS.spec은 gif 누락이었음).
- **⚠ conda PyQt6 함정**: Qt 런타임 DLL(`Qt6Core/Gui/Widgets.dll`)이 `.pyd` 옆이 아닌 `<env>\Library\bin` 에 있어 PyInstaller가 자동수집을 **놓친다**(플러그인만 들어가 frozen 실행 시 크래시). spec에서 `QT_NEEDED` DLL을 **명시적으로 `binaries`에 추가**해 해결 — 그러면 ICU 등 의존 DLL은 자동 추적된다. Qt 모듈을 새로 쓰면(`QtNetwork` 등) `QT_NEEDED`에 추가할 것.
- `excludes`로 scapy/tkinter/psutil 등 차단.

---

## 배포 (MSI 설치 파일)

외부 노트북 배포용 **WiX 기반 MSI**. 정의/스크립트는 `installer/`, 상세는 `installer/README.md`.

```powershell
.\installer\build_msi.ps1          # dist 기반 MSI 빌드 → installer\out\CSV Viewer Setup.msi
.\installer\build_msi.ps1 -Rebuild # PyInstaller 재빌드부터
```

- per-machine 설치(관리자 권한): `C:\Program Files\CSV Viewer`
- 시스템 환경변수 `CSV_VIEWER_HOME` = 설치 경로
- 폴더 / 폴더 빈 공간(배경) 우클릭 → "Open with CSV Viewer(V)" (실행: `"…\CSV Viewer.exe" "%V"`)
- **재실행 = 제거(토글)**: 이미 설치된 상태에서 같은 MSI를 다시 실행하면 설치가 아니라 **제거**로 동작. `Installed` 일 때 `SetProperty REMOVE=ALL` 을 `CostFinalize` 전(seq 999)에 세팅. 진짜 `/x` 제거·업그레이드 중 구버전 제거와는 조건(`NOT REMOVE`, `NOT UPGRADINGPRODUCTCODE`)으로 구분.
- **⚠ WiX 버전 v5 고정**: v6/v7은 OSMF(상용 유지비) EULA 게이트가 빌드를 막는다. `installer/CSVViewer.wxs`는 v4 네임스페이스라 v5에서 그대로 빌드된다. (`build_msi.ps1`이 `--version 5.0.2`로 설치)
- **완료 팝업**: 설치/제거 끝에 VBScript MessageBox(`popup_install.vbs`/`popup_uninstall.vbs`)를 `InstallUISequence`의 `ExecuteAction` 직후 띄움. `/qn` 무인설치에선 안 뜸. (WiX v5는 인라인 스크립트 불가 → 외부 `.vbs`를 `ScriptSourceFile`로 참조, 한글 위해 UTF-8 BOM 필수)
- **⚠ ProductCode·UpgradeCode 둘 다 고정·변경 금지**: `ProductCode`를 고정 GUID로 박았다. 자동 생성되게 두면 매 빌드 ProductCode가 바뀌어, 다시 빌드한 MSI를 Windows가 '다른 제품'으로 보고 **재실행=제거 토글이 깨진다**(설치된 것과 ProductCode가 달라 자기 자신을 인식 못 함 — 실제로 이 버그를 겪음). `AllowSameVersionUpgrades=yes`라 옛 빌드(다른 ProductCode)가 깔려 있어도 새로 설치하면 자동 제거된다.
- **⚠ 버전 올리기**: 고정 ProductCode라 버전만 올려 '제자리 업그레이드'는 불가(같은 ProductCode+다른 버전 = 설치 에러). 토글로 제거→재설치가 기본. 정식 업그레이드가 꼭 필요하면 그때 ProductCode를 새 GUID로 발급(그 시점부터 이전 빌드와 토글 호환은 끊김).
- **⚠ `build_msi.ps1` 편집 시**: 한글 주석 포함 → UTF-8 **BOM 유지 필수**(없으면 PowerShell 5.1이 cp949로 오독해 파싱 실패).

---

## 알려진 TODO / 미완성 항목

- (현재 없음) — '셀 선택 시 열 헤더 볼드 미작동'은 해결됨(아래 변경 이력 "열 헤더 폰트 스타일" 참고).
  - ⚠️ 참고로 헤더 폰트(bold/italic)는 offscreen이 굵기를 렌더하지 않아 자동/스크린샷 검증 불가 → 실Windows 육안이 최종 관문(필터 열의 `⧩` 텍스트 마커는 `headerData`라 offscreen에서도 보임).

---

## 변경 이력 (최신이 위, 한두 줄 요약)

- **Δ 셀 선택 시 비교셀 테두리 + hover 툴팁 + 문자 비교 `=`/`≠`**: Δ 셀(첫 행 제외) 선택 시 그 차이가 비교한 두 부모셀에 테두리(현재 R(n)=파랑, 이전 R(n-1)=빨강) — `gui_delegate.CompareBorderDelegate`가 `super().paint()` 위에 overlay(색·두께는 delegate 상수, 사용자가 직접 조정). **'이전 행'은 *스냅샷 시점의 이전 보이는 행*** (`_snapshot`이 `_delta_prev[base][sr]=prev_sr`를 동시 저장)이라 화면상 윗행과 다를 수 있고, 그 행이 **필터로 숨겨졌으면 빨강 생략**(`delta_compare_cells`가 `prev_prow=None` 반환 → 파랑만). 선택 변경(`currentChanged`+`selectionChanged`)마다 좌표 재계산→마크 갱신(바뀔 때만 `viewport().update()`); selectionModel은 setModel마다 새로 생기므로 `_wire_selection_signals`(update_table)로 (재)연결+이전 마크 정리. **툴팁**(`ToolTipRole`): 두 행번호+값+관계 표시 — `ToolTipRole`은 *hover 시 그 셀 1개만* 조회(페인트/스크롤과 무관)라 18만 행에도 **무비용**. **포맷 정정**: `_format_delta`의 비숫자(문자) 결과를 `—`→`=`/`≠`로 변경(표시·툴팁이 이 한 곳을 따라감).

- **Δ 셀 색칠 + Δ 열 음영**: ① Δ 셀은 소스 셀이 없어(=`mapToSource` 무효) 색을 프록시 `_delta_color={base:{source_row:QColor}}`에 별도 저장하고, `data()`의 `BackgroundRole`이 *사용자 색 > 첫 행 `R(n)-R(n-1)` 옅은 회색(236) > 없음* 순으로 반환. **세 경로 모두 지원**: 선택+색(`gui_viewer._apply_highlight`가 선택 셀을 실제 셀↔Δ 셀로 분리해 Δ는 `set_delta_cell_colors`로 라우팅), 필터창 값별 색(`gui_header.paint_value`가 `color_delta_rows` 추가 호출), `button_none`=전체 해제(`clear_all_delta_colors`, 소스 전체해제와 짝). `_emit_delta_bg`는 변경된 Δ 열의 전 행을 1회 `dataChanged`(뷰는 보이는 셀만 다시 그림 → 행 수 무관). 첫칸 문구는 `_FIRST_LABEL` 한 곳에서 참조. ② Δ 열 **헤더** 배경은 `gui_header.paintSection`에서 `fillRect(223)`로 약간 어둡게 직접 그림(super 는 스타일시트 240으로 덮으므로 Δ 열만 분기해 수동 렌더; 텍스트는 좌측·수직중앙 동일).

- **열 헤더 폰트 스타일(선택/필터 열 → Bold, Δ 열 → Italic)**: `gui_header.FilterHeaderView.paintSection`에서 상태에 맞는 폰트만 painter에 주입하고 `super().paintSection()`에 위임한다 — 배경·테두리·정렬·말줄임 등 렌더는 native 그대로, `QHeaderView::section` 스타일시트도 **유지**(측정상 주입한 painter 폰트가 super를 거쳐 텍스트까지 도달하므로 배경/말줄임 재구현·스타일시트 제거 불필요). 판정: 선택=`initStyleOptionForIndex`의 `State_On`(필터/델타로 모델이 reset/insert돼도 신뢰 가능), 필터=`src in column_filters`(Δ열은 `has_delta_filter`), Δ=`is_delta_column`.
  - ⚠ **진짜 원인 = `highlightSections`**: 커스텀 헤더는 bare QHeaderView 기본값 `False`라 선택해도 `State_On`이 안 떠 *가로 헤더만* 안 굵어졌다(세로 헤더는 QTableView가 자동으로 True → 사용자가 본 비대칭의 원인). `__init__`에서 `setHighlightSections(True)`로 해결. 켜도 선택 추적은 구간 기반이라 행 수 무관.
  - ⚠ **정정**: QHeaderView는 헤더 `FontRole`을 *아예 안 읽는다*(스타일시트 유무 무관 — 측정 확인). 따라서 헤더 폰트는 FontRole이 아니라 paintSection으로만 가능. (Δ '셀' italic은 delegate 경로라 FontRole로 정상 — 별개 경로.)
  - ⚠ 성능: 보이는 섹션당 `State_On` 조회 ≈6µs(200k행 전체 열 선택도 0.000s) → 행 수 무관. 검증: offscreen은 굵기 미렌더 → 실Windows 육안 최종.

- **`FilterHeaderView` 모듈 분리(`GUI/gui_header.py` 신설)**: 테이블 가로 헤더(`FilterHeaderView`)는 우클릭 필터 팝업창(`FilterWidget`)과 **별개 개념**이라 `gui_filter.py`에서 떼어 `GUI/gui_header.py`로 옮겼다. 의존은 **단방향**(`gui_header`가 `gui_filter.FilterWidget`을 import, 역방향 없음 → 순환 없음). `gui_viewer`의 import 1줄만 `gui_header`로 변경. **`utils/`가 아니라 `GUI/`에 둔 이유**: `utils/`는 비시각 모델/스레드 전용(QWidget 없음)이고 `FilterHeaderView`는 `QHeaderView`(시각 위젯)이라 `gui_*` 컨벤션을 따른다. 동작/로직 변경 없음(순수 이동).

- **복사(`copy_selection`) 대용량 성능**: 전 열/행 복사가 18만 행에서 수 초 멈추던 문제 해결(`selectedColumns()`/`selectedRows()`≈3.6s + `selectedIndexes()` 18만 개 생성 + 셀별 `data()` 호출이 원인). 이제 `selectionModel().selection()`의 **range만** 보고(헤더행/행번호열 포함 여부는 `_spans_cover` 구간 커버리지로), 셀 값은 **소스 `rows` + Δ 스냅샷을 직접** 읽는다(프록시 `accepted_rows()`로 proxy→source 행 벌크 매핑, `source_columns()`로 열 변환, Δ 열은 `delta_snapshot()`). 분리/희소 선택은 열별 병합 구간 + `bisect`로 (row,col) 선택 판정. **측정: 10만 행 열 복사 ≈ 0.04s.** (검색의 `capture_scope`와 동일 철학.)

- **Δ(행간 차이) 가상 열**: 열 헤더 우클릭 필터창의 `☰🡫Δ` 버튼(`button_row_delta`, .ui에 이미 존재) → 그 열 바로 오른쪽에 `Δ [원본헤더]` 가상 열 추가. 각 행 = (그 행 값)−(윗행 값), 첫 행 `R(n)-R(n-1)`, 비숫자(문자)는 `=`/`≠`. 흐름: `gui_header.contextMenuEvent`(클릭 열→`source_column_of`로 소스 열 변환) → `filter_model.add_delta_column/remove_delta_column`.
  - ⚠ **스냅샷(고정)**: `add_delta_column` 시점의 *보이는 행 순서*로 1회 계산해 `_delta_snap[base]={source_row:문자열}`에 저장(`_snapshot`). 이후 필터가 바뀌어도 **재계산 안 함** → 스냅샷 때 숨겨졌던 행은 키가 없어 필터를 풀어도 빈칸, 계산됐던 행은 값 유지. (동적 재계산 아님 — 사용자 합의.) 포맷은 `_format_delta` **한 곳**(추후 '동일/변경' 텍스트로 교체 시 여기만).
  - ⚠ **열 간접화(col_map)**: 프록시가 가정하던 '프록시 열==소스 열'을 깼다. `_col_kind/_col_src/_src_to_pcol`로 프록시↔소스 열 매핑. **`column_filters`·`setFilterForColumn`·`column_values_excluding_self`·`source_rows_with_value`는 전부 소스 열 키**(이미 `row[col]` 인덱싱). 헤더뷰가 클릭 열을 `source_column_of`로 1회 변환해 넘긴다. Δ 열은 `mapToSource` 무효.
  - ⚠ **성능**: 추가 = `beginInsertColumns` 1회 + col_map(열 수만큼) 재구성. 뷰는 보이는 셀만 다시 그려 행 수와 무관하게 즉시. 원본 모델에 실제 열 삽입은 금지(18만 행 `row.insert` 프리즈 + 필터/하이라이트 키 재색인 + 커스텀 프록시가 columnsInserted 자동전달 안 함).
  - ⚠ **Δ 열에서 깨지던 곳 동반 수정**: `search_model.search`는 소스 행을 `row[proxy_col]`로 직접 읽어 Δ 열에서 IndexError → `proxy.source_columns()`로 변환·Δ 열 스킵(검색 제외). `copy_selection` 헤더는 `model.column_label(c)`(⧩ 없음, Δ[..] 포함). `_apply_highlight`는 무효(Δ) 소스 인덱스 스킵.
  - ⚠ 버튼 상태(우클릭 열 따라): 일반 열=추가 / 이미 Δ 보유한 원본 열=비활성 / Δ 열=삭제. 추가는 원본 열에서 1회, 제거는 Δ 열에서. 버튼은 **텍스트 대신 아이콘**(`GUI/res/button_row_delta.png`=추가, `button_row_delta_delete.png`=삭제)을 `setIcon`으로 표시 — popup이 매번 새 인스턴스라 `contextMenuEvent`에서 그때 `setText("")`+`setIcon`(+연결)을 설정. 아이콘 경로는 `self.parent.icon_path`(ViewerWindow 주입).
  - ⚠ **Δ 셀 italic**: `data()`가 Δ 열 `FontRole`에 `_italic_font`(italic) 반환(셀은 delegate가 FontRole을 honor). 헤더 italic은 FontRole이 아니라 `gui_header.paintSection`이 담당(QHeaderView는 헤더 FontRole을 안 읽음 — 변경 이력 "열 헤더 폰트 스타일" 참고).
  - ⚠ **Δ 열 필터는 원본 값이 아니라 Δ값(스냅샷) 기준**: 별도 `delta_filters`(base 열 키, `column_filters`와 분리) + `_rebuild`가 `_delta_snap` 조회로 AND 판정. API: `delta_values_excluding_self`(캐스케이딩 후보값)·`setDeltaFilterForColumn`·`has_delta_filter`·`source_rows_with_delta_value`(Δ값 색칠). 헤더뷰는 `is_delta`로 apply/clear/paint/값목록 경로를 분기. Δ값 필터 걸리면 Δ 헤더에도 `⧩`. `_row_passes(i, exclude_src, exclude_delta)`는 인덱스 기반(두 필터 모두). Δ 열 삭제 시 그 Δ필터도 해제(숨기던 행 복원 위해 reset).

- **필터창 값별 행 색칠**: 열 헤더 우클릭 필터창의 각 값 항목 우측 색버튼 → 그 값을 가진 모든 행을 색칠. 흐름: `gui_filter._FilterItemRow`/`FilterWidget.color_picked` → `FilterHeaderView.paint_value` → `filter_model.source_rows_with_value`(선택 시점 lazy 1회 O(N) 스캔) → `table_model.highlight_rows`.
  - ⚠ 색칠은 별도 row-color 레이어 없이 **기존 `highlight_cells`(셀 단위)에 직접 기록**(수동 셀 색칠과 동일 저장소). 이후 일부 셀만 다른 색으로 덮어쓰기가 자연스러움(우선순위 충돌 회피). 대신 한 값이 수만 행이면 셀 수만큼 메모리 증가. `highlight_rows`는 셀별 `QModelIndex` 생성 없이 좌표로 기록 후 bounding box 한 번만 `dataChanged`.
  - ⚠ 색버튼은 `QHBoxLayout`에 넣지 않고 **줄 우측 오버레이**(`_FilterItemRow.resizeEvent`)로 띄우고 체크박스 폭은 `QSizePolicy.Ignored`. 안 그러면 긴 텍스트가 줄 폭을 늘려 버튼이 화면 밖으로 밀리고 가로 스크롤이 생긴다(텍스트는 버튼 아래로 깔림).
  - ⚠ `QColorDialog` 띄우는 동안 팝업 자동닫힘 방지: `FilterWidget._dialog_open` 가드로 eventFilter의 '바깥 클릭=닫힘'을 막음. 닫힌 뒤 `activateWindow()/raise_()/setFocus()`로 팝업 포커스 복원(안 하면 비활성 팔레트라 체크박스가 전부 해제된 듯 회색으로 보임).
