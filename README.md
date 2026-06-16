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
