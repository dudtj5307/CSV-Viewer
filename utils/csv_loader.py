import csv
from PyQt6.QtCore import QThread, pyqtSignal

# 긴 DATA 필드(기본 131072자 초과) 대비 — csv.Error 'field larger than field limit' 방지
csv.field_size_limit(10 * 1024 * 1024)

class CSVLoaderThread(QThread):
    load_complete = pyqtSignal(str, list)  # (파일경로, 데이터)
    load_failed   = pyqtSignal(str)
    load_empty    = pyqtSignal(str)        # 디코딩은 됐지만 데이터 행 없음 (No Data)

    def __init__(self, csv_path):
        super().__init__()
        self.csv_path = csv_path

    def run(self):
        for encode_type in ['utf-8-sig', 'cp949']:    # utf-8-sig: BOM 자동 제거(없어도 일반 UTF-8처럼 동작)
            try:
                with open(self.csv_path, newline='', encoding=encode_type) as csvfile:
                    reader = csv.reader(csvfile)
                    data = [list(row) for row in reader]     # csv.reader는 이미 str 반환
            except (UnicodeDecodeError, LookupError) as e:
                # 인코딩 문제 -> 다음 인코딩으로 재시도
                print(f"[Loader] Decode failed '{self.csv_path}' with {encode_type} / {e}")
                continue
            except csv.Error as e:
                # NUL/필드초과 등 구조 오류(인코딩 오판이 원인일 수 있음) -> 다음 인코딩으로 재시도
                print(f"[Loader] CSV parse error '{self.csv_path}' with {encode_type} / {e}")
                continue
            except OSError as e:
                # 파일 접근 자체가 실패 -> 재시도 무의미
                print(f"[Loader] Cannot open '{self.csv_path}' / {e}")
                self.load_failed.emit(self.csv_path)
                return

            # 디코딩 성공 - 구조 검증은 인코딩과 무관하므로 여기서 확정 처리
            if len(data) < 2:
                print(f"[Loader] '{self.csv_path}' has no data rows. (No Data)")
                self.load_empty.emit(self.csv_path)
                return

            # 엑셀처럼 행마다 열 개수가 달라도 허용: 가장 긴 행 기준으로 빈칸 패딩해 사각형화
            width = max(len(row) for row in data)
            for row in data:
                if len(row) < width:
                    row.extend([""] * (width - len(row)))

            self.load_complete.emit(self.csv_path, data)
            print(f"[Loader] Success opening '{self.csv_path}' with {encode_type}")
            return

        # 모든 인코딩 디코딩 실패
        print(f"[Loader] Error: Cannot load '{self.csv_path}' with available encodings.\n")
        self.load_failed.emit(self.csv_path)
