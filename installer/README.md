# CSV Viewer — MSI 설치 파일 만들기

`CSV Viewer` 를 다른 윈도우 노트북에 배포하기 위한 **WiX 기반 MSI** 정의.

## 이 MSI가 하는 일

| 요구사항 | 구현 |
|---|---|
| 환경변수를 설치 경로로 | 시스템 환경변수 **`CSV_VIEWER_HOME` = 설치 경로** (제거 시 함께 삭제) |
| Program Files 설치 | **`C:\Program Files\CSV Viewer`** (64비트, per-machine) |
| 폴더 우클릭 메뉴 | `Directory\shell` → **Open with CSV Viewer(V)** |
| 폴더 빈 공간 우클릭 | `Directory\Background\shell` → **Open with CSV Viewer(V)** |
| 설치 상태별 3분기 | **미설치→설치**, **옛 버전 설치됨→업그레이드**(구버전 제거 후 새로 설치), **같은 버전 설치됨→제거 토글**(`REMOVE=ALL`) |
| 작업 전 확인 | '시작 전' 영문 Yes/No 확인창 3종. 설치=`Do you wish to install CSV Viewer?` · 업그레이드=`Do you wish to update version?` · 제거=`Do you wish to uninstall CSV Viewer?` (**No → 취소**, 아무것도 안 바뀜) |

> 우클릭 시 `"...\CSV Viewer.exe" "<해당 폴더>"` 로 실행됩니다(`%V`).
> per-machine 설치라 **설치/제거는 관리자 권한**이 필요합니다.

## 빌드 방법

필요 도구: **.NET SDK**(이미 설치됨) + **WiX CLI**(없으면 스크립트가 자동 설치).

```powershell
# (A) 앱은 이미 빌드돼 있고(dist\CSV Viewer) MSI만 만들 때
powershell -ExecutionPolicy Bypass -File ".\installer\build_msi.ps1"

# (B) 앱부터 다시 빌드(PyInstaller) 후 MSI 까지
powershell -ExecutionPolicy Bypass -File ".\installer\build_msi.ps1" -Rebuild
```

결과물: **`installer\out\CSV Viewer Setup.msi`**

WiX CLI를 직접 설치/제거하려면:

```powershell
dotnet tool install --global wix      # 설치
dotnet tool uninstall --global wix    # 제거
```

## 설치 / 제거 (배포 대상 PC)

```powershell
# 설치 (관리자 PowerShell). UI 없이 조용히:
msiexec /i "CSV Viewer Setup.msi" /qb

# 제거
msiexec /x "CSV Viewer Setup.msi" /qb
```

더블클릭해도 UAC 후 설치됩니다.

> **설치 상태에 따라 동작이 갈립니다(`ProductCode` 가 버전별로 결정됨):**
> - **미설치** → 새로 설치.
> - **옛 버전이 설치돼 있음** → 같은 MSI 더블클릭 시 **업그레이드**(구버전 자동 제거 후 새 버전 설치, `MajorUpgrade`).
> - **같은 버전이 설치돼 있음** → 같은 MSI 더블클릭 시 **제거**(토글). 한 번 더 실행하면 다시 설치.
>
> 진짜 제거는 `msiexec /x` 또는 제어판에서도 가능. 동작 원리: 같은 버전이면 ProductCode 가 같아
> `Installed` → `REMOVE=ALL` 을 `CostFinalize` 전에 세팅(토글). 다른 버전이면 ProductCode 가 달라
> `Installed`=거짓 → `MajorUpgrade`(`UpgradeCode` 기준)가 구버전을 제거하고 새로 설치(업그레이드).
> `/x` 제거·업그레이드 중 구버전 제거와는 조건(`NOT REMOVE`, `NOT UPGRADINGPRODUCTCODE`)으로 구분.

> **작업 전 확인창**: 더블클릭(또는 제어판/`msiexec` UI) 으로 실행하면 작업이 **시작되기 전에** 영문 Yes/No
> 확인창이 뜹니다 — 설치 상태에 따라 셋 중 하나:
> - 설치: `Do you wish to install CSV Viewer?`
> - 업그레이드: `Do you wish to update version?`
> - 제거: `Do you wish to uninstall CSV Viewer?`
>
> **No** 를 누르면 깨끗하게 취소되어(파일/환경변수/레지스트리 **변경 없음**), **Yes** 면 진행합니다.
> 확인창은 `ExecuteAction` 전에 실행되는 함수형 VBScript 커스텀액션(반환 `2`=취소 / `1`=진행)이며,
> `WIX_UPGRADE_DETECTED`·`REMOVE` 조건으로 정확히 하나만 뜹니다.
> **무인 설치(`/qn`)** 에서는 UI 시퀀스를 타지 않으므로 확인창 없이 그대로 진행됩니다(완료 팝업도 동일).

## 동작 확인

- 설치 후 **새 탐색기/콘솔**에서 `echo %CSV_VIEWER_HOME%` → 설치 경로 출력
  (MSI가 환경변수 변경을 broadcast 하지만, 이미 떠 있던 창은 못 받을 수 있어 *새 창*에서 확인)
- 아무 폴더 **우클릭** / 폴더 안 **빈 공간 우클릭** → "Open with CSV Viewer(V)"

## 다른 프로그램에서 연동 (CSV_VIEWER_HOME)

외부 응용 SW는 실행파일 경로를 하드코딩하지 말고 환경변수로 만들어 호출한다:

- 실행파일 : **`%CSV_VIEWER_HOME%\CSV Viewer.exe`**
- 호출     : `"%CSV_VIEWER_HOME%\CSV Viewer.exe" "<열려는 CSV 폴더>"`

`CSV_VIEWER_HOME` = 설치 경로(`C:\Program Files\CSV Viewer`)라 설치 위치가 달라져도 이 변수만
참조하면 된다. 환경변수는 **프로세스 시작 시점**에 읽히므로 설치 후 새로 시작된 프로세스에서 사용한다
(이미 떠 있던 프로그램은 재시작 필요). C++(MFC)에서 경로 얻는 예제 코드는 루트
[`README.md`](../README.md) 의 "외부 프로그램에서 실행" 참고.

## 자주 막히는 부분

- **메뉴 아이콘이 안 보임**: 일부 환경에서 경로 공백 때문에 안 뜰 수 있음.
  그래도 메뉴 동작은 정상. 필요하면 `CSVViewer.wxs` 의 `Icon` 값을 조정.
- **버전 올려 업데이트 배포**: `CSVViewer.wxs` 의 `Version` **앞 3자리 중 하나**를 올리면(예: `1.0.0.0`→`1.0.1.0`)
  `build_msi.ps1` 이 ProductCode 를 자동으로 다른 GUID 로 만들고 `MajorUpgrade` 가 구버전을 제거 후 새로 설치(=업그레이드).
  ⚠ **4번째 자리만 올리면**(`1.0.0.0`→`1.0.0.1`) Windows 가 같은 버전으로 취급해 **업데이트가 안 되고 제거 토글**이 됩니다.
  `UpgradeCode` 는 **절대 바꾸지 말 것**(바꾸면 기존 설치를 못 찾아 업그레이드·토글이 깨짐). ProductCode 는 직접 박지 말 것(자동 산출).
- **메뉴 문구/단축키 변경**: `Value="Open with CSV Viewer(&amp;V)"` 의 `&amp;` 뒤 글자가 밑줄 단축키(V).

## 파일

- `CSVViewer.wxs` — WiX 설치 정의 (직접 편집)
- `build_msi.ps1` — 빌드 스크립트
- `out\` — 빌드 결과(MSI) 출력 폴더
