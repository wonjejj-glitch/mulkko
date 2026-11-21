"""Manning 식 기반 간이 수리검토 (만관 통수능, 통수능비, 필요관경).

SWMM 해석 결과가 있으면 조서의 `통수능부족`/`통수능비`/`필요관경` 열이 우선 적용되고,
설계유량만 있는 경우 본 모듈이 개략 검토를 수행한다(정류·원형관·만관 기준).
"""
from __future__ import annotations

import math

from common.models import Pipe


def manning_n(cfg: dict, pipe: Pipe) -> float:
    if pipe.roughness_n:
        return pipe.roughness_n
    table = cfg["manning_n"]
    mat = (pipe.material or "").replace(" ", "")
    for key, val in table.items():
        if key != "default" and key in mat:
            return float(val)
    return float(table["default"])


def full_capacity_lps(diameter_mm: float, slope: float, n: float) -> float:
    """원형관 만관 통수능(L/s) = (1/n)·A·R^(2/3)·S^(1/2)."""
    if slope <= 0 or n <= 0:
        return 0.0
    D = diameter_mm / 1000.0
    area = math.pi * D ** 2 / 4
    radius = D / 4
    return (1.0 / n) * area * radius ** (2 / 3) * math.sqrt(slope) * 1000.0


LPS_TO_CMD = 86.4   # 1 L/s = 86.4 ㎥/일


def full_velocity(diameter_mm: float, slope: float, n: float) -> float:
    """원형관 만관 유속(m/s) = (1/n)·R^(2/3)·S^(1/2)."""
    if slope <= 0 or n <= 0:
        return 0.0
    return (1.0 / n) * (diameter_mm / 4000.0) ** (2 / 3) * math.sqrt(slope)


def flow_velocity(diameter_mm: float, slope: float, n: float, flow_lps: float) -> float:
    """설계유량이 흐를 때의 평균유속(m/s). 원형관 부분수심 Manning 식(등류)으로 수심을 찾아 V=Q/A.

    만관 최대유량(수심 약 94%)을 넘으면 만관 단면으로 나눈 값(압력류 근사).
    """
    if slope <= 0 or n <= 0 or diameter_mm <= 0 or not flow_lps or flow_lps <= 0:
        return 0.0
    D = diameter_mm / 1000.0
    Q = flow_lps / 1000.0

    def q_of(theta: float) -> tuple[float, float]:
        area = D * D / 8.0 * (theta - math.sin(theta))
        r = area / (D * theta / 2.0)
        return (1.0 / n) * area * r ** (2 / 3) * math.sqrt(slope), area

    t_max = 5.278                       # 유량 최대 수심(≈0.938D)의 중심각
    q_max, _ = q_of(t_max)
    if Q >= q_max:
        return Q / (math.pi * D * D / 4.0)
    lo, hi = 1e-6, t_max
    for _ in range(60):
        mid = (lo + hi) / 2
        if q_of(mid)[0] < Q:
            lo = mid
        else:
            hi = mid
    _, area = q_of(hi)
    return Q / area if area else 0.0


def velocity_check(cfg: dict, pipe_use: str | None, v: float) -> tuple[str, str]:
    """(판정, 조치 안내). KDS 61 40 00:2022 1.3 유속 기준(오수 0.6~3.0, 우수·합류 0.8~3.0 m/s)."""
    c = cfg["criteria"]
    use = pipe_use or c.get("default_pipe_use", "오수")
    vmin, vmax = c.get("velocity_limits", {}).get(use, [0.6, 3.0])
    if v < vmin:
        return (f"최소유속 미달({use} {vmin} m/s 이상)",
                "퇴적·악취 우려 → 준설 주기 단축, 플러싱·복단면·오수 전용관 병렬부설 등 최소유속 확보 대책 검토")
    if v > vmax:
        return (f"최대유속 초과({use} {vmax} m/s 이하)", "관 마모·맨홀 손상 우려 → 단차·감세공 설치 또는 관경·경사 조정 검토")
    return f"적정({use} {vmin}~{vmax} m/s)", ""


def required_diameter_mm(cfg: dict, flow_lps: float, slope: float, n: float, limit_ratio: float) -> int | None:
    """설계유량을 통수할 수 있는 최소 표준관경(없으면 None)."""
    for d in sorted(cfg["standard_diameters_mm"]):
        if full_capacity_lps(d, slope, n) * limit_ratio >= flow_lps:
            return int(d)
    return None


def pipe_slope(pipe: Pipe) -> float | None:
    """관저고 차이로 경사 산정. 지반고·관저고가 모두 없으면 None."""
    if pipe.from_invert_el is None or pipe.to_invert_el is None or pipe.plan_length_m <= 0:
        return None
    return (pipe.from_invert_el - pipe.to_invert_el) / pipe.plan_length_m


def apply_hydraulics(pipe: Pipe, cfg: dict) -> None:
    """설계유량이 있으면 통수능비를 계산하고 통수능 부족 여부·필요관경을 채운다.

    조서에 통수능비가 이미 있으면(SWMM 결과 등) 덮어쓰지 않는다.
    """
    pipe.warnings = [w for w in pipe.warnings if not w.startswith("수리검토")]
    if not pipe.design_flow_lps:
        return
    c = cfg["criteria"]
    slope = pipe_slope(pipe)
    if slope is None:
        pipe.warnings.append("수리검토 생략: 관저고(시·종점) 미입력으로 경사를 알 수 없음")
        return
    if slope < 0:
        pipe.warnings.append(f"수리검토 생략: 역경사({slope * 1000:.1f}‰) 관로")
        return
    min_slope = float(c.get("min_slope", 0.0005))
    eff = max(slope, min_slope)
    n = manning_n(cfg, pipe)
    limit = float(c.get("capacity_limit_ratio", 1.0))
    pipe.slope = slope
    pipe.full_capacity_lps = full_capacity_lps(pipe.diameter_mm, eff, n)
    ratio = pipe.design_flow_lps / (pipe.full_capacity_lps * limit) if pipe.full_capacity_lps else 99.0
    if pipe.capacity_ratio is None:
        pipe.capacity_ratio = round(ratio, 3)
    if ratio > 1.0:
        pipe.capacity_insufficient = True
        if not pipe.required_diameter_mm:
            req = required_diameter_mm(cfg, pipe.design_flow_lps, eff, n, limit)
            if req is None:
                pipe.warnings.append("수리검토: 표준관경 최대 규격으로도 통수능 부족 - 경사 상향·관로 분할 검토 필요")
            elif req > pipe.diameter_mm:
                pipe.required_diameter_mm = req
    if slope < min_slope:
        pipe.warnings.append(f"수리검토: 관경사 {slope * 1000:.2f}‰가 최소경사 {min_slope * 1000:.2f}‰ 미만 - 최소경사 적용")
