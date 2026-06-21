"""편집 히스토리(Undo/Redo) — CSV별 독립 스택.

ViewerWindow가 각 CSV cache 엔트리마다 EditHistory 1개를 두고(상한 cap=20단계),
사용자 액션이 끝날 때마다 경량 스냅샷(Memento)을 push한다. 스냅샷은 .viewer 영속화와
같은 평면 dict(export_highlights/export_state 결과)와 열너비 리스트를 담는다 → 셀 단위
기록 없이 '액션 단위'로 되돌린다.

⚠ 이 클래스는 '순수 저장소'다. 모델에서 무엇을 추출할지, 어떤 슬라이스가 안 바뀌어
참조를 공유(COW)할지는 ViewerWindow.record_history/_make_memento 가 판단한다. 여기서는
push/undo/redo 와 상한 관리만 한다.
"""
from collections import namedtuple

# 한 시점의 '되돌릴 수 있는 상태' = 4 슬라이스(각각 .viewer 직렬화 형식 또는 뷰 기하).
#  - highlights: CSVTableModel.export_highlights()  → {색문자열: {열: [행, ...]}}
#  - fd:         CSVFilterProxyModel.export_state()  → {column_filters, deltas}
#  - widths:     [열너비, ...] (그 시점 열 수와 정합)
#  - rows:       [행높이, ...] (그 시점 보이는 행 수와 정합) 또는 None(전부 기본 20 = sentinel)
# ⚠ 네 값은 '불변 스냅샷'으로 취급한다(복원은 read-only, export 는 매번 새 객체). 안 바뀐
#   슬라이스는 직전 Memento 의 객체를 그대로 참조 공유해 메모리를 아낀다(COW) → 절대 제자리 수정 금지.
# ⚠ rows 는 .viewer 영속화 대상이 아니다(세션+Undo 한정) — 열너비와 달리 저장/CSV전환 복원은 안 함.
Memento = namedtuple("Memento", "highlights fd widths rows")


class EditHistory:
    """단일 CSV 의 Undo/Redo 스택. baseline(최초 상태) 위에 최대 cap 단계까지 쌓는다.

    _stack[_pos] = 현재 상태. baseline 은 항상 _stack[0](되돌릴 수 있는 바닥, 액션으로 안 셈).
    스택 길이 상한 = cap + 1 (baseline 1 + 액션 cap).
    """

    def __init__(self, baseline, cap=20):
        self._stack = [baseline]
        self._pos = 0
        self._cap = cap

    def push(self, memento):
        """새 액션 결과를 쌓는다. 현재 위치보다 뒤(redo 가지)는 버리고, 상한 초과 시 가장 오래된 것 폐기."""
        del self._stack[self._pos + 1:]          # 새 액션 → 기존 redo 가지 무효화
        self._stack.append(memento)
        self._pos += 1
        if len(self._stack) > self._cap + 1:     # 상한 초과 → 가장 오래된 액션(인덱스 1) 폐기
            self._stack.pop(0)
            self._pos -= 1

    def undo(self):
        """한 단계 뒤 상태로 이동해 그 Memento 반환(바닥이면 None — 변화 없음)."""
        if self._pos <= 0:
            return None
        self._pos -= 1
        return self._stack[self._pos]

    def redo(self):
        """한 단계 앞 상태로 이동해 그 Memento 반환(맨 앞이면 None)."""
        if self._pos >= len(self._stack) - 1:
            return None
        self._pos += 1
        return self._stack[self._pos]

    def current(self):
        """현재 상태(COW 시 안 바뀐 슬라이스의 참조를 재사용하기 위해 조회)."""
        return self._stack[self._pos]

    def can_undo(self):
        return self._pos > 0

    def can_redo(self):
        return self._pos < len(self._stack) - 1
