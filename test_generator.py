"""
test_generator.py — 탄도탄(ballistic) 궤적 CSV 테스트 데이터 생성기.

동시간대(t=0 부터)에 여러 트랙이 각자의 궤적을 그리는 CSV 를 만든다.
각 트랙은 (발사위치, 최대고도, 탄착지점) 3가지로 정의되고,
  · 수평(x, y)은 발사 → 탄착을 등속(직선)으로 이동,
  · 수직(z)은 중력 g 하의 포물선 — 발사 고도에서 출발해 최대고도(apex)를 찍고 탄착 고도로 낙하.
총 비행시간 T 는 최대고도와 g 에서 유도된다(위치 단위=미터, g=9.8 이면 실제와 비슷한 초 단위).

출력 열 : 시간, 시간(ms), x위치, y위치, z위치, TrackNumber

CSV Viewer 3D 그래프에서 x/y/z 축 + 시간 열(시간 또는 시간(ms)) + TrackNumber 트랙 열로 재생하면
여러 탄도탄이 동시에 날아가는 자취를 볼 수 있다.

의존성 없음(표준 라이브러리만). 실행:
    & "C:\\Users\\yslee\\anaconda3\\envs\\sniff_env\\python.exe" test_generator.py
"""

import csv
import math
import os


def _flight_time(g, launch_z, apex_alt, impact_z):
    """중력 g 하에서 발사고도→apex(최대고도)→탄착고도 까지의 총 비행시간 T.
    상승 조각 + 하강 조각: T = (sqrt(2g(H-Lz)) + sqrt(2g(H-Iz))) / g."""
    up = math.sqrt(max(0.0, 2 * g * (apex_alt - launch_z)))
    down = math.sqrt(max(0.0, 2 * g * (apex_alt - impact_z)))
    return (up + down) / g


def generate_trajectory(track_number, launch, apex_alt, impact, dt=1.0, g=9.8):
    """한 트랙의 궤적 행 목록. 각 행 = (t_sec, t_ms, x, y, z, track_number).

    launch/impact : (x, y, z) 좌표 (z=고도, 지상이면 0). apex_alt : 최대고도(스칼라).
    dt            : 표본 간격(초). g : 중력가속도(위치 단위에 맞춤, 미터면 9.8).
    """
    lx, ly, lz = launch
    ix, iy, iz = impact
    apex_alt = max(apex_alt, lz, iz)          # apex 는 발사/탄착 고도보다 낮을 수 없음

    vz0 = math.sqrt(max(0.0, 2 * g * (apex_alt - lz)))   # 초기 수직 속도
    T = _flight_time(g, lz, apex_alt, iz)
    if T <= 0:                                # 발사=탄착=apex 인 퇴화 케이스 방어
        T = dt

    def sample(t):
        s = t / T                             # 수평 진행 비율(등속)
        x = lx + (ix - lx) * s
        y = ly + (iy - ly) * s
        z = lz + vz0 * t - 0.5 * g * t * t    # 중력 포물선 (t=T 이면 z=탄착고도)
        return (t, int(round(t * 1000)), x, y, z, track_number)

    n = int(math.floor(T / dt))
    rows = [sample(k * dt) for k in range(n + 1)]
    if n * dt < T - 1e-9:                      # 마지막 표본이 탄착보다 이르면 탄착점을 정확히 추가
        rows.append(sample(T))
    return rows


def _format_clock(seconds):
    """경과 초(float) → 'HH:MM:SS.mmm'."""
    ms_total = int(round(seconds * 1000))
    ms = ms_total % 1000
    s = (ms_total // 1000) % 60
    m = (ms_total // 60000) % 60
    h = ms_total // 3600000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def write_csv(tracks, path, dt=1.0, g=9.8, base_clock=0.0):
    """tracks = [(track_number, launch(x,y,z), apex_alt, impact(x,y,z)), ...] → CSV 저장.

    모든 트랙을 t=0 부터 동시에 표본화하고 시간 오름차순(같으면 TrackNumber 순)으로 정렬해 기록.
    base_clock : '시간' 열의 시작 경과초(0 이면 00:00:00.000 부터). 반환 = 기록한 행 수.
    """
    all_rows = []
    for track_number, launch, apex_alt, impact in tracks:
        all_rows.extend(
            generate_trajectory(track_number, launch, apex_alt, impact, dt=dt, g=g))

    # (시간ms, TrackNumber). TrackNumber 는 숫자·문자('FA001') 혼용 가능하므로 str 로 비교
    all_rows.sort(key=lambda r: (r[1], str(r[5])))

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:   # loader = utf-8-sig 우선
        w = csv.writer(f)
        w.writerow(["시간", "시간(ms)", "x위치", "y위치", "z위치", "TrackNumber"])
        for t, t_ms, x, y, z, track_number in all_rows:
            w.writerow([
                _format_clock(base_clock + t),
                t_ms,
                f"{x:.3f}", f"{y:.3f}", f"{z:.3f}",
                track_number,
            ])
    return len(all_rows)


if __name__ == "__main__":
    # ── 여기에 트랙을 원하는 만큼 추가 ──────────────────────────────────
    # (TrackNumber, 발사위치(x, y, z), 최대고도, 탄착지점(x, y, z))
    #   ※ TrackNumber 는 숫자(1)·문자("FA001") 아무거나 가능.
    #   ※ z = 고도(지상은 0), 단위 = 미터. 최대고도는 apex 높이(스칼라).
    TRACKS = [
        ("FA001", (      0,       0, 0), 120_000, ( 300_000, 300_000, 0)),
        ("FA002", (-40_000,  40_000, 0), 130_000, ( 300_000, 300_000, 0)),
        ("FA003", (-30_000,  30_000, 0), 150_000, ( 300_000, 300_000, 0)),
        ("FA004", (-20_000,  20_000, 0), 120_000, ( 300_000, 300_000, 0)),
        ("FA005", (-10_000,  10_000, 0), 130_000, ( 300_000, 300_000, 0)),
        ("FA006", ( 10_000, -10_000, 0), 150_000, ( 300_000, 300_000, 0)),
        ("FA007", ( 20_000, -20_000, 0), 120_000, ( 300_000, 300_000, 0)),
        ("FA008", ( 30_000, -30_000, 0), 130_000, ( 300_000, 300_000, 0)),
        ("FA009", ( 40_000, -40_000, 0), 150_000, ( 300_000, 300_000, 0)),
    ]

    DT_SEC = 1.0      # 표본 간격(초). 점을 촘촘히 원하면 줄이기(예: 0.5)
    GRAVITY = 9.8     # 중력가속도(m/s²) — 비행시간이 최대고도로부터 유도됨

    _here = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(_here, "CSV", "test_ballistic", "ballistic_test.csv")
    n = write_csv(TRACKS, out_path, dt=DT_SEC, g=GRAVITY)
    print(f"wrote {n} rows ({len(TRACKS)} tracks) -> {out_path}")
