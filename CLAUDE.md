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
│   ├── csv_loader.py          # CSVLoaderThread (비동기 CSV 로딩)
│   └── view_state.py          # .viewer 영속화 (분석 결과 저장/로드, QColor↔문자열, 원자적 파일 IO)
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
| `CSVLoaderThread` | utils/csv_loader.py | 비동기 CSV 읽기 (utf-8-sig → cp949 폴백). 읽은 데이터는 `pyqtSignal(str, object)`로 전달 (아래 ⚠). 캐시 무효화용 지문(`signature`=stat, `content_hash`=sha256)도 스레드에서 계산해 `t.signature/t.content_hash`로 노출 |
| `view_state` (모듈) | utils/view_state.py | 분석 결과 영속화. `<CSV폴더>\.viewer`(폴더당 1 JSON)에 사용자 입력만 저장/로드. 원자적 쓰기(temp→os.replace)·머지, QColor↔'#rrggbb', envelope/version 게이트 (아래 ⚠ 변경 이력) |
| `EditHistory` | utils/edit_history.py | Undo/Redo(Ctrl+Z/Ctrl+Y) 스택. **CSV별 1개**(cache['history']), 상한 20단계. `Memento`(highlights/fd/widths 3슬라이스 = .viewer 직렬화 재사용)를 push/undo/redo. 순수 저장소 — export·COW 판단은 `ViewerWindow.record_history`/`_make_memento` (아래 ⚠ 변경 이력) |

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
│     'col_widths'  : [열 너비...]          ← CSV 전환 시 너비 복원
│     'signature'   : (size, mtime_ns)      ← 재진입 시 변경 감지 1차 게이트
│     'content_hash': sha256 hex            ← size 같고 mtime만 다를 때 타이브레이커
│     'status'      : 'ok' | 'empty' | 'fail'
│     'history'     : EditHistory            ← Undo/Redo 스택(CSV별 독립, 상한 20). 모델 최초 표시 시 baseline 1개로 생성
│   }
├── FilterHeaderView  (수평 헤더 - 우클릭 열 필터)
├── CSVLoaderThread   (self.loader_threads 로 추적/정리)
├── SearchModel       (self.search_model)
└── EditHistory       (cache[csv]['history'] - Ctrl+Z/Ctrl+Y, 액션 단위 스냅샷)
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

- **Δ 열 필터 팝업에서 첫 행 안내문구(`r(n)-r(n-1)`) 제외**: Δ 열 우클릭 필터 목록이 첫 보이는 행의 placeholder까지 후보값으로 잡던 것을 빼고 **2번째 (보이는) 행부터** 실제 Δ값만 수집한다. `filter_model.delta_values_excluding_self`에서 `v == _FIRST_LABEL` 스킵 1줄. ⚠ 스냅샷(`_delta_snap`)엔 라벨이 그대로 남아 **표시·값별 색칠·검색은 무영향**(드롭다운 후보에서만 제외) → 첫(기준) 행은 Δ값 필터로 숨길 수 없음(의도된 동작). 첫 행 판정은 인덱스가 아니라 `_FIRST_LABEL` 값으로 — 스냅샷 당시 첫 *보이는* 행이 곧 그 라벨이라 정확. 검증: offscreen(첫행 라벨 제외·실제 Δ값 `{3,0,7}`만·스냅샷 라벨 보존) PASS.

