"""3D 궤적 그래프 창 (button_graph → 현재 CSV 의 열로 x/y/z 3D 그래프).

ViewerWindow.open_graph 이 현재 CSV 의 raw 데이터(헤더 + 전체 행)를 넘겨 GraphWindow 를 띄운다.
- combo_x/y/z : 축으로 쓸 열 선택. 숫자 열만 선택 가능(비숫자 열은 disable).
- combo_time  : '시간' 열. 고르면 자취(trail) 모드 — bar_time(슬라이더)/▶재생으로 시각 T 를 훑으며
                각 트랙이 T 까지의 경로만 그려지고 트랙별 마커가 동기 이동. 현재 시각은
                plainTextEdit_timestamp 에 '현재/총'(실제 표본 시각으로 스냅) 표시. '(row #)'면 전체 경로.
- combo_track : 트랙(그룹) 열. 그 열의 고유값마다 궤적을 분리해 그리고, scrollArea 에 값 목록을
                체크박스(표시/숨김) + 색버튼(트랙별 색)으로 나열한다(gui_filter 패턴 참고).

x/y/z 열이 정해지면 '모든 행(=모든 시간대)'의 (x,y,z) 점을 선으로 이어 궤적을 그리고,
bar_time 가 가리키는 현재 행의 위치를 추가 마커로 표시한다.

⚠ 축 스케일 = '축별 독립 정규화'(set_xlim 방식, 사용자 합의 — 옛 등비율 폐기). 각 축은 자기
  [min,max] 범위를 [0, AXIS_LEN] 큐브로 정규화해 그린다(_norm_axis). 축 열을 고르면 데이터의
  min/max 가 편집칸(lineEdit_{x,y,z}_{min,max})에 자동으로 채워지고, 사용자가 숫자를 바꾸면 그
  범위로 다시 정규화된다. 카메라는 데이터가 아니라 큐브(0~L)에 프레이밍하므로 범위를 좁히면 그
  구간이 확대된다. 각 축선엔 등간격(AXIS_TICKS)으로 실제 값 눈금 숫자를 표시한다.

⚠ GL 렌더는 실제 OpenGL 컨텍스트가 필요해 offscreen 으로 육안검증 불가 → 데이터 로직(숫자 판정·
  점 추출·트랙 그룹·슬라이더 매핑)은 아래 모듈 함수로 분리해 테스트하고, 그래프 모양은 실Windows 육안.

⚠ pyqtgraph/numpy/OpenGL 은 무거워 콜드스타트를 늦춘다 → 이 모듈은 ViewerWindow.open_graph 에서
  '지연 import' 한다(앱 시작 시점엔 로드 안 됨). 그래서 import 들을 모듈 top 에 둬도 안전.
"""

import math
import os
import re

import numpy as np
import pyqtgraph.opengl as gl
from pyqtgraph import Vector
from OpenGL.GL import (GL_DEPTH_TEST, GL_BLEND, GL_ALPHA_TEST, GL_CULL_FACE,
                       GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

from PyQt6.QtWidgets import (QWidget, QCheckBox, QPushButton, QSizePolicy,
                             QColorDialog, QLabel, QApplication, QFileDialog)
from PyQt6.QtCore import Qt, QSize, QPointF, QRectF, QTimer
from PyQt6.QtGui import (QColor, QIcon, QFont, QPainter, QPen, QVector3D,
                         QVector4D, QTextOption, QShortcut, QKeySequence)

from GUI.ui.widget_graph import Ui_GraphForm
from GUI.gui_filter import _FilterItemRow, _filter_sort_key


# ---------------------------------------------------------------------------
# 데이터 로직 (GL 불필요 — 단위 테스트 대상)
# ---------------------------------------------------------------------------

def parse_float(s):
    """CSV 셀 문자열 → float. 빈칸/파싱 실패는 NaN."""
    if s is None:
        return math.nan
    s = str(s).strip()
    if s == "":
        return math.nan
    try:
        return float(s)
    except ValueError:
        return math.nan


def column_is_numeric(rows, col):
    """비어있지 않은 모든 셀이 숫자로 파싱되고, 숫자 셀이 최소 1개 있으면 numeric.
    (빈 셀은 허용 — NaN 으로 들어감. 첫 비숫자에서 즉시 False 반환해 텍스트 열은 일찍 끝난다.)"""
    seen = False
    for r in rows:
        if col >= len(r):
            continue
        s = r[col]
        if s is None:
            continue
        s = str(s).strip()
        if s == "":
            continue
        try:
            float(s)
        except ValueError:
            return False
        seen = True
    return seen


def numeric_columns(headers, rows):
    """숫자 열 인덱스 집합."""
    return {c for c in range(len(headers)) if column_is_numeric(rows, c)}


def column_floats(rows, col):
    """col 열의 전 행 값을 float ndarray 로(빈칸/실패=NaN)."""
    n = len(rows)
    out = np.full(n, np.nan, dtype=float)
    for i, r in enumerate(rows):
        if col < len(r):
            out[i] = parse_float(r[col])
    return out


def parse_time_value(s):
    """CSV 시간 셀 문자열 → float(초 또는 숫자값). 다양한 포맷을 견딘다:
      - 순수 숫자(정수/실수, 예: ms 카운트 '1700' / '12.5') → 그 값 그대로 (단위 무관 숫자 타임라인)
      - 시계 'H:M:S(.f)' · 'M:S(.f)' (예: '14:44:20.354') → 총 초로 환산
      - 날짜+시간('2024-04-16 14:44:20.354')이면 ':' 포함 토큰(시간부)만 사용
      - 뒤따르는 ';' ',' 공백/탭 등은 무시
    파싱 불가/빈칸 → NaN. (순수함수 — offscreen 단위 테스트 대상)"""
    if s is None:
        return math.nan
    s = str(s).strip().strip(";, \t")
    if s == "":
        return math.nan
    try:                                    # 순수 숫자(ms 카운트 등)
        return float(s)
    except ValueError:
        pass
    if " " in s:                            # 날짜+시간 → ':' 포함 토큰(시간부)만
        clk = [t for t in s.split() if ":" in t]
        if clk:
            s = clk[-1]
    if ":" in s:                            # 시계 표기 → 60진 누산(H:M:S 또는 M:S)
        parts = s.split(":")
        if len(parts) > 3:
            return math.nan
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return math.nan
        sec = 0.0
        for p in nums:
            sec = sec * 60.0 + p
        return sec
    return math.nan


def column_time_values(rows, col):
    """col 열 전 행 → parse_time_value ndarray(빈칸/실패=NaN)."""
    n = len(rows)
    out = np.full(n, np.nan, dtype=float)
    for i, r in enumerate(rows):
        if col < len(r):
            out[i] = parse_time_value(r[col])
    return out


def format_clock(sec):
    """초(float) → 'H:MM:SS.mmm'(1시간 이상) 또는 'MM:SS.mmm'(미만). 음수 부호 보존, NaN→'-'."""
    if sec is None or not math.isfinite(sec):
        return "-"
    sign = "-" if sec < 0 else ""
    sec = abs(sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec - h * 3600 - m * 60
    if h:
        return f"{sign}{h:d}:{m:02d}:{s:06.3f}"
    return f"{sign}{m:02d}:{s:06.3f}"


def group_indices_by(rows, col):
    """col 값(문자열, strip) → 그 값을 가진 행 인덱스 리스트(행 순서 유지)."""
    groups = {}
    for i, r in enumerate(rows):
        v = str(r[col]).strip() if col < len(r) and r[col] is not None else ""
        groups.setdefault(v, []).append(i)
    return groups


# 트랙(그룹) 자동 색 팔레트 — 시각적으로 구분되는 색 순환(RGB 0~1)
_TRACK_PALETTE = [
    (0.90, 0.30, 0.30), (0.30, 0.65, 0.95), (0.35, 0.80, 0.40),
    (0.95, 0.75, 0.20), (0.70, 0.45, 0.90), (0.95, 0.55, 0.20),
    (0.30, 0.80, 0.80), (0.95, 0.45, 0.70), (0.55, 0.75, 0.30),
    (0.60, 0.60, 0.95), (0.85, 0.85, 0.35), (0.45, 0.55, 0.85),
]


def track_color(i):
    r, g, b = _TRACK_PALETTE[i % len(_TRACK_PALETTE)]
    return (r, g, b, 1.0)


def _nice_num(x, round_):
    """Heckbert 'nice number' — 양수 x 를 1·2·5·10 × 10^k 중 가까운 값으로(눈금 간격용)."""
    if x <= 0 or not math.isfinite(x):
        return 0.0
    exp = math.floor(math.log10(x))
    f = x / (10 ** exp)                       # 1..10
    if round_:
        nf = 1 if f < 1.5 else 2 if f < 3 else 5 if f < 7 else 10
    else:
        nf = 1 if f <= 1 else 2 if f <= 2 else 5 if f <= 5 else 10
    return nf * (10 ** exp)


def nice_ticks(vmin, vmax, target=8, lo=None):
    """[vmin,vmax]('내부' + 선택적으로 lo 까지 아래로 확장)에 들어가는 깔끔한(1·2·5×10^k 간격)
    눈금 값 리스트 + step. step 은 항상 [vmin,vmax] 기준으로 정한다(lo 로 안 바뀜). lo=None 이면 vmin.
    lo<vmin 이면 데이터(min)보다 아래의 라운드 값(예: 0)도 포함 — 호출부가 '여백 한도'로 lo 를 정한다.
    범위를 안 벗어나는 tight 방식(matplotlib 기본 tick 과 동일 철학). target=목표 눈금 간격 수."""
    if not (math.isfinite(vmin) and math.isfinite(vmax)) or vmax <= vmin:
        return [vmin], 0.0
    step = _nice_num((vmax - vmin) / max(target, 1), True)
    if step <= 0:
        return [vmin], 0.0
    lo_bound = vmin if lo is None else lo
    eps = step * 1e-9
    n0 = math.ceil((lo_bound - eps) / step)    # 하한(lo) 이상 첫 눈금 인덱스
    n1 = math.floor((vmax + eps) / step)        # 범위 내 마지막(최대) 눈금 인덱스
    return [(n0 + k) * step for k in range(int(n1 - n0) + 1)], step


def format_tick_value(v, step):
    """눈금 값 v 를 step 크기에 맞춰 일정한 자리수로 포맷(한 축 라벨 통일).
    매우 크거나(>=1e6) 작은(<1e-4) 간격은 지수표기로."""
    if step <= 0 or not math.isfinite(v):
        return f"{v:g}"
    if v == 0:
        return "0"
    exp = math.floor(math.log10(step))
    if exp <= -5 or exp >= 6:
        return f"{v:.1e}"
    s = f"{v:.{max(0, -exp)}f}"
    if s.startswith("-") and float(s) == 0.0:   # '-0' 방지
        s = s[1:]
    return s


# ---------------------------------------------------------------------------
# 축 눈금 숫자 라벨 (회전 지원 GLTextItem)
# ---------------------------------------------------------------------------

class _TickText(gl.GLTextItem):
    """축 눈금 숫자용 GLTextItem. 기본 GLTextItem 은 투영 위치에 '수평'으로만 그려 회전/오프셋을
    지원하지 않으므로 paint 를 오버라이드해, 투영된 화면 좌표에 픽셀 오프셋(축 아래/왼쪽 배치용)을
    더하고 글씨를 45°(좌상단→우하단, '\\' 방향) 기울여 그린다."""

    def __init__(self, screen_offset=(0.0, 0.0), angle=30.0, **kwds):
        self._screen_offset = screen_offset
        self._angle = angle
        super().__init__(**kwds)

    def paint(self):
        if len(self.text) < 1:
            return
        self.setupGLState()
        p = self.compute_projection().map(QVector3D(*self.pos)).toPointF()
        painter = QPainter(self.view())
        painter.setPen(self.color)
        painter.setFont(self.font)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing |
                               QPainter.RenderHint.TextAntialiasing)
        painter.translate(p.x() + self._screen_offset[0],
                          p.y() + self._screen_offset[1])
        painter.rotate(self._angle)             # +각도 = 시계방향(화면 y 아래) → 좌상단→우하단
        # QPainter.drawText(point, str) 는 '\n' 을 처리하지 않는다(한 줄로만 그림) → 줄마다 직접
        # 그린다. ⚠ 맨 '아래' 줄을 기준점(offset)에 고정하고 줄이 늘면 '위로' 쌓는다 → 줄 수가
        # 늘어도 블록 바닥은 그대로고 위로만 자란다(아래로 쌓여 마커를 덮지 않게). 단일 줄=루프 1회.
        line_h = painter.fontMetrics().lineSpacing()
        lines = self.text.split("\n")
        base = (len(lines) - 1) * line_h
        for i, line in enumerate(lines):
            painter.drawText(QPointF(0.0, i * line_h - base), line)
        painter.end()


# ---------------------------------------------------------------------------
# 카메라 방향 표시 gizmo (좌상단 오버레이)
# ---------------------------------------------------------------------------

class _AxisGizmo(QWidget):
    """실제 그래프 축과 '동일한 각도'로 x/y/z 축을 작게 그리는 좌상단 오버레이.

    ⚠⚠ 정사영이 아니라 '원근 투영'이다: 씬과 똑같은 `projectionMatrix × viewMatrix` 로 world
      원점(0,0,0)→각 축끝(+AXIS_LEN)을 화면에 투영하고, 그 화면 변위벡터(`tip - origin`)를 축소해
      gizmo 코너에 그린다. 단순 회전(`mapVector`)만 쓰면 '축이 화면 어디에 있느냐'에 따른 원근
      전단(shear)을 놓쳐 실제 축과 각도가 어긋난다(중앙이 아니라 큐브 코너에서 축이 뻗으므로
      perspective foreshortening 이 각도를 바꾼다). 같은 파이프라인을 쓰면 각도/단축이 구조적으로
      일치한다. 카메라가 바뀌면 GraphWindow 가 `frameSwapped` 로 `update()` 를 불러 실시간 추종.

    ⚠ 클릭은 이 gizmo 가 아니라 '아래에 깔린 button_graph_reset'(시점 초기화)이 받아야 하므로
      `WA_TransparentForMouseEvents` 로 마우스 이벤트를 통과시킨다. gizmo 는 그리기 전용.
    ⚠ GL 렌더는 offscreen 으로 육안검증 불가 — 각도/원근은 실Windows 에서 최종 확인(행렬값은
      offscreen 으로 씬 full-projection 과 일치 검증)."""

    AXIS_COLORS = {                              # 축별 색(어두운 배경 대비)
        0: QColor(235, 90, 90),                 # x = red
        1: QColor(95, 205, 95),                 # y = green
        2: QColor(95, 160, 245),                # z = blue
    }
    AXIS_LABELS = {0: "x", 1: "y", 2: "z"}
    MARGIN = 15.0                                # 축선이 라벨 자리를 남기도록 두는 가장자리 여백(px)
    LABEL_GAP = 8.0                              # 축 끝점 너머로 라벨을 더 밀어내는 거리(px)

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self._view = view                        # GLViewWidget (graph_area)
        # 클릭은 아래 reset 버튼이 받게 통과시키고, 배경은 투명(3D 씬이 비치게).
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._font = QFont("Arial", 7, QFont.Weight.Bold)

    def _project_axes(self):
        """씬과 동일한 원근 투영(projection × view)으로 world 원점→각 축끝(+AXIS_LEN)을 화면에
        투영해 [(axis, dx, dy, depth)...] 반환. dx,dy=화면 px 변위(y 아래로 +, 실제 축과 동일 각도/
        원근단축), depth=eye z(앞/뒤 페이드용). viewMatrix/projectionMatrix 실패·미표시면 None."""
        view = self._view
        try:
            vm = view.viewMatrix()
            vp = view.getViewport()              # (x0, y0, w, h) device px — 종횡비가 각도에 반영됨
            pm = view.projectionMatrix(vp, vp)
        except Exception:
            return None
        vw, vh = vp[2], vp[3]
        if vw <= 0 or vh <= 0:
            return None
        mvp = pm * vm
        def project(p):
            c = mvp.map(QVector4D(p[0], p[1], p[2], 1.0))
            w = c.w()
            if abs(w) < 1e-9:
                return None
            return ((c.x() / w * 0.5 + 0.5) * vw,
                    (1.0 - (c.y() / w * 0.5 + 0.5)) * vh)   # 화면 y 아래로 +
        s0 = project((0.0, 0.0, 0.0))
        if s0 is None:
            return None
        L = GraphWindow.AXIS_LEN                  # 씬 축 길이와 동일해야 원근 단축이 정확히 일치
        out = []
        for i in range(3):
            tip = [0.0, 0.0, 0.0]
            tip[i] = L
            si = project(tuple(tip))
            if si is None:
                continue
            # 앞/뒤 판정은 위치 무관한 축 방향의 eye z(=mapVector) 부호로(원점→끝 eye z 차와 동치).
            depth = vm.mapVector(QVector3D(*[1.0 if k == i else 0.0
                                             for k in range(3)])).z()
            out.append((i, si[0] - s0[0], si[1] - s0[1], depth))
        return out

    def paintEvent(self, _ev):
        axes = self._project_axes()
        if not axes:
            return
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        R = min(w, h) / 2.0 - self.MARGIN
        if R <= 1.0:
            return
        # 가장 긴 축이 반경 R 을 채우게 공통 배율 — 축별 원근 단축(상대 길이)은 그대로 보존.
        maxlen = max(math.hypot(dx, dy) for _i, dx, dy, _d in axes) or 1.0
        scale = R / maxlen
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing |
                         QPainter.RenderHint.TextAntialiasing)
        p.setFont(self._font)
        # 뒤(depth 작음)로 향하는 축부터 그려 앞축이 위에 오게 한다(화가 알고리즘).
        for i, dx, dy, dz in sorted(axes, key=lambda a: a[3]):
            ex, ey = cx + dx * scale, cy + dy * scale
            col = QColor(self.AXIS_COLORS[i])
            if dz < 0:                           # 화면 뒤로 향하는 축은 흐리게
                col.setAlpha(120)
            pen = QPen(col, 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(cx, cy), QPointF(ex, ey))
            # 라벨 — 축 끝 너머로 LABEL_GAP 만큼 같은 방향으로 더 민다.
            ll = math.hypot(dx, dy) or 1.0
            lx = ex + dx / ll * self.LABEL_GAP
            ly = ey + dy / ll * self.LABEL_GAP
            p.setPen(col)
            p.drawText(QRectF(lx - 6.0, ly - 6.0, 12.0, 12.0),
                       int(Qt.AlignmentFlag.AlignCenter), self.AXIS_LABELS[i])
        # 원점(축이 뻗어 나오는 점) 작은 점
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(210, 210, 210))
        p.drawEllipse(QPointF(cx, cy), 1.6, 1.6)
        p.end()


