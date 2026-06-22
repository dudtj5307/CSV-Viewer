"""분석 결과(.viewer) 영속화 — CSV 폴더당 1개 JSON 파일.

CSV 폴더 루트의 `.viewer` 파일에 그 폴더 CSV들의 '사용자가 가한 분석 상태'를 모아 저장한다.
모델/프록시를 통째로 직렬화하지 않는다(본문 중복·Qt 피클 위험). 대신 **사용자 입력만**
(하이라이트·열 값 필터·Δ 정의·열너비·스크롤)을 평면 dict로 추출해 둔다. 파일 본문 자체는
디스크에 있으므로 저장하지 않고, 재로드 시 **CSV 내용 해시(csv_sha256)가 일치할 때만** 복원한다.

⚠ Δ는 행 리스트(효과) 대신 '스냅샷 당시 열 값 필터(원인)'만 저장한다(Option 2). 18만 행짜리
스냅샷도 보통 빈 필터 몇 byte로 끝나고, 재현 시 동일 파일에 그 필터를 다시 적용해 멤버십을
재구성하므로 "필터 걸고 Δ / 안 걸고 Δ"가 정확히 구분·복원된다. (filter_model.restore_state 참고)

스키마:
  { "app": "csv_viewer", "version": 2, "saved_at": "ISO8601",
    "files": { "<csv파일명(.csv 제외)>": {
        "csv_sha256", "csv_size",
        "highlights":     { "#rrggbb": { "<col>": [row, ...] } },
        "column_filters": [ {"col": int, "hidden": [str, ...]} ],
        "deltas":         [ {"base": int, "snapshot_filter": [...], "filter_hidden": [...],
                             "colors": {"#rrggbb": [row, ...]}} ],
        "hidden_rows":    [ [start, end], ... ],         # 사용자가 숨긴 source_row (연속구간 run 인코딩)
        "hidden_cols":    [ [start, end], ... ],         # 사용자가 숨긴 source_col
        "hidden_col_sizes": { "<col>": width,  ... },    # 숨기기 전 열너비(펼칠 때 원복)
        "hidden_row_sizes": { "<row>": height, ... },    # 숨기기 전 행높이(펼칠 때 원복; 열·행 대칭)
        "col_widths":     { "<width>":  [col, ...] },   # 기본 80 과 다른 열만 (크기→인덱스 그룹)
        "row_heights":    { "<height>": [row, ...] },   # 기본 20 과 다른 행만 (보이는 행 인덱스 기준)
        "scroll":         [v, h] } } }
⚠ v1 은 col_widths 가 [int,...] 전체 배열이었다(행높이 미저장). unpack_sizes 가 구포맷 배열도 읽어 하위호환.
⚠ v3 에서 hidden_*(행/열 숨기기) 추가. 키가 없으면 빈 집합(v1·v2 .viewer 하위호환).
"""
import os
import json
import tempfile
from datetime import datetime

from PyQt6.QtGui import QColor

VIEWER_FILENAME = ".viewer"
APP_ID = "csv_viewer"
SCHEMA_VERSION = 3     # v3: hidden_rows/cols(+sizes) 추가. v2: col_widths 그룹 sparse·row_heights(v1 배열도 읽힘)


# ---------- QColor ↔ 문자열 (table_model / filter_model 의 export/restore 가 공용) ----------
def color_to_str(color):
    # 사용자 하이라이트는 항상 불투명색이라 '#rrggbb' 로 충분(알파 미사용).
    return color.name()


def str_to_color(s):
    return QColor(s)


# ---------- 크기(열너비/행높이) ↔ 그룹 sparse 포맷 ----------
# 저장 파일엔 '기본값과 다른 것만' {크기: [인덱스, ...]} 로 묶는다(하이라이트 {색:{열:[행]}} 와 동일 철학):
#   같은 크기끼리 묶어 잎 배열을 _pretty 가 한 줄로 유지 → 수만 행을 같은 높이로 바꿔도 파일이 안 부푼다.
# 인메모리(cache)·복원은 {인덱스: 크기} sparse dict 로 다룬다(범위 내 인덱스만 적용 → Δ 열 수 불일치도 안전).
def pack_sizes(overrides):
    """{index: size}(기본값과 다른 것만) → {size: [index, ...]} 그룹(저장용). 빈 입력이면 {}."""
    grouped = {}
    for idx, size in overrides.items():
        grouped.setdefault(size, []).append(idx)
    return {s: sorted(idxs) for s, idxs in grouped.items()}


def unpack_sizes(value, default):
    """저장값 → {index: size} sparse dict. 신포맷 {size:[idx]} · 구포맷 [size,...](배열 위치=인덱스) · None 모두 처리."""
    if not value:
        return {}
    if isinstance(value, dict):                      # 신포맷 {size: [index, ...]}
        out = {}
        for size, idxs in value.items():
            s = int(size)
            for i in idxs:
                out[int(i)] = s
        return out
    if isinstance(value, list):                      # 구포맷 [size, ...] (배열 위치=인덱스, 하위호환)
        return {i: v for i, v in enumerate(value) if v != default}
    return {}