- **엑셀형 다중선택 동시조정(열너비/행높이) — 드래그 종료 시 일괄 적용**: 완전 선택된 열 N개(또는 행 N개) 중 하나의 경계를 드래그하면, **손을 떼는 순간** 나머지 선택분이 같은 크기로 스냅된다(드래그 *중*엔 잡은 하나만 실시간=Qt 기본). 신설 `_finalize_resize`·`_full_selection_sections`·`_on_row_resized`·`eventFilter` + `_propagating`/`_pending_h`/`_pending_v` 플래그(`gui_viewer`만, `gui_header` 무변경). `_on_section_resized`에 `_pending_h` 캡처+`_propagating` 가드 1줄씩.
  - ⚠ **행은 세션 한정**(사용자 합의): 행높이 전파는 동작하지만 `.viewer` 저장·Undo/Redo **대상 아님** → cache/Memento/`.viewer` 슬라이스 **신설 안 함**(작업량 대폭 축소). CSV 전환·모델 재부착 시 기본 20으로 리셋됨(per-CSV 행높이 저장 없음). **열은 기존 너비 저장/Undo 그대로** — 전파 후 기존 `_width_timer`가 잠시 뒤 `record_history({'widths'})` 1회로 피어 포함 1단계 기록(추가 코드 0).
  - ⚠ **release 감지 = 헤더 viewport의 eventFilter + `singleShot(0)`**: QHeaderView엔 'resize 종료' 신호가 없다(`sectionResized`는 드래그 *중* 연속 발생, 끝을 모름). 그래서 *어느 섹션을 어떤 크기로*는 `sectionResized`가 `_pending_h/v`에 캡처하고, *언제*(종료)는 헤더 mouse-release를 eventFilter로 잡아 `singleShot(0)`(헤더 자체 release 처리 직후=최종 크기 확정 후)로 `_finalize_resize` 호출. `_pending_*`가 없으면 no-op라 단순 헤더 클릭/우클릭(필터 팝업)엔 무영향. LeftButton release만.
    - ⚠⚠ **반드시 `header.viewport()`에 필터를 건다 — `header` 자체에 걸면 release가 안 온다**: QHeaderView는 QAbstractScrollArea라 마우스 이벤트가 헤더 객체가 아니라 그 `viewport()`로 전달된다. `header.installEventFilter`로 걸면 `eventFilter`가 release를 **한 번도 못 받아** 전파가 통째로 죽는다(실제로 이 버그를 겪음 — offscreen 단위테스트가 `eventFilter`를 *직접 호출*해 통과시켜 놓쳤다). 검증은 `QTest.mousePress/Move/Release`로 **실제 이벤트 디스패치**를 태워야 잡힌다(직접 호출 금지). `obj is header.viewport()`로 비교.
  - ⚠ **`_propagating` 가드 필수**: 전파의 `resizeSection`이 다시 `sectionResized`를 emit → `_on_section_resized`/`_on_row_resized`로 재귀. 이 플래그 ON 구간엔 두 핸들러가 early-return(재귀·재기록·`_pending_*` 덮어쓰기 방지). 기존 `_suppress_width_record`(프로그램적 변경: 모델부착·undo 복원)와 **별개** — 전파는 사용자 제스처라 그쪽으로 억제하면 안 됨(아래 Undo 항목의 예고대로).
  - ⚠ **18만 행 함정 회피**: "완전 선택된 열/행"은 `selectedColumns()/selectedRows()`(열·행 전체선택 시 수 초) 대신 `selectionModel().selection()`의 **range 직접 분석**(`_full_selection_sections`, `_spans_cover` 재사용 → O(range)). `capture_scope`·`copy_selection`과 동일 철학. 측정: 6만 행 전체선택 판정 **1.6ms**.
  - ⚠ **대량 행 전파 = `setUpdatesEnabled(False)`로 repaint thrash 차단**: 전 행(수만) 선택 후 높이 조정 시 매 섹션 resizeSection의 repaint가 누적돼 **6만 행 929ms→43ms(~21x)**. 시그널은 안 막아(뷰 레이아웃 일관성 유지) 끝나고 1회만 다시 그림. 180k 추정 ~130ms(1회성, 종료 시점). 전파는 잡은 섹션(idx) 제외 — 그건 드래그가 이미 적용.
  - ⚠ **전파 조건 = 잡은 섹션이 다중 완전선택의 일원(`len>=2 and idx in sections`)**: 단일/선택 밖 섹션 드래그는 그것만 변경(엑셀 동일). 셀 블록만 선택(헤더 미선택)은 `_spans_cover` 불충족 → 전파 없음(엑셀은 열/행 헤더 전체선택 필요). 검증: offscreen 9케이스(열/행 판정·분리선택·전파·열 Undo 1단계·단일/미포함 no-op·6만 행 안전) + **`QTest` 실 마우스 이벤트 e2e**(헤더 클릭 선택→경계 드래그→release 전파: 열 `[80,130,130,130,80,80]`·행 `[20,20,20]→[45,45,45]`, viewport 버그 회귀 포함). **드래그 체감/스냅 렌더는 실Windows 육안 최종.**