# ---------------------------------------------------------------------------
# GraphWindow
# ---------------------------------------------------------------------------

class GraphWindow(QWidget, Ui_GraphForm):
    MAX_TRACKS = 60                            # 트랙 개수 상한 — 넘으면 분리 안 하고 단일 궤적 + 안내
    # ---- 시간 타임라인 / 재생 ----
    SLIDER_STEPS = 1000                         # bar_time 슬라이더 해상도(0..STEPS) → 분수로 시각 T 매핑
    PLAY_INTERVAL_MS = 33                       # 재생 타이머 간격(≈30fps)
    PLAY_BASE_DURATION_SEC = 12.0              # 1× 에서 전체 타임라인을 재생하는 데 걸리는 시간(초)
    SPEEDS = (0.5, 1.0, 2.0, 4.0)              # button_time_speed 순환 배속
    DEFAULT_COLOR = (0.20, 0.85, 0.95, 1.0)    # 트랙 그룹 없을 때 단일 궤적 색(cyan)
    MARKER_SIZE   = 9                           # 트랙별 현재 위치 마커 점 크기(px) — 데이터 점(3.5)·선(2.0)보다 큼
    POINT_SIZE    = 4.0                          # 각 데이터 포인트 점 크기(px) — 선폭(2.0)보다 약간 큼.
                                                 #   데이터가 1개뿐이면 선이 안 그려지므로 점으로 보이게 함.
    DOT_DARKEN    = 0.8                          # 데이터 점 색 = 트랙 선색 × 이 비율(살짝 어둡게).
                                                 #   ⚠ 점은 'translucent' 로 그린다 — 기본 'additive' 는
                                                 #   어두운 배경서 색이 더해져 흰색으로 번진다.
    # 자취(trail) 모드에서 '아직 도달 못한 전체 경로'를 깔아두는 흐린 선.
    _BG_LEVEL = 30 / 255.0                       # 배경(QColor(30,30,30)) 밝기 — ghost 를 배경 쪽으로 섞을 기준
    GHOST_FRACTION = 0.28                        # ghost 색 = 배경↔트랙색 보간 비율(0=배경, 1=원색). 작을수록 흐림
    GHOST_WIDTH = 1.2                            # ghost 선 두께(자취 솔리드 2.0 보다 가늘게)
    MARKER_LABEL_OFFSET = (8.0, -15.0)          # 마커 좌표 라벨 화면 오프셋(점 위쪽; 화면 y 위=-)
    MARKER_LABEL_COLOR = QColor(255, 250, 180)  # 마커 좌표 라벨 색(연노랑)
    # 깊이검사 끈 GL 옵션 — 마커/라벨을 궤적·축 위(최상단)에 항상 그린다(translucent + DEPTH_TEST off).
    _ON_TOP_GL = {
        GL_DEPTH_TEST: False,
        GL_BLEND: True,
        GL_ALPHA_TEST: False,
        GL_CULL_FACE: False,
        'glBlendFunc': (GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA),
    }
    CAMERA_DISTANCE_FACTOR = 1.2               # 카메라 거리 = bounding box 대각선 × 이 값(작을수록 크게 보임)
    AXIS_COLOR = (0.5, 0.5, 0.5, 1.0)          # x/y/z 축선 색 = 진한 회색 고정(어두운 배경 대비 위해 너무 어둡지 않게)
    AXIS_LEN = 10.0                            # 정규화 큐브 한 변 길이(각 축 [min,max] → [OFFSET,AXIS_LEN] 로 매핑)
    # 데이터 시작점(min)이 원점에 딱 붙지 않도록 원점 쪽에 두는 여백 — 축 길이의 비율.
    # min → ORIGIN_OFFSET_FRAC*L, max → L 로 매핑(데이터가 원점에서 5% 떨어져 시작).
    ORIGIN_OFFSET_FRAC = 0.1
    AXIS_TICKS = 10                            # 그리드 칸 수(배경 바둑판 — 데이터 눈금과 별개의 균등 배경)
    TICK_COUNT_TARGET = 8                       # nice 눈금 목표 간격 수(실제 개수는 라운드 step 에 따라 가변)
    TICK_FONT_PT = 8                           # 눈금 숫자 글씨 크기(기본 GLTextItem 16 의 절반)
    # 눈금 숫자 화면 픽셀 오프셋 (dx, dy) — x=축 아래, y·z=축 왼쪽(전부 축 바깥쪽). 화면 y 는 아래로 +.
    # 눈금선과 숫자의 수직거리를 적당히 띄움(x=아래로, y·z=왼쪽으로). y 는 옆에서 본 뷰(축이 가로로
    # 누움)에서 축과 겹치지 않도록 아래(dy)로도 충분히 내림.
    TICK_LABEL_OFFSET = {0: (4.0, 12.0), 1: (4.0, 12.0), 2: (4.0, 12.0)}
    AXIS_OVERSHOOT = 1.05                       # 축선을 범위(L)보다 살짝 더 연장(끝 라벨 x/y/z 에 가깝게)
    TICK_DOT_SIZE = 5                           # 축 눈금 위치 점 크기(px)
    TICK_DOT_COLOR = (0.80, 0.80, 0.80, 1.0)    # 축 눈금 위치 점 색(연회색)
    # ---- 마우스 호버 정보(툴팁/링) ----
    PICK_RADIUS_PT = 13                         # 점(꼭짓점/마커) 히트 판정 반경(px) — 이내면 좌표 툴팁
    PICK_RADIUS_LINE = 8                        # 선(세그먼트) 히트 판정 반경(px) — 이내면 트랙명만
    HOVER_RING_SIZE = 18                        # 호버 강조 헤일로 크기(px) — 마커(9)보다 크게
    HOVER_RING_COLOR = (1.0, 1.0, 1.0, 0.55)    # 호버 강조 헤일로 색(밝은 반투명 흰색)

    def __init__(self, icon_path=None):
        # ⚠ 부모를 두지 않는다(독립 top-level 창). 메인 창(ViewerWindow)을 부모로 둔 채 Qt.Window
        #   플래그를 주면, 이 안의 GLViewWidget 이 네이티브 surface 를 요구하면서 첫 표시 때 부모
        #   메인 창의 네이티브 윈도우까지 재생성돼 메인 창이 닫혔다 다시 열리는 깜빡임이 생긴다.
        #   parent=None 이면 그 재생성 트리거가 사라진다. 대신 메인 창 closeEvent 에서 이 창을 닫는다.
        super().__init__(None)
        self.setupUi(self)
        self.icon_path = icon_path
        self.setWindowTitle("3D Graph")
        if icon_path:
            self.setWindowIcon(QIcon(os.path.join(icon_path, "button_csv_view.png")))

        self._loading = False
        self._title = ""            # set_data 의 title (그래프 이미지 저장 기본 파일명에 사용)
        self.headers = []
        self.rows = []
        self._numeric = set()
        self._float_cache = {}      # col -> ndarray
        self._track_rows = {}       # value -> [row idx]  (트랙 열 활성 + 상한 이내일 때만)
        self._track_color = {}      # value -> (r,g,b,a)
        self._track_visible = {}    # value -> bool
        self._track_cb = {}         # value -> QCheckBox
        # 트랙별 '시간순 정렬' 캐시 — 자취(trail) 그릴 때 T 까지의 prefix 를 searchsorted 로 O(log n)
        # 잘라 쓰려고 미리 만든다(트랙/시간 열 바뀔 때만 재구성). value -> (정렬된 행 idx ndarray).
        self._track_sorted = {}     # value -> ndarray[int]  (시간 오름차순, 시간 비활성이면 행 순서)
        self._track_sorted_times = {}  # value -> ndarray[float] (위와 같은 순서의 시간값) 또는 None
        # 시간 타임라인 상태 (combo_time 열 기준)
        self._time_values = None    # 전 행의 시간값 ndarray (또는 None=시간 비활성)
        self._time_active = False   # True=자취(trail)+마커+재생 / False=전체(full)
        self._time_is_clock = False # True=시계표기(H:M:S) → 타임스탬프를 시계로 포맷
        self._t0 = 0.0              # 타임라인 최소/최대 시각
        self._t1 = 0.0
        self._time_sorted_all = None  # 전 트랙 합친 '실제 표본 시각' 오름차순(중복 제거) — 표시 시각 스냅용
        # 재생(playback)
        self._playing = False
        self._speed_idx = 1         # SPEEDS 인덱스(기본 1.0×)
        self._pos = 0.0            # 슬라이더 위치 float 누산기(정수 슬라이더의 끊김 방지)
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(self.PLAY_INTERVAL_MS)
        self._play_timer.timeout.connect(self._on_play_tick)
        self._line_items = []       # 궤적 GLLinePlotItem 들
        self._axis_items = []       # X/Y/Z 축선 GLLinePlotItem 들(진한 회색·원점 고정)
        self._labels = []           # 축 이름(x/y/z) + 눈금 숫자 GLTextItem 들
        self._tick_font = QFont("Helvetica", self.TICK_FONT_PT)   # 눈금 숫자 글씨(작게)

        # ---- GL scene 기본 아이템 ----
        view = self.graph_area
        view.setBackgroundColor(QColor(30, 30, 30))
        self._grid = gl.GLGridItem()
        self._grid.setColor((128, 128, 128, 76))   # XY 바둑판 — 기본 흰색(255)의 절반 밝기(두 배 어둡게)
        self._grid.setVisible(False)
        view.addItem(self._grid)
        self._marker = gl.GLScatterPlotItem()
        self._marker.setGLOptions(self._ON_TOP_GL)   # 깊이검사 끔 → 궤적 위(최상단)에 항상
        self._marker.setDepthValue(10)               # 그리기 순서도 맨 뒤(같은 depth 끼리 마지막)
        self._marker.setVisible(False)
        view.addItem(self._marker)
        # 마커 위에 띄우는 현재 위치 x/y/z 좌표 라벨(회전 0 + 화면 오프셋으로 점 위쪽에).
        self._marker_font = QFont("Helvetica", 9)
        self._marker_label = _TickText(screen_offset=self.MARKER_LABEL_OFFSET, angle=0.0,
                                       font=self._marker_font, color=self.MARKER_LABEL_COLOR)
        self._marker_label.setDepthValue(10)
        self._marker_label.setVisible(False)
        view.addItem(self._marker_label)
        self._tick_dots = gl.GLScatterPlotItem()   # 축 눈금 위치 점들(작은 점)
        self._tick_dots.setVisible(False)
        view.addItem(self._tick_dots)
        # 호버 강조 링 — 마우스가 올라간 데이터 점을 최상단(_ON_TOP_GL) 밝은 헤일로로 강조.
        self._hover_ring = gl.GLScatterPlotItem()
        self._hover_ring.setGLOptions(self._ON_TOP_GL)
        self._hover_ring.setDepthValue(11)           # 마커(10)보다 위
        self._hover_ring.setVisible(False)
        view.addItem(self._hover_ring)

        # 시야 고정: 1·2축이면 회전(드래그) 잠금, 3축만 자유. GLViewWidget 의 회전은 mouseMoveEvent
        # 의 좌클릭 orbit 이라, 잠금 중엔 그 핸들러를 건너뛴다(인스턴스 메서드 교체 — gui_viewer 의
        # edit_csv_path.mousePressEvent 교체와 동일 패턴). 휠 줌은 그대로 둔다.
        self._view_locked = False
        self._last_sel = None        # 직전 축 선택 조합 — 바뀔 때만 시야 각도 재설정(같은 차원 내 유지)
        self._gl_orig_mouse_move = self.graph_area.mouseMoveEvent
        self.graph_area.mouseMoveEvent = self._gl_mouse_move

        # 줌(휠) 연동 눈금 세분: _frame_scene 이 잡는 기본 카메라 거리를 _base_distance 로 기억해 두고,
        # 휠로 확대(거리↓)하면 그 배율만큼 눈금 target 을 키워 간격을 세분한다(2배 확대=절반 간격).
        # GLViewWidget 은 줌 시그널이 없어 wheelEvent 를 인스턴스 교체(mouseMove 와 동일 패턴)해 가로챈다.
        self._base_distance = None
        self._tick_target = self.TICK_COUNT_TARGET
        self._gl_orig_wheel = self.graph_area.wheelEvent
        self.graph_area.wheelEvent = self._gl_wheel

        # 카메라 방향 gizmo — 좌상단 reset 버튼 자리에 겹쳐 x/y/z 축 방향을 작게 표시한다.
        # 버튼을 gizmo 만큼 키워(전 영역 클릭=시점 초기화) 그 위에 마우스-투과 gizmo 를 얹는다
        # (gizmo 가 위라 항상 보이고, 클릭은 통과해 아래 버튼이 받음). geometry 는 코드로만 조정
        # → ui/ 생성본은 손대지 않는다. 카메라가 돌면 _update_gizmo() 로 다시 그려 실시간 추종.
        self._setup_gizmo()
        # 마우스 호버 정보(점=좌표/트랙/시간/행, 선=트랙명) — 오버레이 라벨 + 강조 링.
        self._setup_hover()

        # 축 범위(min/max) 편집칸 — 축 index(0=x,1=y,2=z) → (min, max) QLineEdit 쌍.
        # 축 열을 고르면 그 열 데이터의 min/max 로 자동 채우고, 사용자가 숫자를 바꾸면 그 범위로
        # 정규화해 그래프에 적용한다(set_xlim/ylim/zlim 방식 — 축마다 독립 스케일).
        self._range_edits = {
            0: (self.lineEdit_x_min, self.lineEdit_x_max),
            1: (self.lineEdit_y_min, self.lineEdit_y_max),
            2: (self.lineEdit_z_min, self.lineEdit_z_max),
        }
        self._axis_range = {0: None, 1: None, 2: None}   # axis → (vmin, vmax) 또는 None
        self._reset_ranges()

        # ---- 시그널 ----
        self.combo_x.currentIndexChanged.connect(lambda: self._on_axis_column_changed(0))
        self.combo_y.currentIndexChanged.connect(lambda: self._on_axis_column_changed(1))
        self.combo_z.currentIndexChanged.connect(lambda: self._on_axis_column_changed(2))
        for ax, (e_min, e_max) in self._range_edits.items():
            e_min.editingFinished.connect(lambda a=ax: self._on_range_edited(a))
            e_max.editingFinished.connect(lambda a=ax: self._on_range_edited(a))
        # time 열 = 자취(trail) on/off + 타임라인 정의. 슬라이더는 0..STEPS 분수로 시각 T 매핑.
        self.combo_time.currentIndexChanged.connect(self._on_time_column_changed)
        self.bar_time.valueChanged.connect(self._on_time_slider_changed)
        # sliderMoved=사용자 드래그만(프로그램적 setValue 는 안 옴) → 누산기를 사용자 위치로 동기화
        self.bar_time.sliderMoved.connect(lambda v: setattr(self, "_pos", float(v)))
        self.combo_track.currentIndexChanged.connect(self._on_track_column_changed)
        # 재생 컨트롤
        self.button_time_play.clicked.connect(self._toggle_play)
        self.button_time_speed.clicked.connect(self._cycle_speed)
        # 시점(카메라) 초기화 — 처음 봤던 각도·거리·중심으로 복귀(이미지/텍스트는 추후 결정)
        self.button_graph_reset.clicked.connect(self._reset_view)
        self.button_time_play.setText("▷")
        self.button_time_speed.setText(f"{self.SPEEDS[self._speed_idx]:.1f}x")
        self.plainTextEdit_timestamp.setPlainText("")
        # plainTextEdit_timestamp 우측 정렬 — QPlainTextEdit 은 setAlignment 가 없어 문서 기본
        # 텍스트 옵션으로 정렬을 준다(이후 setPlainText 한 텍스트에도 적용됨).
        _ts_opt = self.plainTextEdit_timestamp.document().defaultTextOption()
        _ts_opt.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.plainTextEdit_timestamp.document().setDefaultTextOption(_ts_opt)

        # ---- 그래프 이미지 저장/복사 ----
        # Ctrl+S = 현재 화면(graph_area) 그대로 PNG 저장, Ctrl+C = 클립보드 복사.
        # 별도 top-level 창이라 메인 뷰어의 Ctrl+S/Ctrl+C 와 충돌하지 않는다(WindowShortcut).
        # 짧은 알림(toast)은 .ui 를 안 건드리려고 코드로만 만든다(gizmo/hover 라벨과 동일 패턴).
        self._toast = QLabel(self)
        self._toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast.hide)
        sc_save = QShortcut(QKeySequence("Ctrl+S"), self)
        sc_save.activated.connect(self.save_graph_image)
        sc_copy = QShortcut(QKeySequence("Ctrl+C"), self)
        sc_copy.activated.connect(self.copy_graph_image)

    # ---------- 데이터 주입 (재오픈 시 새 CSV 로 갱신) ----------
    def set_data(self, headers, rows, title=None):
        self._loading = True
        self._title = title or ""   # 저장 기본 파일명용
        self._last_sel = None       # 새 CSV → 시야를 다시 잡도록 초기화
        self._pause()               # 재생 중이었으면 멈춤
        self.headers = list(headers or [])
        self.rows = rows or []
        self._float_cache.clear()
        self._numeric = numeric_columns(self.headers, self.rows)
        if title:
            self.setWindowTitle(f"{title} - 3D Graph")
        self._populate_combos()
        self._reset_ranges()        # 축 범위 편집칸 비우고 비활성(전부 '(none)' 시작)
        self._setup_timeline()      # combo_time 기준 타임라인/슬라이더(기본 '(row #)'=시간 비활성=전체)
        self._loading = False
        self._build_track_list()    # 트랙 열 기본 '(none)' → 목록 비움
        self._rebuild_track_order() # 트랙×시간 정렬 캐시 구성
        self._update_timestamp()
        self._redraw()

    # ---------- combo 채우기 ----------
    def _populate_combos(self):
        # x/y/z: '(none)'(data=-1) + 모든 헤더(data=열 인덱스, 비숫자는 disable)
        for combo in (self.combo_x, self.combo_y, self.combo_z):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(none)", -1)
            for c, name in enumerate(self.headers):
                combo.addItem(str(name), c)
                if c not in self._numeric:
                    item = combo.model().item(combo.count() - 1)
                    if item is not None:
                        item.setEnabled(False)   # 비숫자 열 → 선택 불가(회색)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        # time / track: 모든 열 허용. time 은 '(row #)', track 은 '(none)'.
        self.combo_time.blockSignals(True)
        self.combo_time.clear()
        self.combo_time.addItem("(row #)", -1)
        for c, name in enumerate(self.headers):
            self.combo_time.addItem(str(name), c)
        self.combo_time.setCurrentIndex(0)
        self.combo_time.blockSignals(False)

        self.combo_track.blockSignals(True)
        self.combo_track.clear()
        self.combo_track.addItem("(none)", -1)
        for c, name in enumerate(self.headers):
            self.combo_track.addItem(str(name), c)
        self.combo_track.setCurrentIndex(0)
        self.combo_track.blockSignals(False)
        # ⚠ 자동 선택하지 않는다 — x/y/z/time/track 모두 '(none)'(index 0) 으로 비워둔 채 시작한다.

    def _combo_col(self, combo):
        data = combo.currentData()
        return -1 if data is None else int(data)

    # ---------- 축 범위(min/max) ----------
    def _reset_ranges(self):
        """모든 축 범위를 비우고 편집칸을 빈칸·비활성으로(전부 '(none)' 시작/새 CSV)."""
        for axis in (0, 1, 2):
            self._axis_range[axis] = None
            self._fill_range_edits(axis, None)

    def _on_axis_column_changed(self, axis):
        """축 콤보 변경 → 그 열 데이터의 min/max 를 범위로 잡아 편집칸에 채우고 다시 그린다."""
        if self._loading:
            return
        combo = (self.combo_x, self.combo_y, self.combo_z)[axis]
        col = self._combo_col(combo)
        rng = self._data_minmax(col) if col >= 0 else None
        self._axis_range[axis] = rng
        self._fill_range_edits(axis, rng)
        self._redraw()

    def _data_minmax(self, col):
        """col 열의 유한값 (min, max). 유한값이 없으면 None."""
        arr = self._floats(col)
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            return None
        return (float(finite.min()), float(finite.max()))

    def _fill_range_edits(self, axis, rng):
        """축 편집칸(min/max)에 범위를 표시. rng=None 이면 빈칸·비활성."""
        e_min, e_max = self._range_edits[axis]
        for e in (e_min, e_max):
            e.blockSignals(True)
        if rng is None:
            e_min.clear()
            e_max.clear()
            e_min.setEnabled(False)
            e_max.setEnabled(False)
        else:
            e_min.setText(f"{rng[0]:g}")
            e_max.setText(f"{rng[1]:g}")
            e_min.setEnabled(True)
            e_max.setEnabled(True)
        for e in (e_min, e_max):
            e.blockSignals(False)

    def _on_range_edited(self, axis):
        """사용자가 min/max 를 고치고 포커스를 떠나면 → 그 범위로 정규화해 다시 그린다.
        잘못된 입력(숫자 아님·min≥max)은 직전 값으로 되돌린다."""
        if self._loading or self._axis_range.get(axis) is None:
            return
        cur = self._axis_range[axis]
        e_min, e_max = self._range_edits[axis]
        lo = self._parse_edit(e_min.text(), cur[0])
        hi = self._parse_edit(e_max.text(), cur[1])
        if lo >= hi:                                # min≥max → 무효, 직전 값 복원
            self._fill_range_edits(axis, cur)
            return
        if (lo, hi) == cur:                          # 변동 없음
            return
        self._axis_range[axis] = (lo, hi)
        self._fill_range_edits(axis, (lo, hi))       # 표시 정규화(공백 정리)
        self._redraw()

    @staticmethod
    def _parse_edit(text, fallback):
        try:
            return float(str(text).strip())
        except (ValueError, TypeError):
            return fallback

    def _axis_pos(self, frac):
        """데이터 비율(frac: 0=min, 1=max)을 축 위 그리기 좌표로. 원점 쪽에 ORIGIN_OFFSET_FRAC
        여백을 둬 min → off, max → L 로 매핑(frac 은 scalar/ndarray 둘 다 가능)."""
        off = self.ORIGIN_OFFSET_FRAC * self.AXIS_LEN
        return off + frac * (self.AXIS_LEN - off)

    def _nice_axis_ticks(self, axis):
        """축 i 의 깔끔한 눈금 — [(그리기좌표, 값)...], step. 범위 없으면 ([], 0).
        눈금 점·숫자 라벨이 같은 위치를 쓰도록 단일 출처."""
        rng = self._axis_range.get(axis)
        if rng is None:
            return [], 0.0
        vmin, vmax = rng
        span = (vmax - vmin) or 1.0
        # 원점 쪽 여백의 '절반'(ORIGIN_OFFSET_FRAC/2 = 5%) 선까지는 데이터(min)보다 아래의 라운드
        # 눈금도 허용 → 데이터가 1 부터여도 '0' 같은 눈금을 그 여백 안에 표시(5% 선은 안 넘음).
        L = self.AXIS_LEN
        off = self.ORIGIN_OFFSET_FRAC * L
        margin = (self.ORIGIN_OFFSET_FRAC / 2.0) * L
        v_lo = vmin - (margin / (L - off)) * span   # off-margin(5% 선) 위치에 대응하는 데이터 값
        values, step = nice_ticks(vmin, vmax, self._tick_target, lo=v_lo)
        return [(self._axis_pos((v - vmin) / span), v) for v in values], step

    def _norm_axis(self, axis, col):
        """col 열 전 행 값을 그 축의 [min,max] 범위로 [off,AXIS_LEN] 정규화(NaN 유지).
        열 미선택/범위 없음이면 0 배열(그 차원 납작)."""
        n = len(self.rows)
        if col < 0:
            return np.zeros(n)
        rng = self._axis_range.get(axis)
        if rng is None:
            return np.zeros(n)
        vmin, vmax = rng
        span = (vmax - vmin) or 1.0
        return self._axis_pos((self._floats(col) - vmin) / span)

    # ---------- 시간 타임라인 / 재생 ----------
    def _setup_timeline(self):
        """combo_time 열로 타임라인을 정한다. 숫자/시계로 파싱되면 '실제 시간 값' 기준(자취),
        열을 골랐지만 파싱 불가면 행 인덱스로 폴백(자취), '(row #)'(=col<0) 선택이면 비활성(전체)."""
        col = self._combo_col(self.combo_time)
        n = len(self.rows)
        self._time_values = None
        self._time_is_clock = False
        self._t0 = self._t1 = 0.0
        self._time_active = False
        self._time_sorted_all = None
        if col >= 0 and n > 0:
            tv = column_time_values(self.rows, col)
            finite = tv[np.isfinite(tv)]
            if finite.size >= 2 and float(finite.max()) > float(finite.min()):
                self._time_values = tv                      # 실제 시간 값 기준
                self._time_is_clock = self._col_looks_clock(col)
                self._t0 = float(finite.min()); self._t1 = float(finite.max())
                self._time_active = True
                self._time_sorted_all = np.unique(finite)   # 실제 표본 시각(오름차순·중복제거)
            elif n > 1:                                     # 파싱 실패 → 행 인덱스 폴백
                self._time_values = np.arange(n, dtype=float)
                self._t0 = 0.0; self._t1 = float(n - 1)
                self._time_active = True
                self._time_sorted_all = self._time_values   # arange = 이미 오름차순 실제값
        # 슬라이더: 시간 활성이면 0..STEPS(분수로 T 매핑), 아니면 비활성
        self.bar_time.blockSignals(True)
        self.bar_time.setMinimum(0)
        self.bar_time.setMaximum(self.SLIDER_STEPS if self._time_active else 0)
        self.bar_time.setValue(0)
        self.bar_time.setEnabled(self._time_active)
        self.bar_time.blockSignals(False)
        self._pos = 0.0
        self._set_play_enabled(self._time_active)

    def _col_looks_clock(self, col):
        """첫 비어있지 않은 셀에 ':' 가 있으면 시계 표기로 간주(타임스탬프 포맷 분기용)."""
        for r in self.rows:
            if col < len(r) and r[col] is not None:
                s = str(r[col]).strip()
                if s:
                    return ":" in s
        return False

    def _current_time(self):
        """슬라이더 위치(0..STEPS) → 연속 시각 T(자취 cutoff·마커 frontier 계산용 '내부' 값).
        ⚠ 이 값은 보간된 연속값이라 데이터에 없을 수 있다 → '표시'에는 쓰지 말 것
        (표시는 `_current_sample_time` 으로 실제 표본 시각에 스냅). 시간 비활성이면 None."""
        if not self._time_active:
            return None
        hi = self.bar_time.maximum()
        frac = (self.bar_time.value() / hi) if hi else 0.0
        return self._t0 + frac * (self._t1 - self._t0)

    def _current_sample_time(self):
        """현재 슬라이더 위치에 해당하는 '실제 데이터 시각' — 보간값(_current_time)이 아니라
        T 이하 가장 최근의 실제 표본 시각으로 스냅(자취/마커 frontier 와 동일 의미). 없으면 None."""
        arr = self._time_sorted_all
        if not self._time_active or arr is None or arr.size == 0:
            return None
        T = self._current_time()
        if T is None:
            return None
        k = int(np.searchsorted(arr, T, side="right"))
        return float(arr[k - 1]) if k > 0 else float(arr[0])

    def _on_time_column_changed(self, *_):
        if self._loading:
            return
        self._pause()
        self._setup_timeline()
        self._rebuild_track_order()      # 시간 순서 바뀜 → 트랙 정렬 캐시 갱신
        self._update_timestamp()
        self._redraw()

    def _on_time_slider_changed(self, *_):
        """슬라이더 이동(사용자 드래그/재생 틱) → 자취·마커만 다시 그린다(축·카메라는 유지)."""
        if self._loading:
            return
        self._draw_lines_and_markers()
        self._update_timestamp()

    def _update_timestamp(self):
        """plainTextEdit_timestamp = '현재 / 총' 진행 표시(현재=실제 표본 시각). 시간 비활성이면 빈칸."""
        if not self._time_active:
            self.plainTextEdit_timestamp.setPlainText("")
            return
        self.plainTextEdit_timestamp.setPlainText(
            f"{self._fmt_time(self._current_sample_time())} / {self._fmt_time(self._t1)}")

    def _fmt_time(self, t):
        if t is None or not math.isfinite(t):
            return "-"
        return format_clock(t) if self._time_is_clock else f"{t:g}"

    # ---------- 재생(playback) ----------
    def _set_play_enabled(self, on):
        self.button_time_play.setEnabled(on)
        self.button_time_speed.setEnabled(on)
        if not on:
            self._pause()

    def _toggle_play(self):
        if not self._time_active:
            return
        if self._playing:
            self._pause()
            return
        if self.bar_time.value() >= self.bar_time.maximum():    # 끝에서 누르면 처음부터
            self.bar_time.setValue(0)
        self._pos = float(self.bar_time.value())
        self._playing = True
        self.button_time_play.setText("⏸")
        self._play_timer.start()

    def _pause(self):
        self._playing = False
        if self._play_timer.isActive():
            self._play_timer.stop()
        self.button_time_play.setText("▶")

    def _on_play_tick(self):
        span = self._t1 - self._t0
        if span <= 0 or not self._time_active:
            self._pause()
            return
        # 한 틱당 슬라이더 증가량 = STEPS × (틱초 / 기준재생시간) × 배속 (누산기로 끊김 방지)
        inc = (self.SLIDER_STEPS * (self.PLAY_INTERVAL_MS / 1000.0)
               / self.PLAY_BASE_DURATION_SEC * self.SPEEDS[self._speed_idx])
        self._pos += inc
        if self._pos >= self.SLIDER_STEPS:
            self.bar_time.setValue(self.SLIDER_STEPS)           # 끝 도달 → 정지
            self._pause()
        else:
            self.bar_time.setValue(int(self._pos))

    def _cycle_speed(self):
        self._speed_idx = (self._speed_idx + 1) % len(self.SPEEDS)
        self.button_time_speed.setText(f"{self.SPEEDS[self._speed_idx]:.1f}x")

    # ---------- 그리기 ----------
    def _floats(self, col):
        arr = self._float_cache.get(col)
        if arr is None:
            arr = column_floats(self.rows, col)
            self._float_cache[col] = arr
        return arr

    def _stack(self, X, Y, Z, idxs):
        """행 인덱스들 → (M,3) 점 배열. 비유한(NaN/inf) 행은 제외."""
        idx = np.asarray(idxs, dtype=int)      # ndarray/range/list 모두 무복사 또는 1회 변환
        if idx.size == 0:
            return np.empty((0, 3))
        pts = np.column_stack((X[idx], Y[idx], Z[idx]))
        return pts[np.isfinite(pts).all(axis=1)]

    def _clear_lines(self):
        for it in self._line_items:
            self.graph_area.removeItem(it)
        self._line_items = []

    def _clear_axes(self):
        for it in self._axis_items:
            self.graph_area.removeItem(it)
        self._axis_items = []

    def _clear_labels(self):
        for t in self._labels:
            self.graph_area.removeItem(t)
        self._labels = []

    def _add_line(self, pts, color):
        item = gl.GLLinePlotItem(pos=pts, color=color, width=2.0,
                                 antialias=True, mode='line_strip')
        self.graph_area.addItem(item)
        self._line_items.append(item)
        # 각 데이터 포인트마다 작은 점 — 데이터가 1개뿐이면 line_strip 이 아무것도 안 그려서
        # 아예 안 보이는 문제를 막는다. 점은 선폭보다 약간 크게(POINT_SIZE).
        # ⚠ glOptions='translucent': 기본 'additive' 는 어두운 배경서 색이 더해져 점이 흰색으로
        #    번진다 → translucent 로 트랙색(살짝 어둡게)을 제대로 표시.
        dots = gl.GLScatterPlotItem(pos=pts, color=self._dot_color(color),
                                    size=self.POINT_SIZE, pxMode=True)
        dots.setGLOptions('translucent')
        self.graph_area.addItem(dots)
        self._line_items.append(dots)

    def _dot_color(self, color):
        """데이터 점 색 = 트랙 선색을 DOT_DARKEN 만큼 어둡게(흰색 번짐 방지 + 선과 살짝 대비)."""
        f = self.DOT_DARKEN
        return (color[0] * f, color[1] * f, color[2] * f, 1.0)

    def _ghost_color(self, color):
        """트랙 색을 배경 쪽으로 섞어 흐리게 만든 (r,g,b,1). 자취 미도달(전체 경로) 표시용."""
        bg, f = self._BG_LEVEL, self.GHOST_FRACTION
        r, g, b = color[0], color[1], color[2]
        return (bg + (r - bg) * f, bg + (g - bg) * f, bg + (b - bg) * f, 1.0)

    def _add_ghost_line(self, pts, color):
        """전체 경로를 흐린 색 가는 선으로(아직 도달 못한 부분도 형태가 보이게). 점(scatter)은 안 찍음."""
        item = gl.GLLinePlotItem(pos=pts, color=color, width=self.GHOST_WIDTH,
                                 antialias=True, mode='line_strip')
        self.graph_area.addItem(item)
        self._line_items.append(item)

    def _add_axis_line(self, p0, p1):
        item = gl.GLLinePlotItem(pos=np.array([p0, p1], dtype=float),
                                 color=self.AXIS_COLOR, width=1.5, antialias=True)
        self.graph_area.addItem(item)
        self._axis_items.append(item)

    def _axis_cols(self):
        return (self._combo_col(self.combo_x),
                self._combo_col(self.combo_y),
                self._combo_col(self.combo_z))

    def _visible_groups(self):
        """그릴 (트랙값, 색) 쌍 — 트랙 열이 있으면 보이는 트랙만, 없으면 단일 그룹(None)."""
        if self._track_rows:
            for v in self._track_rows:
                if self._track_visible.get(v, True):
                    yield v, self._track_color.get(v, self.DEFAULT_COLOR)
        else:
            yield None, self.DEFAULT_COLOR

    def _rebuild_track_order(self):
        """트랙별 행 인덱스를 시간 오름차순으로 정렬해 캐시(자취 prefix 를 searchsorted 로 O(log n)
        잘라 쓰려고). 시간 비활성이면 행 순서 그대로(정렬 시간=None). 트랙/시간 열 바뀔 때만 호출."""
        self._track_sorted = {}
        self._track_sorted_times = {}
        groups = self._track_rows if self._track_rows else {None: range(len(self.rows))}
        tv = self._time_values
        for value, idxs in groups.items():
            arr = idxs if isinstance(idxs, np.ndarray) else np.fromiter(idxs, dtype=int)
            if self._time_active and tv is not None and arr.size:
                t = tv[arr]
                order = np.argsort(t, kind="stable")    # NaN 은 끝으로 → 자취 prefix 에서 자연 제외
                self._track_sorted[value] = arr[order]
                self._track_sorted_times[value] = t[order]
            else:
                self._track_sorted[value] = arr
                self._track_sorted_times[value] = None

    def _draw_lines_and_markers(self):
        """궤적 선 + 트랙별 현재 위치 마커만 그린다(축·그리드·카메라는 안 건드림 → 재생/스크럽 핫패스).
        시간 활성(자취): 각 트랙을 시각 T 까지의 prefix 로 그리고 마지막 점에 마커. 비활성(전체): 전부."""
        cx, cy, cz = self._axis_cols()
        self._clear_lines()
        self._drawn_tracks = []         # 호버 피킹용 — 이번에 '솔리드'로 그린 트랙별 idx 기록
        self._hover_dirty = True        # 그린 점이 바뀌었으니 호버 인덱스는 다음 호버 때 재구성
        if (cx < 0 and cy < 0 and cz < 0) or not self.rows:
            self._update_markers([], [])
            self._hide_hover()
            return []
        # 고른 축은 그 축의 [min,max] 범위로 [0,AXIS_LEN] 정규화, 안 고른 축은 0(그 차원 납작).
        X = self._norm_axis(0, cx)
        Y = self._norm_axis(1, cy)
        Z = self._norm_axis(2, cz)
        T = self._current_time()
        trail = self._time_active and T is not None
        all_pts, mk_pts, mk_cols = [], [], []
        for value, color in self._visible_groups():
            idx_arr = self._track_sorted.get(value)
            if idx_arr is None or len(idx_arr) == 0:
                continue
            if trail:
                # ① 전체 경로를 '흐린 색'으로 먼저 깔아 아직 도달 못한 부분의 형태도 보이게 한다.
                full_pts = self._stack(X, Y, Z, idx_arr)
                if len(full_pts) >= 2:
                    self._add_ghost_line(full_pts, self._ghost_color(color))
                if len(full_pts):
                    all_pts.append(full_pts)    # 프레이밍은 항상 전체 경로 기준(재생 중 안정)
                # ② 그 위에 시각 T 까지의 자취(진한 색) + 현재 위치 마커.
                ts = self._track_sorted_times.get(value)
                k = int(np.searchsorted(ts, T, side="right")) if ts is not None else len(idx_arr)
                if k <= 0:                      # 첫 표본 시각보다 이르면 ghost 만(자취/마커 없음)
                    continue
                drawn = idx_arr[:k]
                pts = self._stack(X, Y, Z, drawn)
                if len(pts):
                    self._add_line(pts, color)
                    self._drawn_tracks.append((value, color, drawn))
                    mk_pts.append(pts[-1])      # 현재 위치 = T 이하 마지막 유한 점
                    mk_cols.append(color)
            else:                               # 전체 모드 — 궤적 전부(마커 없음)
                pts = self._stack(X, Y, Z, idx_arr)
                if len(pts):
                    self._add_line(pts, color)
                    self._drawn_tracks.append((value, color, idx_arr))
                    all_pts.append(pts)
        self._update_markers(mk_pts, mk_cols)
        return all_pts

    def _redraw(self, *_):
        """구조 변경(축·범위·트랙·시간 열·새 CSV)용 전체 다시 그리기: 선·마커 + 축·그리드·카메라."""
        if self._loading:
            return
        cx, cy, cz = self._axis_cols()
        # 축을 1개 이상 골라야 그린다 (1개=1차원·2개=2차원·3개=3차원).
        if (cx < 0 and cy < 0 and cz < 0) or not self.rows:
            self._clear_lines()
            self._grid.setVisible(False)
            self._clear_axes()
            self._clear_labels()
            self._marker.setVisible(False)
            self._marker_label.setVisible(False)
            self._tick_dots.setVisible(False)
            self._drawn_tracks = []
            self._hover_dirty = True
            self._hide_hover()
            return
        self._draw_lines_and_markers()
        self._frame_scene((cx >= 0, cy >= 0, cz >= 0))

    def _reset_view(self):
        """카메라 '시점'을 초기 상태로 복귀 — 현재 선택 축 기준 기본 각도(`_view_for`)·기본 거리·
        큐브 중심으로. 회전/휠 줌으로 바뀐 시야를 처음 봤던 상태로 되돌린다(시간·자취·트랙 선택은 유지).
        ⚠ `_last_sel=None` 으로 둬야 같은 축 조합이어도 `_frame_scene` 이 각도/회전잠금을 강제 재설정."""
        cx, cy, cz = self._axis_cols()
        if (cx < 0 and cy < 0 and cz < 0) or not self.rows:
            return
        self._last_sel = None
        self._redraw()

    def _frame_scene(self, sel):
        """X/Y/Z 축(진한 회색·원점 고정)·바닥 그리드·눈금 라벨·카메라를 정규화 큐브에 맞춘다.
        각 축은 자기 [min,max] 범위로 [0,AXIS_LEN] 정규화되므로 큐브 한 변 길이는 항상 L=AXIS_LEN
        고정. 카메라는 데이터가 아니라 '큐브(0~L)'에 프레이밍 → 범위를 좁히면 그 구간으로 확대된다.
        축은 화면 X/Y/Z 에 고정(데이터 따라 이동 안 함), 선택된 축만 그린다. sel=(x선택, y선택, z선택).
        ⚠ 축/그리드/눈금/카메라는 'sel + 축 범위'(정규화 큐브)로만 결정 → 그릴 점이 없어도
          (트랙 전부 해제·전부 NaN·재생 시작 전) 항상 그린다. 마커는 _update_markers 가 이미 처리."""
        self._clear_axes()
        self._clear_labels()
        L = self.AXIS_LEN
        sx, sy, sz = sel
        dim = sum(sel)
        self._tick_target = self.TICK_COUNT_TARGET   # 새 프레이밍 = 기본 줌 → 눈금 밀도 기본값으로

        # 축선 — '선택된 축만' (선택 안 된 축은 안 보이게). 원점에서 +방향으로 L 보다 살짝 더 길게
        # (AXIS_OVERSHOOT) 그려 끝 라벨(x/y/z)에 가깝게 닿는다.
        for i in range(3):
            if not sel[i]:
                continue
            p1 = [0.0, 0.0, 0.0]
            p1[i] = L * self.AXIS_OVERSHOOT
            self._add_axis_line([0.0, 0.0, 0.0], p1)

        # 축 눈금 점 + 숫자 라벨 — 선택된 축의 nice 눈금 위치마다(점·숫자 동일 위치).
        self._rebuild_tick_dots(sel)
        self._add_axis_labels(sel, L)

        # 바둑판 그리드 — 선택된 평면에. 3축/2축(xy)=XY 평면, 2축(xz)=XZ, 2축(yz)=YZ.
        # 1축은 평면이 아니므로 그리드 숨김. (rotate→translate 순서: 로컬 회전 후 world 이동)
        g = self._grid
        g.resetTransform()
        g.setSize(x=L, y=L)
        g.setSpacing(x=L / self.AXIS_TICKS, y=L / self.AXIS_TICKS)
        if dim >= 3 or (dim == 2 and sx and sy):
            g.translate(L / 2.0, L / 2.0, 0.0)              # XY 평면(z=0)
            g.setVisible(True)
        elif dim == 2 and sx and sz:
            g.rotate(90, 1, 0, 0)                           # XY→XZ 평면(y=0)
            g.translate(L / 2.0, 0.0, L / 2.0)
            g.setVisible(True)
        elif dim == 2 and sy and sz:
            g.rotate(90, 0, 1, 0)                           # XY→YZ 평면(x=0)
            g.translate(0.0, L / 2.0, L / 2.0)
            g.setVisible(True)
        else:
            g.setVisible(False)                             # 1축 — 평면 아님

        # 카메라/프레이밍 — 큐브(0~L) 중심. 선택 안 한 축은 0 평면.
        center = [(L / 2.0 if sel[i] else 0.0) for i in range(3)]
        diag = L * math.sqrt(max(dim, 1))
        self.graph_area.opts['center'] = Vector(center[0], center[1], center[2])
        # 시야 각도/회전잠금은 '선택 축 조합'이 바뀔 때만 설정 → 같은 차원 내 재그리기(트랙 토글·
        # 색 변경·범위 편집)에선 사용자가 맞춘 각도를 유지.
        if sel != self._last_sel:
            elev, azim, self._view_locked = self._view_for(sel)
            self.graph_area.setCameraPosition(elevation=elev, azimuth=azim)
            self._last_sel = sel
        self._base_distance = diag * self.CAMERA_DISTANCE_FACTOR   # 줌 배율 기준(눈금 세분용)
        self.graph_area.setCameraPosition(distance=self._base_distance)

    def _rebuild_tick_dots(self, sel):
        """선택된 축의 nice 눈금 위치마다 작은 점을 찍는다(숫자 라벨과 동일 위치). 줌 재계산에도 재사용."""
        dots = []
        for i in range(3):
            if not sel[i]:
                continue
            for tpos, _val in self._nice_axis_ticks(i)[0]:
                p = [0.0, 0.0, 0.0]
                p[i] = tpos
                dots.append(p)
        if dots:
            arr = np.array(dots, dtype=float)
            self._tick_dots.setData(pos=arr,
                                    color=np.tile(self.TICK_DOT_COLOR, (len(arr), 1)),
                                    size=self.TICK_DOT_SIZE, pxMode=True)
            self._tick_dots.setVisible(True)
        else:
            self._tick_dots.setVisible(False)

    def _gl_wheel(self, ev):
        """휠 줌 — 원래 GLViewWidget 동작(거리 변경) 후, 줌 배율에 맞춰 눈금만 다시 그린다(카메라는
        건드리지 않음 → _redraw 처럼 거리를 기본값으로 되돌리지 않는다)."""
        self._gl_orig_wheel(ev)
        if self._apply_tick_density():
            self._retick()

    def _apply_tick_density(self):
        """현재 카메라 거리 ÷ 기본 거리로 줌 배율을 구해 눈금 target 을 갱신. 확대(거리↓)면 배율>1 →
        target 을 키워(=간격 세분) 2배 확대 시 약 절반 간격. **딱 2단계만**: 배율을 [1,2]로 클램프해
        2배 이상 확대해도 더는 세분되지 않고(4·8배 X), 축소는 기본(1)까지만(그 아래로 안 성김).
        target 이 실제로 바뀌었으면 True(다시 그릴 필요)."""
        if not self._base_distance:
            return False
        cur = self.graph_area.opts.get('distance')
        if not cur:
            return False
        ratio = min(2.0, max(1.0, self._base_distance / cur))   # 1배(기본)~2배까지만 세분
        new_target = self.TICK_COUNT_TARGET * ratio
        if abs(new_target - self._tick_target) < 1e-6:
            return False
        self._tick_target = new_target
        return True

    def _retick(self):
        """카메라는 그대로 두고 눈금 점·숫자 라벨만 현재 _tick_target 으로 재생성(줌 중 호출)."""
        sel = self._last_sel
        if not sel or not any(sel) or not self.rows:
            return
        self._clear_labels()
        self._add_axis_labels(sel, self.AXIS_LEN)
        self._rebuild_tick_dots(sel)

    def _add_axis_labels(self, sel, L):
        """선택된 축마다 눈금 숫자(0..AXIS_TICKS 등간격, 각 위치의 실제 값)와 축 이름(x/y/z)을
        GLTextItem 으로 그린다. (GLTextItem 미지원 빌드면 통째 생략.)"""
        names = ("x", "y", "z")
        try:
            for i in range(3):
                if not sel[i]:
                    continue
                ticks, step = self._nice_axis_ticks(i)
                for tpos, val in ticks:
                    pos = [0.0, 0.0, 0.0]
                    pos[i] = tpos
                    t = _TickText(pos=np.array(pos, dtype=float),
                                  text=format_tick_value(val, step), color=QColor(170, 170, 170),
                                  font=self._tick_font,
                                  screen_offset=self.TICK_LABEL_OFFSET[i], angle=30.0)
                    self.graph_area.addItem(t)
                    self._labels.append(t)
                # 축 이름 — 눈금 너머 끝점에.
                pos = [0.0, 0.0, 0.0]
                pos[i] = L * 1.08
                t = gl.GLTextItem(pos=np.array(pos, dtype=float),
                                  text=names[i], color=QColor(230, 230, 230))
                self.graph_area.addItem(t)
                self._labels.append(t)
        except Exception:
            pass

    def _view_for(self, sel):
        """선택 축 조합 → (elevation, azimuth, 회전잠금). 1축=그 축이 화면 가로로 고정,
        2축=두 축이 이루는 평면을 정면으로 고정, 3축만 자유 회전(잠금 해제)."""
        sx, sy, sz = sel
        dim = sx + sy + sz
        if dim >= 3:
            return 5, -60, False            # 자유 회전
        if dim == 2:
            if sx and sy:
                return 90, -90, True         # XY 평면(top view) — X축 끝 오른쪽·Y축 끝 위
            if sx and sz:
                return 0, -90, True          # XZ 평면(front view) — X축 끝 오른쪽·Z축 끝 위
            return 0, 0, True                # YZ 평면(side view) — Y축 끝 오른쪽·Z축 끝 위
        # 1축 — 그 축을 화면 가로로
        if sx:
            return 0, -90, True              # X 가로(front)
        if sy:
            return 0, 0, True                # Y 가로(side)
        return 0, -90, True                  # Z (front; Z는 표준 카메라상 세로로 표시됨)

    def _setup_gizmo(self):
        """좌상단 reset 버튼 자리에 카메라 방향 gizmo 를 겹쳐 놓는다. 버튼을 gizmo 크기로 키워
        전 영역이 클릭(시점 초기화)되게 하고, 그 위에 마우스-투과 gizmo 를 얹는다."""
        btn = self.button_graph_reset
        g = btn.geometry()
        # 버튼 좌상단을 기준으로 정사각 영역을 잡되, 라벨이 들어갈 만큼 키운다(원래 31 → 58).
        side = 58
        x, y = g.x(), g.y()
        btn.setGeometry(x, y, side, side)        # 클릭 영역 = gizmo 전체
        self._gizmo = _AxisGizmo(self.graph_area, self.graph_area)
        self._gizmo.setGeometry(x, y, side, side)
        self._gizmo.raise_()                     # gizmo 가 위(항상 보임), 클릭은 투과해 버튼이 받음
        self._gizmo.show()
        # ⚠ 갱신은 GL 위젯의 frameSwapped(매 프레임 swap 후 발생)에 묶는다 — 드래그 회전·휠·
        #   setCameraPosition 등 카메라가 바뀌면 GL 이 반드시 재렌더하므로, 출처와 무관하게 gizmo 가
        #   '항상 화면과 동일한 카메라'로 다시 그려진다. (마우스 이벤트만 후킹하면 일부 경로에서
        #   갱신을 놓쳐 gizmo 가 옛 각도에 멈출 수 있음 — 시점이 어긋나 보이는 원인.)
        #   gizmo.update() 는 자식 위젯 repaint 일 뿐 paintGL 을 호출하지 않아 재렌더 루프 없음.
        self.graph_area.frameSwapped.connect(self._gizmo.update)

    def _setup_hover(self):
        """마우스 호버 정보 표시 준비 — 다크테마 오버레이 라벨 + 마우스 추적 + leave 후킹.
        점/선 피킹은 _update_hover 가 '화면 투영 + 최근접'으로 처리(GLViewWidget 은 3D 피킹이 없음).
        성능: 그려진 점 인덱스는 _draw_lines_and_markers 가 _hover_dirty 로만 알리고, 실제 투영
        인덱스 재구성은 호버 시 1회(_ensure_hover_index) → 재생/스크럽 핫패스엔 비용 0(lazy)."""
        self._hover_dirty = True
        self._drawn_tracks = []          # [(트랙값, 색, 그려진 idx ndarray)] — _draw_lines_and_markers 가 채움
        self._hover_pts = None           # (N,3) 그려진 점들의 정규화 좌표(투영 대상)
        self._hover_rows = None          # (N,) 각 점의 원본 행 인덱스(원본 값 조회용)
        self._hover_tid = None           # (N,) 각 점의 트랙 id(_hover_values 인덱스)
        self._hover_values = []          # tid -> 트랙 값(트랙 열 없으면 [None])
        self._hover_cols = (-1, -1, -1)  # 인덱스 만들 당시 (x,y,z) 소스 열
        self._seg_a = self._seg_b = self._seg_tid = None   # 선 피킹용 세그먼트 양끝 인덱스 + 트랙 id
        # 커서 옆 다크 오버레이 라벨(마우스 투과 → 호버 자체를 안 가림). gizmo 처럼 코드로만 생성.
        lbl = QLabel(self.graph_area)
        lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lbl.setFont(QFont("Consolas", 9))
        lbl.setStyleSheet(
            "QLabel { background-color: rgba(18,18,18,225); color: rgb(235,235,235);"
            " border: 1px solid rgb(95,95,95); border-radius: 4px; padding: 4px 7px; }")
        lbl.hide()
        self._hover_label = lbl
        # 버튼 없이도 이동 이벤트가 오게(호버) + 창 떠날 때 숨김. leaveEvent 도 인스턴스 교체(mouseMove 패턴).
        self.graph_area.setMouseTracking(True)
        self._gl_orig_leave = self.graph_area.leaveEvent
        self.graph_area.leaveEvent = self._gl_leave

    def _gl_mouse_move(self, ev):
        # 호버 정보(툴팁/링)는 모든 시야 모드에서 동작 — 잠금/회전과 무관하게 먼저 갱신.
        self._update_hover(ev)
        # 시야 잠금(1·2축) 중엔 드래그 회전/pan 을 무시하고, 3축이면 원래 GLViewWidget 동작.
        if self._view_locked:
            ev.accept()
            return
        self._gl_orig_mouse_move(ev)

    def _gl_leave(self, ev):
        # 커서가 그래프 영역을 떠나면 오버레이/링을 숨긴다.
        self._hide_hover()
        self._gl_orig_leave(ev)

    def _hide_hover(self):
        if self._hover_label.isVisible():
            self._hover_label.hide()
        if self._hover_ring.visible():      # GLGraphicsItem 은 visible()(QWidget 의 isVisible 아님)
            self._hover_ring.setVisible(False)

    # ---------- 호버 피킹 (화면 투영 + 최근접) ----------
    def _ensure_hover_index(self):
        """그려진 점들의 (정규화 좌표·원본 행·트랙 id)와 선 피킹용 세그먼트 인덱스를 캐싱(lazy 재구성).
        _draw_lines_and_markers 가 _hover_dirty 만 세우고 → 첫 호버 때 여기서 1회 만든다(재생 비용 0).
        ⚠ ghost(미도달 전체 경로)는 제외 — 호버는 '솔리드로 그린' 점/선에만 반응."""
        if not self._hover_dirty:
            return
        self._hover_dirty = False
        self._hover_pts = self._hover_rows = self._hover_tid = None
        self._seg_a = self._seg_b = self._seg_tid = None
        self._hover_values = [v for (v, _c, _i) in self._drawn_tracks]
        cx, cy, cz = self._axis_cols()
        self._hover_cols = (cx, cy, cz)
        if not self._drawn_tracks or not self.rows:
            return
        X = self._norm_axis(0, cx)
        Y = self._norm_axis(1, cy)
        Z = self._norm_axis(2, cz)
        pts_all, rows_all, tid_all = [], [], []
        seg_a, seg_b, seg_tid = [], [], []
        base = 0
        for tid, (_v, _c, idxs) in enumerate(self._drawn_tracks):
            idx = np.asarray(idxs, dtype=int)
            if idx.size == 0:
                continue
            pts = np.column_stack((X[idx], Y[idx], Z[idx]))
            mask = np.isfinite(pts).all(axis=1)       # _stack 과 동일 — 그린 점과 정확히 일치
            pts = pts[mask]
            rows = idx[mask]
            m = len(pts)
            if m == 0:
                continue
            pts_all.append(pts)
            rows_all.append(rows)
            tid_all.append(np.full(m, tid, dtype=int))
            if m >= 2:                                # 트랙 내부 연속 점쌍 = 세그먼트(트랙 경계는 안 이음)
                a = base + np.arange(m - 1)
                seg_a.append(a)
                seg_b.append(a + 1)
                seg_tid.append(np.full(m - 1, tid, dtype=int))
            base += m
        if not pts_all:
            return
        self._hover_pts = np.vstack(pts_all)
        self._hover_rows = np.concatenate(rows_all)
        self._hover_tid = np.concatenate(tid_all)
        if seg_a:
            self._seg_a = np.concatenate(seg_a)
            self._seg_b = np.concatenate(seg_b)
            self._seg_tid = np.concatenate(seg_tid)

    def _project_screen(self):
        """그려진 점들(_hover_pts)을 현재 카메라로 화면 픽셀(px,py)에 투영. valid=카메라 앞(w>0).
        compute_projection() = ndc_to_viewport × mvpMatrix(world→논리 화면픽셀, y 아래로 +). 이 행렬을
        numpy 로 한 번에 적용한다(점마다 QMatrix4x4.map 루프면 18만 점에서 프리즈)."""
        if self._hover_pts is None or len(self._hover_pts) == 0:
            return None
        try:
            tr = self._marker_label.compute_projection()
        except Exception:
            return None
        # QMatrix4x4.data() = 열우선(OpenGL) 16개 → reshape(4,4) 는 (표준행렬)^T 이므로
        # 행벡터 점들에 대해 pts_h @ data 가 곧 M·p(투영)이 된다.
        mat = np.array(tr.data(), dtype=float).reshape(4, 4)
        n = len(self._hover_pts)
        ph = np.column_stack((self._hover_pts, np.ones(n)))
        homog = ph @ mat
        w = homog[:, 3]
        valid = w > 1e-6                              # 카메라 앞에 있는 점만(뒤=음수 w)
        w_safe = np.where(valid, w, 1.0)
        px = homog[:, 0] / w_safe
        py = homog[:, 1] / w_safe
        return px, py, valid

    def _update_hover(self, ev):
        """마우스 위치에서 점/선을 피킹해 오버레이 라벨(+점이면 강조 링)을 띄운다."""
        if ev.buttons() != Qt.MouseButton.NoButton:   # 드래그(회전/팬) 중엔 호버 억제
            self._hide_hover()
            return
        self._ensure_hover_index()
        proj = self._project_screen()
        if proj is None:
            self._hide_hover()
            return
        px, py, valid = proj
        pos = ev.position()
        mx, my = pos.x(), pos.y()
        # ① 점 피킹 — 최근접 투영 점이 PICK_RADIUS_PT 이내면 좌표 툴팁 + 강조 링.
        if valid.any():
            d2 = (px - mx) ** 2 + (py - my) ** 2
            d2[~valid] = np.inf
            j = int(np.argmin(d2))
            if d2[j] <= self.PICK_RADIUS_PT ** 2:
                self._show_point_hover(j, mx, my)
                return
        # ② 선 피킹 — 점 히트 없으면 최근접 세그먼트가 PICK_RADIUS_LINE 이내면 트랙명만.
        tid = self._nearest_segment(px, py, valid, mx, my)
        if tid is not None:
            value = self._hover_values[tid] if tid < len(self._hover_values) else None
            if value is not None:                     # 트랙 열 없으면(단일 궤적) 보여줄 트랙명 없음
                self._hover_ring.setVisible(False)
                self._show_overlay(f"Track: {value or '(empty)'}", mx, my)
                return
        self._hide_hover()

    def _nearest_segment(self, px, py, valid, mx, my):
        """커서에서 가장 가까운 (양끝 모두 카메라 앞) 세그먼트의 트랙 id — PICK_RADIUS_LINE 이내면 반환,
        아니면 None. 화면 2D 점-선분 거리를 벡터화(세그먼트 전체 일괄)."""
        if self._seg_a is None:
            return None
        a, b = self._seg_a, self._seg_b
        ok = valid[a] & valid[b]
        if not ok.any():
            return None
        ax, ay = px[a][ok], py[a][ok]
        bx, by = px[b][ok], py[b][ok]
        vx, vy = bx - ax, by - ay
        wx, wy = mx - ax, my - ay
        seg2 = vx * vx + vy * vy
        t = np.where(seg2 > 0, (wx * vx + wy * vy) / np.where(seg2 > 0, seg2, 1.0), 0.0)
        t = np.clip(t, 0.0, 1.0)
        cxp = ax + t * vx
        cyp = ay + t * vy
        d2 = (mx - cxp) ** 2 + (my - cyp) ** 2
        k = int(np.argmin(d2))
        if d2[k] > self.PICK_RADIUS_LINE ** 2:
            return None
        return int(self._seg_tid[ok][k])

    def _show_point_hover(self, j, mx, my):
        """점 j 에 대한 트랙/좌표(원본값)/시간/행 툴팁 + 그 점에 강조 링."""
        pt = self._hover_pts[j]
        self._hover_ring.setData(pos=np.array([pt], dtype=float),
                                 color=self.HOVER_RING_COLOR,
                                 size=self.HOVER_RING_SIZE, pxMode=True)
        self._hover_ring.setVisible(True)
        row = int(self._hover_rows[j])
        tid = int(self._hover_tid[j])
        value = self._hover_values[tid] if tid < len(self._hover_values) else None
        cx, cy, cz = self._hover_cols
        lines = []
        lines.append(f"row: {row}")
        if value is not None:
            lines.append(f"Track: {value or '(empty)'}")
        for col in (cx, cy, cz):                       # 선택된 축의 '원본' 데이터 값(정규화 전)
            if col >= 0:
                name = str(self.headers[col]) if col < len(self.headers) else str(col)
                lines.append(f"{name}: {self._floats(col)[row]:g}")
        if self._time_active and self._time_values is not None:
            lines.append(f"t: {self._fmt_time(float(self._time_values[row]))}")
        self._show_overlay("\n".join(lines), mx, my)

    def _show_overlay(self, text, mx, my):
        """커서 옆에 오버레이 라벨을 띄우되 그래프 영역 밖으로 안 나가게 클램프."""
        lbl = self._hover_label
        lbl.setText(text)
        lbl.adjustSize()
        gw, gh = self.graph_area.width(), self.graph_area.height()
        w, h = lbl.width(), lbl.height()
        x = mx + 16
        y = my + 16
        if x + w > gw:
            x = mx - 16 - w
        if y + h > gh:
            y = my - 16 - h
        lbl.move(int(max(0, x)), int(max(0, y)))
        lbl.show()
        lbl.raise_()

    def _update_markers(self, pts, colors):
        """트랙별 '현재 위치' 마커를 각 트랙 색으로 한 번에 찍는다(GLScatterPlotItem 1개에 N점).
        pts=[(x,y,z)...], colors=[(r,g,b,a)...]. 비면 숨김."""
        if not pts:
            self._marker.setVisible(False)
            self._marker_label.setVisible(False)
            return
        self._marker.setData(pos=np.array(pts, dtype=float),
                             color=np.array(colors, dtype=float),
                             size=self.MARKER_SIZE, pxMode=True)
        self._marker.setVisible(True)
        # 다중 트랙이라 3D 좌표 라벨은 생략(현재 시각은 plainTextEdit_timestamp 으로 표시).
        self._marker_label.setVisible(False)

    # ---------- 트랙(그룹) 목록 ----------
    def _on_track_column_changed(self, *_):
        if self._loading:
            return
        self._build_track_list()
        self._rebuild_track_order()    # 트랙 그룹 바뀜 → 시간 정렬 캐시 갱신
        self._redraw()

    # ---------- 그래프 이미지 저장/복사 ----------
    def _grab_graph_image(self):
        """현재 graph_area 를 '보이는 그대로' QImage 로 캡처해 반환(실패 시 null QImage).

        ⚠ grabFramebuffer 가 잡는 건 GL 씬(궤적·점·마커·축/눈금 텍스트)뿐 — graph_area 위에
          얹힌 오버레이(방향 gizmo·호버 라벨)는 GL 이 아니라 따로 그려지므로 그 위에 합성해야
          화면과 같아진다. (시점초기화 버튼은 배경 alpha=0 로 화면상 안 보이므로 합성 제외 — grab
          시 불투명 박스로 찍히면 오히려 화면과 달라짐. gizmo 가 그 자리에 보이는 것의 전부.)
          반환 이미지가 device px(고DPI) 이므로 실제 픽셀배율을 DPR 로 박아 두면 이후 painter 가
          논리좌표(=오버레이 위젯 pos)로 그릴 수 있다."""
        area = self.graph_area
        img = area.grabFramebuffer()
        if img.isNull():
            return img
        scale = img.width() / max(1, area.width())
        img.setDevicePixelRatio(scale)
        painter = QPainter(img)
        for child in (getattr(self, "_gizmo", None), getattr(self, "_hover_label", None)):
            if child is not None and child.isVisible():
                painter.drawPixmap(child.pos(), child.grab())
        painter.end()
        return img

    def save_graph_image(self):
        """Ctrl+S — 현재 그래프 화면을 PNG 파일로 저장(파일 선택창)."""
        img = self._grab_graph_image()
        if img.isNull():
            self._show_toast("Capture failed!", success=False)
            return
        base = re.sub(r'[\\/:*?"<>|]', "_", (self._title or "graph").strip()) or "graph"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Graph Image", base + "_graph.png",
            "PNG Image (*.png);;JPEG Image (*.jpg);;All Files (*)")
        if not path:
            return
        if img.save(path):
            self._show_toast("Graph image saved!", success=True)
        else:
            self._show_toast("Save failed!", success=False)

    def copy_graph_image(self):
        """Ctrl+C — 현재 그래프 화면을 클립보드에 이미지로 복사."""
        img = self._grab_graph_image()
        if img.isNull():
            self._show_toast("Capture failed!", success=False)
            return
        QApplication.clipboard().setImage(img)
        self._show_toast("Graph image copied!", success=True)

    def _show_toast(self, text, ms=1400, success=True):
        # graph_area 오른쪽 위 구석에 잠깐 뜨는 알림(성공=초록/실패=빨강). gui_viewer._show_toast 패턴.
        bg = "rgba(54,186,101,220)" if success else "rgba(200,55,55,200)"
        self._toast.setStyleSheet(
            "QLabel { color: white; background-color: %s; border-radius: 3px;"
            " padding: 8px 14px; font-size: 13px; font-weight: 600; }" % bg)
        self._toast.setText(text)
        self._toast.adjustSize()
        tr = self.graph_area.mapTo(self, self.graph_area.rect().topRight())
        margin = 15
        x = tr.x() - self._toast.width() - margin
        y = tr.y() + margin
        self._toast.move(max(0, x), max(0, y))
        self._toast.raise_()
        self._toast.show()
        self._toast_timer.start(ms)

    def closeEvent(self, event):
        # 창을 닫으면(메인 창이 명시적으로 close) 재생 타이머도 멈춘다.
        self._pause()
        super().closeEvent(event)

    def _build_track_list(self):
        self._clear_track_list()
        self._track_rows = {}
        self._track_color = {}
        self._track_visible = {}
        self._track_cb = {}
        c = self._combo_col(self.combo_track)
        if c < 0 or not self.rows:
            return
        groups = group_indices_by(self.rows, c)
        values = sorted(groups.keys(), key=_filter_sort_key)
        if len(values) > self.MAX_TRACKS:
            # 고유값이 너무 많음 → 분리 궤적/목록 생략(단일 궤적으로 폴백) + 안내
            self._add_track_notice(
                f"{len(values)} groups — too many to split.\n"
                f"Pick a lower-cardinality column\n(≤ {self.MAX_TRACKS}).")
            return
        for i, v in enumerate(values):
            color = track_color(i)
            self._track_rows[v] = groups[v]
            self._track_color[v] = color
            self._track_visible[v] = True

            cb = QCheckBox()
            cb.setText(v if v != "" else "(empty)")
            cb.setChecked(True)
            # 폭 Ignored: 긴 텍스트가 줄을 넓혀 가로스크롤/색버튼 밀림 방지(gui_filter 와 동일)
            cb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            cb.stateChanged.connect(lambda _s, val=v: self._on_track_toggle(val))

            color_btn = QPushButton()
            color_btn.setFixedSize(QSize(16, 16))
            color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            color_btn.setToolTip("Track color")
            self._style_color_button(color_btn, QColor.fromRgbF(*color))
            color_btn.clicked.connect(
                lambda _c, val=v, b=color_btn: self._pick_track_color(val, b))

            row = _FilterItemRow(cb, color_btn)
            self.verticalLayout.addWidget(row)
            self._track_cb[v] = cb
        self.verticalLayout.addStretch(1)

    def _clear_track_list(self):
        while self.verticalLayout.count():
            item = self.verticalLayout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _add_track_notice(self, text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color: rgb(120,120,120); padding: 4px;")
        self.verticalLayout.addWidget(label)
        self.verticalLayout.addStretch(1)

    def _on_track_toggle(self, value):
        cb = self._track_cb.get(value)
        if cb is not None:
            self._track_visible[value] = cb.isChecked()
            self._redraw()

    def _pick_track_color(self, value, btn):
        initial = QColor.fromRgbF(*self._track_color.get(value, self.DEFAULT_COLOR))
        color = QColorDialog.getColor(initial, self, "Track Color")
        if color.isValid():
            self._track_color[value] = (color.redF(), color.greenF(), color.blueF(), 1.0)
            self._style_color_button(btn, color)
            self._redraw()

    def _style_color_button(self, btn, color):
        btn.setStyleSheet(
            "QPushButton { border: 1px solid rgb(120,120,120); border-radius: 8px;"
            f" background-color: {color.name()}; }}"
            "QPushButton:hover { border: 1px solid #333; }")
