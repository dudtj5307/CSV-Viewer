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
│   ├── gui_filter.py          # FilterHeaderView / FilterWidget - 열 필터 헤더
│   ├── res/                   # 아이콘/스피너 리소스 (png · ico · gif)
│   └── ui/                    # pyuic6 자동생성 UI 파일 (직접 수정 금지)
│       ├── dialog_viewer.py   # Ui_ViewerWindow
│       ├── dialog_filter.py   # Ui_FilterForm
│       └── widget_esc.py      # Ui_WidgetESC (ESC 안내 위젯)
├── utils/
│   └── viewer/
│       ├── table_model.py     # CSVTableModel (QAbstractTableModel)
│       ├── filter_model.py    # CSVFilterProxyModel (열 필터 프록시)
│       ├── search_model.py    # SearchModel (Ctrl+F 검색 로직)
│       └── csv_loader.py      # CSVLoaderThread (비동기 CSV 로딩)
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
| `FilterHeaderView` | gui_filter.py | 커스텀 수평 헤더. 우클릭 → 열 필터 팝업 |
| `FilterWidget` | gui_filter.py | 체크박스 기반 필터 UI |
| `CSVTableModel` | viewer/table_model.py | QAbstractTableModel. rows + highlight_cells |
| `CSVFilterProxyModel` | viewer/filter_model.py | 열별 필터 프록시. column_filters, 헤더 `⏷` 표시 |
| `SearchModel` | viewer/search_model.py | Ctrl+F 검색 + 하이라이트 |
| `CSVLoaderThread` | viewer/csv_loader.py | 비동기 CSV 읽기 (utf-8-sig → cp949 폴백). 읽은 데이터는 `pyqtSignal(str, object)`로 전달 (아래 ⚠) |

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
- **⚠ 버전 올릴 때**: `CSVViewer.wxs`의 `Version`만 올리면 `MajorUpgrade`가 구버전 자동 제거 후 설치. `UpgradeCode`는 **절대 변경 금지**(바꾸면 구버전과 별개 제품이 되어 중복 설치됨).
- **⚠ `build_msi.ps1` 편집 시**: 한글 주석 포함 → UTF-8 **BOM 유지 필수**(없으면 PowerShell 5.1이 cp949로 오독해 파싱 실패).

---

## 알려진 TODO / 미완성 항목

- `FilterHeaderView` 셀 선택 시 열 헤더 볼드 미작동
  - 원인: `QHeaderView::section { ... }` stylesheet가 QStyleSheetStyle로 렌더링을 가로채 native 볼드 처리가 안 됨
  - 해결: `paintSection` override로 직접 볼드 텍스트 그리기 (보류)
  - ⚠️ paintSection 직접 드로잉 오버레이는 `QWidget.grab()`에 캡처되지 않음(clip 비움) → 자동/스크린샷 검증 불가. 필터 열 표시(`⏷`)도 이 때문에 paintSection 대신 `headerData` 텍스트 마커로 구현함