- **18만 행 열 헤더 선택 렉 제거(가로 헤더 repaint 1,600ms→0.3ms, ~1,300x)**: 열 헤더를 선택하면 그 열 전체(18만 셀)가 선택되는데, 가로 헤더를 다시 그릴 때마다 Qt 기본 `initStyleOptionForIndex`가 섹션 `selectedPosition`(인접 선택 연결선)을 구하려 `QItemSelectionModel.isColumnSelected()`를 호출 → 열이 '전체 선택'이면 true 단축 불가라 **전 행을 순회하며 행마다 프록시 `flags()`(→`mapToSource`)를 18만 번** 부른다(헤더 repaint 1회 ≈ 1.6s). 선택뿐 아니라 **헤더 호버·가로스크롤·포커스마다 재실행**돼 "계속 얼어붙는" 현상이었다. `selectColumn` 자체(선택 저장)는 0.1ms로 무죄. 해법: `gui_header.FilterHeaderView.initStyleOptionForIndex`를 오버라이드(이 메서드는 **virtual**이라 `super().paintSection` 내부 호출에도 반영됨)해, super 가 도는 동안만 프록시 `rowCount`를 0으로 위장(`filter_model._fast_header_paint`)해 그 전행 루프를 빈 루프로 만들고, 선택 상태(`State_On`=Bold 트리거)는 **selection 범위로 O(range) 직접 판정**(`_column_intersects_selection`). `gui_header`(+2 메서드) + `filter_model`(`_fast_header_paint` 플래그 + `rowCount` 가드 1줄).
  - ⚠ **`setHighlightSections(False)`로는 안 줄어든다** — 이건 선택 강조가 아니라 `selectedPosition`(인접 섹션 연결선) 계산이라서. 그래서 아래 "열 헤더 폰트 스타일" 항목의 *"State_On ≈6µs·행 수 무관"* 기록은 **부정확했음**(열을 실제로 전체 선택한 상태로 측정 안 함 → 정정함). [[measure-before-big-refactor]]
  - ⚠ **rowCount 0 위장이 안전한 이유**: 가로 헤더 스타일 옵션은 행 수와 무관(섹션 기하는 열 기준) → 렌더 영향 0. 측정상 패치 전후 **헤더 픽셀 diff=0**. 동기·단일스레드 구간이라 try/finally 안에서 set→super→restore, 다른 코드가 그 사이 rowCount를 관측하지 않음. 세로 헤더(행 선택)는 열(3~4개)만 훑어 원래 빠르고 이 오버라이드도 안 타므로 무관.
  - ⚠ **State_On 직접 판정 = 원본과 동치**: 원본 `State_On`은 Qt `columnIntersectsSelection`(열이 선택과 겹치면 True)이라, 범위 판정(`any(rng.left()<=col<=rng.right())`)과 동일 의미 → Bold 동작 보존(단일셀·열전체·다중열 선택 모두 검증). `selectedPosition=NotAdjacent`로 두어 native 선택 연결선/음영(State_Sunken)은 생략 — 의도된 선택 표시는 Bold라 무방(필요시 '열 완전선택' 여부도 range로 싸게 판정해 State_Sunken 복원 가능).
  - ⚠ 검증: offscreen 회귀(18만 행 + Δ열 + ⧩필터, 선택열 헤더 repaint 0.33ms·연속 0.31ms/회, State_On 정확, flags/mapToSource 18만→0, 렌더 픽셀 동일) PASS. **Bold 굵기 자체는 offscreen 미렌더 → 실Windows 육안 최종**(기존 헤더 폰트와 동일 한계).

- **취소/다시실행(Ctrl+Z / Ctrl+Y, +Ctrl+Shift+Z, 툴바 `button_undo`/`button_redo`)**: 분석 편집(하이라이트·열필터·Δ·**열너비**)을 **CSV별 독립** 스택에 **액션 단위**로 쌓아 되돌린다(상한 20단계). 스크롤·검색·CSV선택은 대상 아님. 신설 `utils/edit_history.py`(`EditHistory`+`Memento`) + `gui_viewer`(`record_history`·`undo`/`redo`·`_restore_memento`·`_make_memento`·너비 디바운스·`_update_undo_buttons`) + `gui_header`(5개 액션 꼬리에 `record_history` 1줄) + `filter_model.has_delta_colors`(전체해제 no-op 판정).
  - ⚠ **버튼 활성/비활성 + 아이콘 = `_update_undo_buttons`(can_undo/can_redo) 한 곳**: enabled 와 함께 **전용 아이콘**도 교체(`button_undo/redo.png` ↔ `button_undo/redo_disable.png`). ⚠ 비활성 버튼은 Qt 가 아이콘을 **Disabled 모드로 한 번 더 페이드**하므로, disable 이미지를 `QIcon.addPixmap(pm, Mode.Disabled)`로 그 모드에 직접 등록해 추가 페이드 없이 그림 그대로 보이게 한다(미등록 시 이중으로 흐려짐). 갱신 호출 지점 — 액션 기록 후(record_history: undo 활성+**redo 가지 폐기→비활성**), undo/redo 후, `update_table` 꼬리/빈·실패 분기(CSV 전환·모델없음 반영), `_start_loading`(로딩 중), `_load_folder`·`reload_csv_list` 모델 비울 때. 모델/히스토리 없으면 둘 다 비활성.
  - ⚠ **셀 단위 기록 안 함 — `.viewer` 직렬화 재사용**: Memento = 3슬라이스(`highlights`=`export_highlights`, `fd`=`export_state`, `widths`=헤더 너비 리스트). 복원은 `.viewer` 자동복원과 **동일 경로**(`restore_highlights`(소스, emit 없음) → `restore_state`(프록시 reset 이 새 하이라이트·열·필터로 보이는 셀 전체 리페인트) → 너비 `resizeSection`). 1→2 순서라 별도 dataChanged emit 불필요. Δ↔필터 상호작용도 통째 복원이라 안 깨짐.
  - ⚠ **Copy-on-Write**: 액션이 안 바꾼 슬라이스는 직전 Memento의 **객체를 참조 공유**(`_make_memento`가 `changed` 집합에 없는 슬라이스를 prev에서 그대로 가져옴). 필터/너비 액션 20번 해도 highlights 스냅샷은 1개 → 메모리 거의 0. 대량 값별색칠 반복만 그 횟수만큼 누적(상한 20). **Memento 값은 불변 취급**(export=새 객체, restore=read-only) — 절대 제자리 수정 금지.
  - ⚠ **일괄 액션 = 정확히 1 단계**: `record_history`는 각 사용자 액션 핸들러 **꼬리에서 1회만** 호출(셀/열/행 루프 안 금지). 여러 셀 색칠·값별 수만 행 색칠·전체해제·여러 열 동시 너비조정 모두 1단계. 색칠은 이미 `highlight_cell`/`highlight_rows` 1회 호출이라 자동 충족.
  - ⚠ **열너비만 연속 신호 → 디바운스(350ms)로 1제스처=1단계**: `sectionResized`는 드래그 중 연속 발생(+향후 '여러 열 동시조정'은 열마다 여러 번) → `_width_timer` 가 묶어 1회 기록. **명시적 너비 기록 시 record_history 가 그 타이머를 stop**(Δ 추가/삭제의 열삽입이 emit하는 sectionResized 와의 중복 단계 방지). CSV 전환 직전 `_close_ui_overlays` 가 보류 중 너비변경을 직전 CSV에 flush('보이는 것=히스토리 top' 유지).
  - ⚠ **프로그램적 너비변경 억제(`_suppress_width_record`)**: `update_table`(모델부착·기본너비·너비복원)·`_restore_memento`(undo 중 resizeSection)도 `sectionResized` 를 emit → 이 플래그 ON 구간에선 기록 안 함(undo 중 재귀·셋업 중 오기록 방지). **향후 '여러 열 동시조정'의 propagation 은 사용자 제스처라 억제하면 안 됨**(디바운스로 1단계가 되도록 둘 것).
  - ⚠ **baseline = 모델 최초 표시 직후**(`update_table` 꼬리 `_ensure_baseline_history`, 너비/스크롤 복원 끝난 시점). 캐시 재사용 경로는 history가 이미 있어 skip → 세션 편집 보존. **파일 외부변경/F5/reload 로 cache 엔트리 폐기 시 그 CSV 히스토리도 함께 폐기**(상태 리셋과 일치 — 추가 코드 없음).
  - ⚠ `selectionModel`은 restore_state의 reset 으로 유지(setModel 아님)라 Δ 비교 핸들러 재연결 불필요. 복원 후 Δ/검색 테두리 마크만 초기화. 검색바 열려있으면 재검색(행 집합 변동 가능).
  - ⚠ 검증: offscreen 4묶음(EditHistory 단위〔push/undo/redo·redo절단·20상한 정확히 20회〕 · ViewerWindow COW 참조공유〔`is`〕+undo/redo+Δ 열수변동 · 일괄색칠 1단계+CSV별 독립 · no-op 가드) PASS. **너비 디바운스 체감·렌더는 offscreen 미검증 → 실Windows 육안 최종.**

