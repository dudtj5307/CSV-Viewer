# CSV Viewer

CSV 폴더를 빠르게 열람하는 PyQt6 데스크톱 뷰어.
다른 응용 SW가 `CSV Viewer.exe [CSV폴더경로]` 를 실행하면 독립 프로세스로 뷰어 창이 하나 뜬다. 여러 번 실행하면 서로 독립적인 창이 여러 개 뜬다. onedir 빌드라 압축해제가 없어 매 실행 기동이 가볍다.

> Packet_Parsing_Software(PPS)에서 CSV Viewer 부분만 분리한 프로젝트.

## 실행

```
# 폴더 경로를 주면 그 폴더를 바로 연다 (없으면 폴더 선택창)
CSV Viewer.exe "D:\some\csv_folder"
```

개발 중 실행 (conda env `sniff_env`, PyQt6 설치 필요):

```
python csv_viewer.py "CSV\raw_250416_174444"
```

## 외부 프로그램에서 실행 (C++ MFC 2010)

`CSV Viewer.exe` 에 CSV 폴더 경로를 인자로 넘겨 **독립 프로세스**로 띄운다.
실행파일명·폴더 경로 모두 공백이 들어갈 수 있으므로 따옴표 처리에 주의한다.
이 함수를 여러 번 호출하면 서로 독립적인 뷰어 창이 여러 개 뜬다.

### 실행파일 경로 — MSI 설치 시 환경변수 `CSV_VIEWER_HOME` 사용 (권장)

MSI(`installer/`)로 설치하면 시스템 환경변수 **`CSV_VIEWER_HOME`** 에 설치 경로
(`C:\Program Files\CSV Viewer`)가 등록된다. 실행파일을 하드코딩하지 말고 이 변수로 경로를
만들면, 나중에 설치 위치가 바뀌어도 외부 SW 코드를 고칠 필요가 없다.

- 실행파일 경로 : **`%CSV_VIEWER_HOME%\CSV Viewer.exe`**
- 배치/명령 예 : `"%CSV_VIEWER_HOME%\CSV Viewer.exe" "D:\data\csv_folder"`

```cpp
// CSV_VIEWER_HOME 환경변수로부터 "CSV Viewer.exe" 의 전체 경로를 만든다.
// 반환값 : 성공 TRUE / 변수 없음(= MSI 미설치) FALSE
BOOL GetCsvViewerExePath(CString& strExePath)
{
    TCHAR szHome[MAX_PATH] = { 0 };
    DWORD n = GetEnvironmentVariable(_T("CSV_VIEWER_HOME"), szHome, MAX_PATH);
    if (n == 0 || n >= MAX_PATH)
        return FALSE;   // 변수 없음 → 미설치. 안내하거나 직접 경로로 fallback.

    strExePath = szHome;                    // 예: C:\Program Files\CSV Viewer
    if (strExePath.Right(1) != _T("\\"))
        strExePath += _T("\\");
    strExePath += _T("CSV Viewer.exe");
    return TRUE;
}
```

> ⚠ 환경변수는 **프로세스가 시작될 때** 읽힌다. MSI 설치 직후, 이미 떠 있던 프로그램은 변경을
> 못 받을 수 있으니 **설치 후 새로 시작된 프로세스**에서 호출해야 한다(상주/서비스면 재시작).

### 방법 A — ShellExecute (간단, 권장)

