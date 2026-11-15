"""정비 후 관로의 수리 제원(내경·조도계수)과 정비 전후 통수능 비교 (개략)."""
from __future__ import annotations

from common.hydraulics import full_capacity_lps, manning_n, pipe_slope
from common.models import M_CIPP, M_PARTIAL_DIG, M_REPLACE, M_UPSIZE, Pipe, PipeDecision


def post_rehab_properties(pipe: Pipe, dec: PipeDecision, cfg: dict) -> tuple[float, float, bool]:
    """(정비 후 내경 mm, 정비 후 조도계수, 변경 여부).

    굴착교체: 신관 조도계수(증경은 신설 관경). 비굴착 전체보수: 라이너 두께만큼 내경 감소+CIPP 조도계수.
    부분굴착+전체보수: 구간 길이 가중 조도계수(내경은 라이너 두께 환산 감소). 그 외는 변경 없음.
    """
    h = cfg["rehab_hydraulics"]
    D = float(pipe.diameter_mm)
    n0 = manning_n(cfg, pipe)
    L = pipe.plan_length_m or 1.0

    def liner(d: float) -> float:
        return max(float(h["cipp_thickness_min_mm"]), d * float(h["cipp_thickness_ratio"]))

    if dec.method == M_UPSIZE:
        return float(dec.new_diameter_mm or D), float(h["new_pipe_n"]), True
    if dec.method == M_REPLACE:
        return D, float(h["new_pipe_n"]), True
    if dec.method == M_CIPP:
        return D - 2 * liner(D), float(h["cipp_n"]), True
    if dec.method == M_PARTIAL_DIG:
        dig = min(sum(c.length_m for c in dec.dig_clusters), L)
        if dec.sub_method == M_CIPP:
            rest = L - dig
            n_eq = (dig * float(h["new_pipe_n"]) + rest * float(h["cipp_n"])) / L
            return D - 2 * liner(D) * rest / L, n_eq, True
        n_eq = (dig * float(h["new_pipe_n"]) + (L - dig) * n0) / L
        return D, n_eq, dig > 0
    return D, n0, False


def capacity_before_after(pipe: Pipe, dec: PipeDecision, cfg: dict) -> dict | None:
    """정비 전후 만관 통수능. 관저경사를 알 수 없으면 None."""
    slope = pipe_slope(pipe)
    if slope is None or slope <= 0:
        return None
    slope = max(slope, float(cfg["criteria"].get("min_slope", 0.0005)))
    d1, n1, changed = post_rehab_properties(pipe, dec, cfg)
    q0 = full_capacity_lps(pipe.diameter_mm, slope, manning_n(cfg, pipe))
    q1 = full_capacity_lps(d1, slope, n1)
    return {"d0": pipe.diameter_mm, "n0": manning_n(cfg, pipe), "q0": q0, "d1": d1, "n1": n1, "q1": q1,
            "change_pct": (q1 / q0 - 1) * 100 if q0 else 0.0, "changed": changed}