- **MSI 설치/제거 '시작 전' 영문 Yes/No 확인창**: 더블클릭/UI 실행 시 작업 전에 `Do you wish to install CSV Viewer?`(설치)·`Do you wish to uninstall CSV Viewer?`(제거)를 띄우고 **No=취소·Yes=진행**. 신설 `installer/confirm_install.vbs`·`confirm_uninstall.vbs`(함수형 VBScript) + `CSVViewer.wxs`에 `Binary`+`CustomAction`(ConfirmInstall/ConfirmUninstall) + `InstallUISequence` `Before="ExecuteAction"` 2줄.
  - ⚠ **깨끗한 취소 = 함수형 VBScript 반환 2(user-exit)**: 인라인 스크립트(`Script=`/`ScriptSourceFile` — 완료 팝업 방식)는 success/실패만 반환 → No 취소가 '치명적오류'로 뜬다. 반환값으로 취소하려면 **함수 타깃**(`BinaryRef`+`VBScriptCall="함수명"`, Binary 테이블 임베드)이 필수: 함수가 `2`(msiDoActionStatusUserExit)=취소·`1`(success)=진행 반환. MSI 표 검증 type=**4102**(0x1000 64비트스크립트+0x6 VBScript+Binary소스, **Continue비트 0x40 없음**=반환검사) vs 완료 팝업 4198(0x40=Return=ignore).
  - ⚠ **`Return="check"` 필수**(완료 팝업은 `ignore`): 반환 취소를 반영하려면 check. 부작용 — VBScript 엔진이 죽은 PC에선 확인창 *실패=설치 중단*(완료 팝업은 무시). 이 앱은 이미 VBScript 팝업 의존 + Win11 기본 탑재라 수용(WiX `WIX1163` VBScript deprecated 경고는 신규/기존 4개 CA 공통).
  - ⚠ **스케줄=`ExecuteAction` 전** → No면 실제 파일/환경변수/레지스트리 변경이 시작되기 전에 중단(아무것도 안 바뀜). 검증 시퀀스: SetREMOVE(999)→CostFinalize(1000)→ConfirmInstall(1298)/ConfirmUninstall(1299)→ExecuteAction(1300)→완료팝업(1301/2). REMOVE가 confirm보다 먼저 세팅돼 조건이 정확.
  - ⚠ **조건=완료 팝업과 동일**: ConfirmInstall=`NOT Installed AND NOT REMOVE`(신규설치)·ConfirmUninstall=`REMOVE="ALL"`(토글/`/x` 제거). `/qn` 무인설치는 UI 시퀀스를 안 타 확인창 없이 진행(기존 팝업과 동일). 검증: `wix build` PASS + MSI 표(CustomAction/InstallUISequence/Binary) 덤프 확인. **실제 Yes/No 동작은 관리자 실설치 필요 → 실Windows 육안 최종.**