# ---------- 인덱스 집합 ↔ 연속구간(run) 인코딩 (숨김 행/열 저장용) ----------
# 숨김은 수만 개일 수 있어(예: 10만 행 숨김) 평면 정수 리스트 대신 연속구간으로 묶는다:
#   {0,1,2,5,6} → [[0,2],[5,6]]. _pretty 가 스칼라 리스트(=각 run)를 한 줄로 유지하므로 컴팩트.
def pack_runs(indices):
    """정수 집합/리스트 → 연속구간 [[start, end], ...] (오름차순, 양끝 포함). 빈 입력이면 []."""
    s = sorted(set(indices))
    if not s:
        return []
    runs = []
    start = prev = s[0]
    for x in s[1:]:
        if x == prev + 1:
            prev = x
        else:
            runs.append([start, prev])
            start = prev = x
    runs.append([start, prev])
    return runs


def unpack_runs(runs):
    """[[start, end], ...] → set(int). 잘못된 항목은 조용히 무시(깨진 .viewer 방어)."""
    out = set()
    for r in runs or []:
        try:
            a, b = int(r[0]), int(r[1])
        except (TypeError, ValueError, IndexError):
            continue
        if a <= b:
            out.update(range(a, b + 1))
    return out


# ---------- 파일 IO ----------
def _read_doc(path):
    """폴더의 .viewer 를 읽어 정상 envelope 면 반환, 아니면(없음/깨짐/타앱) 빈 envelope.
    어떤 경우에도 예외를 밖으로 내보내지 않는다 → CSV 열람을 절대 막지 않음."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = None
    if isinstance(doc, dict) and doc.get("app") == APP_ID and isinstance(doc.get("files"), dict):
        doc.setdefault("version", SCHEMA_VERSION)
        return doc
    return {"app": APP_ID, "version": SCHEMA_VERSION, "files": {}}


def _pretty(obj, indent=0):
    """가독성용 JSON 직렬화. **구조(dict, '리스트의 dict' 목록)는 들여쓰되, 잎 배열
    (행 목록·hidden·col_widths·scroll 등)은 한 줄로 유지**한다.
    → 18만 행 목록이 indent=2 처럼 한 칸당 한 줄로 터지지 않으면서 구조는 한눈에 보인다.
    출력은 표준 JSON(json.load 로 그대로 읽힘 — 토큰 사이 공백만 다름)."""
    pad, pad1 = "  " * indent, "  " * (indent + 1)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        # ⚠ 키는 str(k) 로 따옴표 감싸기 — 하이라이트의 '열' 키가 int 라, 표준 json.dump 와 달리
        #   커스텀 프린터는 직접 문자열화해야 유효한 JSON 키('5')가 된다(안 그러면 5: 처럼 깨짐).
        body = ",\n".join(f"{pad1}{json.dumps(str(k), ensure_ascii=False)}: {_pretty(v, indent + 1)}"
                          for k, v in obj.items())
        return "{\n" + body + "\n" + pad + "}"
    if isinstance(obj, list) and any(isinstance(x, dict) for x in obj):
        # 원소가 dict인 목록(column_filters / deltas)만 한 dict씩 줄바꿈
        body = ",\n".join(f"{pad1}{_pretty(x, indent + 1)}" for x in obj)
        return "[\n" + body + "\n" + pad + "]"
    # 그 외(스칼라 리스트·좌표쌍 리스트·스칼라) → 한 줄 compact
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _write_atomic(path, doc):
    """temp 작성 후 os.replace 로 원자 교체(부분 파일 방지). 실패(권한 등) 시 False."""
    folder = os.path.dirname(path) or "."
    try:
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".viewer-", suffix=".tmp")
    except OSError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_pretty(doc) + "\n")
        os.replace(tmp, path)            # 같은 볼륨 → 원자적 (Windows 포함)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def load_folder_states(folder):
    """폴더의 .viewer 에서 files map 반환. 없거나 깨졌으면 빈 dict."""
    if not folder:
        return {}
    return _read_doc(os.path.join(folder, VIEWER_FILENAME)).get("files", {})


def save_file_state(folder, csv_name, file_state):
    """폴더 .viewer 를 다시 읽어 csv_name 항목만 교체 후 원자적 재기록(다른 CSV 저장본 보존).
    반환: 성공 여부(bool)."""
    if not folder:
        return False
    path = os.path.join(folder, VIEWER_FILENAME)
    doc = _read_doc(path)                  # 저장 시점에 다시 읽어 머지 → 다른 CSV/다른 창의 기록 보존
    doc["files"][csv_name] = file_state
    doc["saved_at"] = datetime.now().isoformat(timespec="seconds")
    return _write_atomic(path, doc)
