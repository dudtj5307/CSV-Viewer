import csv
import os
import hashlib
from PyQt6.QtCore import QThread, pyqtSignal

# 긴 DATA 필드(기본 131072자 초과) 대비 — csv.Error 'field larger than field limit' 방지
csv.field_size_limit(10 * 1024 * 1024)

class CSVLoaderThread(QThread):
    load_complete = pyqtSignal(str, object)  # (파일경로, 데이터)
    load_failed   = pyqtSignal(str)
    load_empty    = pyqtSignal(str)        # 디코딩은 됐지만 데이터 행 없음 (No Data)

    def __init__(self, csv_path):
        super().__init__()
        self.csv_path = csv_path
        # 캐시 무효화용 지문: signature=(size, mtime_ns) 빠른 게이트 · content_hash=내용 타이브레이커.
        # 콜백(GUI 스레드)이 emit 뒤 t.signature/t.content_hash 로 읽어 캐시에 저장한다(참조 안전).
        self.signature = None
        self.content_hash = None

    def _stat_sig(self):
        # 메타데이터 지문(0.007ms). 파일이 사라졌으면 None.
        try:
            s = os.stat(self.csv_path)
        except OSError:
            return None
        return (s.st_size, s.st_mtime_ns)

    def _hash_content(self):
        # 내용 해시(sha256). 재진입 시 size 같고 mtime만 다를 때만 비교에 쓰이는 baseline.
        h = hashlib.sha256()
        try:
            with open(self.csv_path, 'rb') as f:
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    h.update(chunk)
        except OSError:
            return None
        return h.hexdigest()

    def run(self):
        self.signature = self._stat_sig()             # 시작 시 1회(7µs) — 성공/빈/실패 공통
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

            self.content_hash = self._hash_content()    # 성공 시에만 baseline 해시 계산(빈/실패는 None)
            self.load_complete.emit(self.csv_path, data)
            print(f"[Loader] Success opening '{self.csv_path}' with {encode_type}")
            return

        # 모든 인코딩 디코딩 실패
        print(f"[Loader] Error: Cannot load '{self.csv_path}' with available encodings.\n")
        self.load_failed.emit(self.csv_path)