- **분석 결과 저장/자동복원(`.viewer`)**: 하이라이트·열필터·Δ·열너비·스크롤을 **CSV 폴더당 1개 `<폴더>\.viewer`(JSON)** 에 Ctrl+S로 저장(현재 CSV 1개)했다가, 다음에 그 CSV를 **처음 열 때 내용 해시(csv_sha256)가 일치하면** 자동 복원. 신설 `utils/view_state.py`(폴더 파일 IO·QColor↔'#rrggbb'·envelope/version 게이트) + 모델 `export/restore` 메서드 + `gui_viewer`(Ctrl+S 바인딩·`_apply_saved_state`·저장 토스트).
  - ⚠ **proxy/cache 통째 저장 안 함**: 모델 피클은 본문(18만 행) 중복+Qt 버전의존이라 금지. **사용자 입력만** 평면 JSON으로 추출하고 파생(`_accepted`·col_map·Δ값)은 동일 파일에서 재계산. 파일 본문 자체는 저장 안 함(해시로 동일성만 확인).
  - ⚠ **하이라이트 저장 포맷 = 색→열→행(`{색: {열: [행]}}`), 메모리는 `{(행,열): 색}` 유지**: 보통 행이 열보다 압도적으로 많아 파일엔 열로 묶어 좌표쌍 `[행,열]` 반복을 없앤다(bulk 색칠 ~절반↓). 변환은 `export/restore_highlights`의 O(셀 수) 1회 루프(Ctrl+S·로드 때만 — 핫패스 아님). **⚠ 메모리 `highlight_cells`는 같은 구조로 바꾸지 말 것**: `data()`의 BackgroundRole이 스크롤마다 보이는 셀마다 `get((row,col))` **O(1)**로 색을 찾는 페인트 핫패스라, 색→열→행으로 바꾸면 '이 셀 무슨 색?'에 전 색의 행목록을 뒤져야(`row in [...]`) 18만 행 렌더가 느려진다 — **저장 포맷 ↔ 메모리 구조 분리가 핵심**. `restore_highlights`는 `int(col)`로 받아 in-memory(int 열키)·디스크 JSON(str 열키)·구포맷 `[[행,열],...]`(하위호환) 모두 처리. Δ색은 열 개념이 없어(`{색:[행]}`) 그대로 둠.
  - ⚠ **Δ는 '행 리스트'가 아니라 '스냅샷 당시 열필터(원인)'만 저장(Option 2)**: snapshot_rows를 다 박으면 무필터 Δ는 `range(N)`=18만 정수(~1.2MB/Δ)라 무식 → 대신 `_delta_snap_filter[base]`(`add_delta_column` 시점의 `column_filters` 복사, 보통 빈 dict)만 저장하고, 복원 때 그 필터를 **같은 파일에 재적용→`_compute_snapshot`로 값 재생**. "필터 걸고 Δ vs 안 걸고 Δ"가 서로 다른 snapshot_filter로 저장돼 각각 정확히 재현. (정렬 기능이 없어 보이는 행=오름차순 → 순서 저장 불필요·멤버십만.) 한계: 스냅샷 당시 *다른 Δ의 값 필터*까지 걸렸던 극히 드문 경우는 열필터만 반영. (`_snapshot`은 `_compute_snapshot(self._accepted)` 위임 — add/restore 단일 로직.)
  - ⚠ **복원 시점 = 모델 최초 생성 분기에서만**(`update_table` else, 뷰 부착 *전*). 그래야 세션 중 변경이 .viewer로 덮어써지지 않고(캐시 재사용 경로는 재적용 안 함), 하이라이트 `dataChanged` emit도 불필요. **순서: 필터·Δ(`restore_state`) → 하이라이트(`restore_highlights`) → col_widths → 스크롤** — 뒤 둘은 기존 `update_table` 꼬리가 cache의 `col_widths`/`last_view`를 읽어 처리하므로 `_apply_saved_state`가 그 두 키를 cache에 주입한다(Δ 복원 후라야 열 수가 맞아 `len==hdr.count()` 가드 통과).
  - ⚠ **F5(표 포커스, `reload_current_csv`)는 .viewer를 의도적으로 건너뛴다 = raw 새로고침**: 현재 CSV만 캐시 폐기 후 **자동복원 없이** 다시 로드(사용자 요청). 구현은 `_skip_viewer`(csv명 set) — reload 시 add, `_apply_saved_state` 진입 즉시 그 이름이면 discard+return(1회성). 빈/실패 경로(`update_table`의 무데이터 early-return)에서도 discard해 스테일 마크 방지. **.viewer 파일 자체는 안 건드림**(다시 저장하려면 Ctrl+S). 비-F5 경로(첫 열람·새 창)는 그대로 자동복원. 리스트 포커스 F5(`reload_csv_list`)는 모델을 새로 안 만들어 .viewer와 무관(변경 없음).
  - ⚠ **해시 비용 0**: loader가 로드 성공 시 이미 계산한 `content_hash` 재활용(저장=박기, 복원=비교)이라 GUI 스레드 재해싱 없음. `content_hash`는 status=='ok'에서만 생겨 '분석 있는 경우'와 일치.
  - ⚠ **저장은 read-modify-write 머지**(`save_file_state`가 .viewer를 다시 읽어 현재 CSV 항목만 교체→원자적 재기록) → 다른 CSV 저장본 보존. col_widths/scroll은 cache가 'CSV 전환 시'에만 갱신되므로 **저장 시점엔 뷰에서 직접 캡처**. 깨진/구버전 .viewer는 envelope·version 게이트 + 복원 `try/except`로 조용히 무시(**CSV 열람 절대 안 막음**). `.viewer`/`.viewer-*.tmp`는 `.csv`로 안 끝나 목록·F5에서 제외. 다중 프로세스 동시편집은 사용자 합의로 배제(머지·원자적 쓰기로 부분 안전).
  - ⚠ **JSON 포맷 = 커스텀 `_pretty`**(구조는 들여쓰고 *잎 배열은 한 줄*): `json.dump(indent=2)`로 바꾸지 말 것 — 잎 배열이 원소당 한 줄로 터져 가독성↓ + 값별 행 색칠(수만 행)이 파일을 수십 배로 부풀린다. 규칙: dict와 '원소가 dict인 목록(column_filters/deltas)'만 줄바꿈, 스칼라 리스트(highlights의 행목록·hidden·col_widths·scroll)는 compact 한 줄. ⚠ **dict 키는 `json.dumps(str(k))`로 직접 문자열화** — highlights 새 포맷의 '열' 키가 int라, 표준 `json.dump`(자동)와 달리 커스텀 프린터는 수동 변환해야 유효 JSON 키(`"5":`)가 된다(안 그러면 `5:`로 깨짐). 표준 JSON이라 `json.load` 그대로 읽힘.
  - ⚠ 검증: offscreen 유닛 7(필터+하이라이트 라운드트립〔색→열→행 포맷·구포맷 하위호환·int/str 열키·범위밖 무시·폴더왕복〕·Option2 Δ→필터·필터→Δ·Δ색/Δ필터·파일머지·깨진파일·JSON 전과정) + 통합 3(로드→분석→Ctrl+S 기록·새 창 자동복원·해시불일치 차단) PASS. **저장 토스트/하이라이트 *렌더*는 offscreen 미검증 → 실Windows 육안 최종.**