```cpp
#include <shellapi.h>
#pragma comment(lib, "shell32.lib")

// CSV Viewer 실행파일에 CSV 폴더 경로를 인자로 넘겨 독립 프로세스로 띄운다.
//   szExePath   : "CSV Viewer.exe" 의 전체 경로 (보통 GetCsvViewerExePath 로 CSV_VIEWER_HOME 에서 얻음.
//                 예: L"C:\\Program Files\\CSV Viewer\\CSV Viewer.exe")
//   szCsvFolder : 열고 싶은 CSV 폴더 경로     (예: L"D:\\data\\csv_folder")
// 반환값 : 성공하면 TRUE
BOOL LaunchCsvViewer(LPCTSTR szExePath, LPCTSTR szCsvFolder)
{
    // 폴더 경로에 공백이 있을 수 있으므로 따옴표로 감싸 하나의 인자로 전달한다.
    CString strParam;
    strParam.Format(_T("\"%s\""), szCsvFolder);

    // ShellExecute 는 실행파일(szExePath)과 인자(strParam)를 분리해서 받으므로
    // 실행파일 경로의 공백은 따로 따옴표 처리할 필요가 없다.
    HINSTANCE hInst = ShellExecute(
        NULL,             // 부모 윈도우 (없음)
        _T("open"),       // 동작: 실행
        szExePath,        // 실행할 파일
        strParam,         // 커맨드라인 인자 (CSV 폴더 경로)
        NULL,             // 작업 디렉터리 (기본)
        SW_SHOWNORMAL);   // 창 표시 방식

    // ShellExecute 는 성공 시 32 보다 큰 값을 반환한다.
    return (reinterpret_cast<INT_PTR>(hInst) > 32);
}
```

### 방법 B — CreateProcess (Win32 API만, 의존성 없음)

```cpp
// ShellExecute 대안. 커맨드라인을 직접 구성해 프로세스를 생성한다.
BOOL LaunchCsvViewer(LPCTSTR szExePath, LPCTSTR szCsvFolder)
{
    // 커맨드라인 = "실행파일경로" "CSV폴더경로"  (둘 다 공백 대비 따옴표로 감쌈)
    CString strCmd;
    strCmd.Format(_T("\"%s\" \"%s\""), szExePath, szCsvFolder);

    STARTUPINFO         si = { sizeof(si) };
    PROCESS_INFORMATION pi = { 0 };

    // CreateProcess 는 두 번째 인자(커맨드라인)를 내부에서 수정할 수 있으므로
    // 읽기 전용 리터럴이 아닌 쓰기 가능한 버퍼를 넘겨야 한다.
    BOOL bOk = CreateProcess(
        NULL,                 // 모듈명 (NULL → 커맨드라인에서 파싱)
        strCmd.GetBuffer(),   // 커맨드라인 (쓰기 가능 버퍼)
        NULL, NULL,           // 프로세스/스레드 보안 속성
        FALSE,                // 핸들 상속 안 함
        0,                    // 생성 플래그
        NULL,                 // 환경 블록 (부모와 동일)
        NULL,                 // 작업 디렉터리 (부모와 동일)
        &si, &pi);
    strCmd.ReleaseBuffer();

    if (bOk)
    {
        // 독립 프로세스로 띄우고 핸들은 바로 닫는다 (프로세스는 계속 실행됨).
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
    }
    return bOk;
}
```

### 호출 예시

```cpp
// MSI 설치 환경: CSV_VIEWER_HOME 으로 실행파일 경로를 얻어 호출 (권장)
CString strExe;
if (GetCsvViewerExePath(strExe))
    LaunchCsvViewer(strExe, _T("D:\\data\\csv_folder"));
else
    AfxMessageBox(_T("CSV Viewer 가 설치되어 있지 않습니다. (CSV_VIEWER_HOME 없음)"));

// 경로를 직접 아는 경우(개발/디버그 등):
LaunchCsvViewer(_T("C:\\Program Files\\CSV Viewer\\CSV Viewer.exe"),
                _T("D:\\data\\csv_folder"));
```

## 빌드 (PyInstaller)

### 1. 가상환경 준비

```
conda create -n sniff_env python pyqt pyinstaller
conda activate sniff_env
```

### 2. 빌드

```
(sniff_env) cd "yourDirectory"
(sniff_env) pyinstaller CSV_Viewer.spec
```

### 3. 출력

`dist\CSV Viewer\CSV Viewer.exe` (onedir = 폴더 통째로 배포)

- onedir: 실행 시 압축해제가 없어 기동이 빠름
- console=False: 외부 SW에서 실행 시 콘솔창이 뜨지 않음
