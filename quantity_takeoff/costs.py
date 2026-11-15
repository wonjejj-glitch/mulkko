"""공종별 단가 적용 및 굴착 단면 계산(개략)."""
from __future__ import annotations

import math
from dataclasses import dataclass

from common.config import price_by_diameter
from common.models import Pipe, QuantityItem


def partial_repair_unit_cost(cfg: dict, diameter_mm: float, size_m: float) -> float:
    """부분보수 1개소 단가 = 관경별 기준단가(1.0m) × 규격 보정계수."""
    base = price_by_diameter(cfg["unit_prices"]["partial_repair_per_site"], diameter_mm)
    factors = {float(k): float(v) for k, v in cfg["criteria"]["partial_repair_size_factor"].items()}
    if size_m in factors:
        f = factors[size_m]
    else:  # 표에 없는 규격은 선형 보간
        ks = sorted(factors)
        lo = max([k for k in ks if k <= size_m], default=ks[0])
        hi = min([k for k in ks if k >= size_m], default=ks[-1])
        f = factors[lo] if hi == lo else factors[lo] + (factors[hi] - factors[lo]) * (size_m - lo) / (hi - lo)
    return base * f


def full_cipp_cost(cfg: dict, diameter_mm: float, length_m: float) -> float:
    return price_by_diameter(cfg["unit_prices"]["cipp_full_per_m"], diameter_mm) * length_m


@dataclass
class TrenchSection:
    method: str              # 사면굴착 | 간이흙막이 | 강재토류판
    depth_m: float           # 굴착깊이 H
    bottom_width_m: float    # 바닥폭 B
    top_width_m: float       # 상단폭
    area_m2: float           # 굴착 단면적
    sand_area_m2: float      # 모래기초 단면적(관 단면 제외)
    pipe_area_m2: float      # 관 외경 단면적
    shoring: str | None      # 가시설 종류
    pave_width_m: float      # 포장 깨기/복구 폭


def trench_section(cfg: dict, diameter_mm: float, avg_depth_m: float) -> TrenchSection:
    """관경 D, 평균 관저심도 H에 따른 굴착 단면.

    H < 2.5m : 사면굴착 (경사 1:n)
    2.5 ≤ H < 4.0m : 간이흙막이 (수직굴착)
    H ≥ 4.0m : 강재토류판 (수직굴착)
    """
    ex = cfg["excavation"]
    D = diameter_mm / 1000.0
    Do = D * float(ex["outer_diameter_factor"])
    t_found = float(ex["foundation_thickness_m"])
    H = avg_depth_m + t_found
    pipe_area = math.pi * Do ** 2 / 4
    if H < float(ex["slope_cut_max_depth_m"]):
        B = max(float(ex["min_bottom_width_m"]), Do + 2 * float(ex["work_space_each_side_m"]))
        n = float(ex["slope_ratio"])
        top = B + 2 * n * H
        area = (B + n * H) * H
        method, shoring = "사면굴착", None
        sand_h = t_found + Do + float(ex["sand_cover_above_crown_m"])
        sand_area = (B + n * sand_h) * sand_h - pipe_area
    else:
        B = max(float(ex["min_bottom_width_m"]), Do + 2 * float(ex["shoring_work_space_each_side_m"]))
        top = B
        area = B * H
        if H < float(ex["simple_shoring_max_depth_m"]):
            method, shoring = "간이흙막이", "간이흙막이"
        else:
            method, shoring = "강재토류판", "강재토류판"
        sand_h = t_found + Do + float(ex["sand_cover_above_crown_m"])
        sand_area = B * sand_h - pipe_area
    return TrenchSection(
        method=method, depth_m=round(H, 3), bottom_width_m=round(B, 3), top_width_m=round(top, 3),
        area_m2=area, sand_area_m2=max(sand_area, 0.0), pipe_area_m2=pipe_area, shoring=shoring,
        pave_width_m=top + 2 * float(ex["pavement_extra_width_m"]),
    )


def excavation_items(cfg: dict, pipe: Pipe, method: str, length_m: float, new_diameter_mm: int,
                     label: str = "굴착교체") -> list[QuantityItem]:
    """굴착 구간(전체 또는 부분굴착) 수량 산출."""
    up = cfg["unit_prices"]
    ex = cfg["excavation"]
    # 증경 시 신설관 기준으로 단면 산정(굴착폭 확대), 심도는 기존 관저 기준 + 증가 관경
    depth = pipe.avg_depth_m + max(0, new_diameter_mm - pipe.diameter_mm) / 1000.0
    sec = trench_section(cfg, new_diameter_mm, depth)
    L = length_m
    pid = pipe.pipe_id
    v_exc = sec.area_m2 * L
    v_sand = sec.sand_area_m2 * L
    v_pipe = sec.pipe_area_m2 * L
    v_back = max(v_exc - v_sand - v_pipe, 0.0)
    v_surplus = (v_exc - v_back) * float(ex["soil_bulking_factor"])
    pav = pipe.pavement if pipe.pavement in up["pavement_break_per_m2"] else ex.get("default_pavement", "아스콘")
    pav_area = sec.pave_width_m * L
    spec_sec = f"{sec.method} H={sec.depth_m:.2f}m B={sec.bottom_width_m:.2f}m"
    items = [
        QuantityItem(pid, method, "포장공", "포장깨기", f"{pav}", "m2", round(pav_area, 2), up["pavement_break_per_m2"][pav]),
        QuantityItem(pid, method, "토공", "터파기", spec_sec, "m3", round(v_exc, 2), up["excavation_per_m3"]),
        QuantityItem(pid, method, "관부설공", "기존관 철거", f"D{pipe.diameter_mm}", "m", round(L, 2),
                     price_by_diameter(up["pipe_removal_per_m"], pipe.diameter_mm)),
        QuantityItem(pid, method, "관부설공", "모래기초", f"관상단+{ex['sand_cover_above_crown_m']}m", "m3", round(v_sand, 2),
                     up["sand_foundation_per_m3"]),
        QuantityItem(pid, method, "관부설공", "관 부설(자재포함)", f"D{new_diameter_mm}", "m", round(L, 2),
                     price_by_diameter(up["pipe_install_per_m"], new_diameter_mm)),
        QuantityItem(pid, method, "토공", "되메우기", "양질토", "m3", round(v_back, 2), up["backfill_per_m3"]),
        QuantityItem(pid, method, "토공", "잔토처리", f"토량환산 {ex['soil_bulking_factor']}", "m3", round(v_surplus, 2),
                     up["surplus_soil_per_m3"]),
        QuantityItem(pid, method, "포장공", "포장복구", f"{pav}", "m2", round(pav_area, 2), up["pavement_restore_per_m2"][pav]),
    ]
    if sec.shoring:
        price = up["simple_shoring_per_m2"] if sec.shoring == "간이흙막이" else up["steel_sheathing_per_m2"]
        items.append(QuantityItem(pid, method, "가시설공", sec.shoring, f"양측 H={sec.depth_m:.2f}m", "m2",
                                  round(2 * sec.depth_m * L, 2), price))
    for it in items:
        it.spec = f"[{label}] {it.spec}"
    return [i for i in items if i.qty > 0]


def excavation_cost(cfg: dict, pipe: Pipe, length_m: float, new_diameter_mm: int) -> float:
    return sum(i.amount for i in excavation_items(cfg, pipe, "-", length_m, new_diameter_mm))