- **전체검색 시작 위치 = 내 현재 위치(앵커)부터**: Ctrl+F 전체검색이 항상 1/N부터 시작하던 것을, 일반 에디터처럼 **내 위치 이상인 첫 매치(예: 103/200)부터** 시작하도록(`search_model.search`의 `current_index=0`을 분기). **범위검색은 그대로 1/N**, 전체검색만 `bisect_left(matches, anchor)`. **앵커 = 마지막 선택(현재) 셀이 유효하면 그 (행,열), 없으면 화면 최상단 보이는 행(열=0)** (`_anchor_rowcol`; 사용자 합의 — *가시성 체크 없이 선택 셀 우선*). `matches`가 (row,col) 오름차순이라 **튜플 비교가 곧 '행 우선·열 다음'** → bisect O(log N), 헤더 매치(-1,*)는 앵커행(≥0)보다 작아 자동 제외. 앵커 **'이상(>=)'**이라 자기 셀이 매치면 포함(같은 검색어 Enter 재검색은 idempotent, 전진은 F3 — 기존 Enter=재검색/F3=다음 동작 유지). 매치를 다 지나쳤으면 1번으로 wrap. ⚠ 느린 `selectedRows()/selectedColumns()` 안 쓰고 `currentIndex()`+`rowAt(0)` O(1)만(CLAUDE.md 선택 API 함정 회피). 검증: offscreen 6케이스(선택셀 시작·열우선·끝에서 wrap·범위검색 무시·무선택 fallback·앵커값) PASS. `search_model.py`만 변경.

- **검색 현재 셀 회색 테두리**: Ctrl+F로 매치를 하나씩 이동(next/prev)할 때 현재 매치 셀에 **회색 2px 테두리**(기존 scrollTo/선택 이동에 +α). 기존 `CompareBorderDelegate`(Δ 비교 파랑/빨강 테두리, 테이블에 1개만 깔림)에 **검색 마크 추가**(`_search`·`set_search_mark`·`GRAY` 상수, `_draw_border` 헬퍼로 공통화) — 새 델리게이트 안 만들고 재사용(Δ와 독립; 같은 셀이면 회색이 위). 좌표는 Δ와 동일 프록시 (row,col). **델리게이트 set/clear는 GUI 레이어에서**: `search_model`(utils)이 델리게이트를 안 만지도록, 위치변경마다 이미 호출되는 `search_gui_update` 슬롯에서 `_update_search_mark`로 갱신. 해제 3곳 — 빈 검색·검색바 닫기(`search_gui_hide`)·CSV 전환(`_wire_selection_signals`, 모델 교체). **유지 정책(사용자 합의)**: *다음 이동/검색바 닫기까지 유지*(수동 클릭으론 안 사라짐 → selectionChanged 훅 불필요). ⚠ 헤더 매치(행 -1)는 기존대로 열 전체 선택만, 셀 테두리 없음(델리게이트는 데이터 셀만 paint → `_update_search_mark`가 행<0이면 None). 성능: paint당 튜플 비교 1회 + 이동당 `viewport().update()` 1회 → 행 수 무관(Δ와 동일). ⚠ **회색 렌더는 offscreen 미검증(Δ 테두리와 같은 사유) → 실Windows 육안 최종**; 마크 *로직*은 offscreen 7케이스(첫매치·next·prev·헤더제외·빈검색/닫기/CSV전환 해제) PASS. 색/두께는 `GRAY` 상수 한 곳.

