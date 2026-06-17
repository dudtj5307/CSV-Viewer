# CSV Viewer — MSI 설치 파일 만들기

`CSV Viewer` 를 다른 윈도우 노트북에 배포하기 위한 **WiX 기반 MSI** 정의.

## 이 MSI가 하는 일

| 요구사항 | 구현 |
|---|---|
| 환경변수를 설치 경로로 | 시스템 환경변수 **`CSV_VIEWER_HOME` = 설치 경로** (제거 시 함께 삭제) |
| Program Files 설치 | **`C:\Program Files\CSV Viewer`** (64비트, per-machine) |
| 폴더 우클릭 메뉴 | `Directory\shell` → **Open with CSV Viewer(V)** |
| 폴더 빈 공간 우클릭 | `Directory\Background\shell` → **Open with CSV Viewer(V)** |
| 재실행 시 제거(토글) | 이미 설치돼 있으면 같은 MSI 재실행 → **제거**(`REMOVE=ALL`) |

> 우클릭 시 `"...\CSV Viewer.exe" "<해당 폴더>"` 로 실행됩니다(`%V`).
> per-machine 설치라 **설치/제거는 관리자 권한**이 필요합니다.

## 빌드 방법

필요 도구: **.NET SDK**(이미 설치됨) + **WiX CLI**(없으면 스크립트가 자동 설치).

```powershell
# (A) 앱은 이미 빌드돼 있고(dist\CSV Viewer) MSI만 만들 때
.\installer\build_msi.ps1

# (B) 앱부터 다시 빌드(PyInstaller) 후 MSI 까지
.\installer\build_msi.ps1 -Rebuild
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

> **재실행 = 제거(토글)**: 이미 설치된 상태에서 **같은 MSI를 다시 더블클릭하면 설치가 아니라 제거**로 동작합니다.
> (한 번 더 실행하면 다시 설치 → 토글) 진짜 제거는 `msiexec /x` 또는 제어판에서도 가능.
> 동작 원리: 설치돼 있으면(`Installed`) `REMOVE=ALL` 을 `CostFinalize` 전에 세팅. `/x` 제거·버전 업그레이드 중
> 구버전 제거와는 조건(`NOT REMOVE`, `NOT UPGRADINGPRODUCTCODE`)으로 구분.

## 동작 확인

- 설치 후 **새 탐색기/콘솔**에서 `echo %CSV_VIEWER_HOME%` → 설치 경로 출력
  (MSI가 환경변수 변경을 broadcast 하지만, 이미 떠 있던 창은 못 받을 수 있어 *새 창*에서 확인)
- 아무 폴더 **우클릭** / 폴더 안 **빈 공간 우클릭** → "Open with CSV Viewer(V)"

## 자주 막히는 부분

- **메뉴 아이콘이 안 보임**: 일부 환경에서 경로 공백 때문에 안 뜰 수 있음.
  그래도 메뉴 동작은 정상. 필요하면 `CSVViewer.wxs` 의 `Icon` 값을 조정.
- **버전 올려 재배포**: `CSVViewer.wxs` 의 `Version` 만 올리면(예: `1.0.1.0`)
  `MajorUpgrade` 가 구버전을 자동 제거 후 새로 설치. `UpgradeCode` 는 **절대 바꾸지 말 것**.
- **메뉴 문구/단축키 변경**: `Value="Open with CSV Viewer(&amp;V)"` 의 `&amp;` 뒤 글자가 밑줄 단축키(V).

## 파일

- `CSVViewer.wxs` — WiX 설치 정의 (직접 편집)
- `build_msi.ps1` — 빌드 스크립트
- `out\` — 빌드 결과(MSI) 출력 폴더