- **CSV 재진입 시 파일 변경 감지 → 캐시 무효화**: 다른 CSV 보다 돌아올 때, 캐시 존재 여부만 보던 것을 디스크 파일과 비교해 바뀌었으면 그 CSV 캐시를 폐기·재로드하도록 변경(`clicked_csv_list`에 `_cache_is_fresh` 게이트 추가). **방식 = stat 게이트 + 해시 타이브레이커**(사용자 합의): ① `stat`(size+mtime_ns) 같으면 신선 — 해시 안 함(절대다수 경로, **≈7µs·파일 크기 무관**) · ② size 다르면 내용 변경 확정이라 해시 생략하고 폐기 · ③ **size 같고 mtime만 다를 때만** 내용 `sha256` 비교(touch/동일 재저장이면 캐시 유지+`signature` 갱신, 다르면 폐기). ⚠ **왜 전체해시를 기본으로 안 쓰나**: 이 검사는 *재진입마다 GUI 스레드에서 동기로* 치르는데, 105MB 실측 전체해시 warm 41~130ms(cold 수백ms~초)라 매 탭전환 끊김+예산(0.1~0.3s) 위태 → `os.stat`(0.007ms)이 1만 배 싸고 외부 재생성은 mtime/size로 100% 잡힘. 지문 저장: 로더 스레드가 `signature`(stat, 성공/빈/실패 공통)+`content_hash`(sha256, **성공 시에만**)를 계산해 `t.signature/t.content_hash`로 노출 → 완료 콜백이 cache에 저장(`'signature'`,`'content_hash'` 키 신설). ⚠ **변경 감지 시 그 CSV의 뷰 상태(필터·하이라이트·Δ열·스크롤·열너비) 전부 초기화**(내용이 달라지면 값 기준 필터/Δ가 무의미 — 사용자 합의). ⚠ 미세 TOCTOU(로더 emit~콜백 사이 재변경)는 이 앱 시나리오상 사실상 0; 완전 무경합 원하면 로더 `os.fstat(열린 핸들)`로 전환 가능. ⚠ F5(`reload_csv_list`)는 기존대로 추가/삭제만 동기화(변경 자동무효화는 범위 밖). 검증: offscreen 9개(판정 로직)+3개(비동기 전 과정: 최초 지문 기록·미변경 캐시 유지·외부 변경 자동 재로드) 모두 PASS.

- **ESC 연타 닫기 간격 상수화(0.5초)**: `ViewerWindow.ESC_INTERVAL_SEC=0.5`(초)로 1초→0.5초 단축·매크로화. ⚠ **연타 유효 간격과 ESC 안내 토스트 노출 시간을 의도적으로 같은 상수에 묶음**(토스트=`int(상수×1000)`ms, `show_esc_message`). 둘을 독립값으로 '정리'하지 말 것 — 안내가 떠 있는 동안 = 재입력 유효 시간이라는 UX 의도.

- **열 너비 per-CSV 저장/복원**: CSV 전환 시 열 너비가 기본값(80)으로 리셋되던 문제 해결. 원인은 너비가 **단일 공유 헤더에만** 살아있고 per-CSV 저장이 없던 것(신규/리로드 로드의 `setModel(None)`이 매번 기본값으로 리셋 — 측정 확인; 직접 swap은 sticky하나 CSV별이 아니라 무의미). 해결: `last_view`(스크롤 위치)와 **동일 패턴**으로 cache에 `col_widths` 키 추가 — 떠날 때 `clicked_csv_list`에서 `[sectionSize(c)...]` 캡처, 들어올 때 `update_table`에서 `resizeSection` 복원. **proxy model이 아니라 cache에 저장**(너비는 뷰 기하 → 데이터 프록시에 두면 레이어 위반; cache는 이미 last_view를 담는 per-CSV 뷰상태 저장소). ⚠ 복원은 **가로 스크롤(last_view)보다 먼저** 적용(너비가 스크롤 범위를 바꿔 클램프 방지). ⚠ `len(col_widths)==header.count()` 가드 = Δ 열 안전장치(불일치 시 기본값 유지). O(열 수)라 행 수 무관(18만 행 함정 없음). 검증: offscreen 통합테스트로 복원·열수 상이(6↔8)·무bleed·Δ열(6→7) 모두 PASS. 세션 한정(프로세스 독립·무상태라 런치 간 영속화는 범위 밖).

- **Δ 셀 선택 시 비교셀 테두리 + hover 툴팁 + 문자 비교 `=`/`≠`**: Δ 셀(첫 행 제외) 선택 시 그 차이가 비교한 두 부모셀에 테두리(현재 R(n)=파랑, 이전 R(n-1)=빨강) — `gui_delegate.CompareBorderDelegate`가 `super().paint()` 위에 overlay(색·두께는 delegate 상수, 사용자가 직접 조정). **'이전 행'은 *스냅샷 시점의 이전 보이는 행*** (`_snapshot`이 `_delta_prev[base][sr]=prev_sr`를 동시 저장)이라 화면상 윗행과 다를 수 있고, 그 행이 **필터로 숨겨졌으면 빨강 생략**(`delta_compare_cells`가 `prev_prow=None` 반환 → 파랑만). 선택 변경(`currentChanged`+`selectionChanged`)마다 좌표 재계산→마크 갱신(바뀔 때만 `viewport().update()`); selectionModel은 setModel마다 새로 생기므로 `_wire_selection_signals`(update_table)로 (재)연결+이전 마크 정리. **툴팁**(`ToolTipRole`): 두 행번호+값+관계 표시 — `ToolTipRole`은 *hover 시 그 셀 1개만* 조회(페인트/스크롤과 무관)라 18만 행에도 **무비용**. **포맷 정정**: `_format_delta`의 비숫자(문자) 결과를 `—`→`=`/`≠`로 변경(표시·툴팁이 이 한 곳을 따라감).

- **Δ 셀 색칠 + Δ 열 음영**: ① Δ 셀은 소스 셀이 없어(=`mapToSource` 무효) 색을 프록시 `_delta_color={base:{source_row:QColor}}`에 별도 저장하고, `data()`의 `BackgroundRole`이 *사용자 색 > 첫 행 `R(n)-R(n-1)` 옅은 회색(236) > 없음* 순으로 반환. **세 경로 모두 지원**: 선택+색(`gui_viewer._apply_highlight`가 선택 셀을 실제 셀↔Δ 셀로 분리해 Δ는 `set_delta_cell_colors`로 라우팅), 필터창 값별 색(`gui_header.paint_value`가 `color_delta_rows` 추가 호출), `button_none`=전체 해제(`clear_all_delta_colors`, 소스 전체해제와 짝). `_emit_delta_bg`는 변경된 Δ 열의 전 행을 1회 `dataChanged`(뷰는 보이는 셀만 다시 그림 → 행 수 무관). 첫칸 문구는 `_FIRST_LABEL` 한 곳에서 참조. ② Δ 열 **헤더** 배경은 `gui_header.paintSection`에서 `fillRect(223)`로 약간 어둡게 직접 그림(super 는 스타일시트 240으로 덮으므로 Δ 열만 분기해 수동 렌더; 텍스트는 좌측·수직중앙 동일).

- **열 헤더 폰트 스타일(선택/필터 열 → Bold, Δ 열 → Italic)**: `gui_header.FilterHeaderView.paintSection`에서 상태에 맞는 폰트만 painter에 주입하고 `super().paintSection()`에 위임한다 — 배경·테두리·정렬·말줄임 등 렌더는 native 그대로, `QHeaderView::section` 스타일시트도 **유지**(측정상 주입한 painter 폰트가 super를 거쳐 텍스트까지 도달하므로 배경/말줄임 재구현·스타일시트 제거 불필요). 판정: 선택=`initStyleOptionForIndex`의 `State_On`(필터/델타로 모델이 reset/insert돼도 신뢰 가능), 필터=`src in column_filters`(Δ열은 `has_delta_filter`), Δ=`is_delta_column`.
  - ⚠ **진짜 원인 = `highlightSections`**: 커스텀 헤더는 bare QHeaderView 기본값 `False`라 선택해도 `State_On`이 안 떠 *가로 헤더만* 안 굵어졌다(세로 헤더는 QTableView가 자동으로 True → 사용자가 본 비대칭의 원인). `__init__`에서 `setHighlightSections(True)`로 해결. 켜도 선택 추적은 구간 기반이라 행 수 무관.
  - ⚠ **정정**: QHeaderView는 헤더 `FontRole`을 *아예 안 읽는다*(스타일시트 유무 무관 — 측정 확인). 따라서 헤더 폰트는 FontRole이 아니라 paintSection으로만 가능. (Δ '셀' italic은 delegate 경로라 FontRole로 정상 — 별개 경로.)
  - ⚠ 성능 **정정**: 과거 "보이는 섹션당 `State_On` 조회 ≈6µs·행 수 무관"이라 적었으나 **틀렸다** — 열을 *실제로 전체 선택*한 상태에서 측정하지 않은 탓. `paintSection`이 부르는 `initStyleOptionForIndex`는 Qt 기본 구현에서 `isColumnSelected()`로 **전 행을 스캔**해(18만 행 열 선택 시 헤더 repaint ≈1.6s) 심각한 렉을 유발했다. → 변경 이력 맨 위 "18만 행 열 헤더 선택 렉 제거"에서 `initStyleOptionForIndex` 오버라이드로 해결(이제 행 수 무관 ~0.3ms). 검증: offscreen은 굵기 미렌더 → 실Windows 육안 최종.

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
