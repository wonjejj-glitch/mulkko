"""정비공법별 공종 수량 산출(굴착·비굴착·부대공)."""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from common.config import price_by_diameter
from common.models import (M_CIPP, M_CLEAN, M_NONE, M_PARTIAL_DIG, M_PATCH, M_REPLACE, M_UPSIZE, Pipe,
                           PipeDecision, QuantityItem)
from quantity_takeoff.costs import excavation_items, partial_repair_unit_cost


def _nondig_ancillary(cfg: dict, pipe: Pipe, method: str, length: float, n_lateral: int) -> list[QuantityItem]:
    """비굴착 보수 필수 부대공: 준설·세정·시공 전/후 CCTV·물돌리기·돌출관 절단."""
    up = cfg["unit_prices"]
    D, pid, L = pipe.diameter_mm, pipe.pipe_id, round(length, 2)
    items = [
        QuantityItem(pid, method, "부대공", "관로 준설", f"D{D}", "m", L, price_by_diameter(up["dredging_per_m"], D)),
        QuantityItem(pid, method, "부대공", "고압 세정", f"D{D}", "m", L, price_by_diameter(up["cleaning_per_m"], D)),
        QuantityItem(pid, method, "부대공", "CCTV 촬영", "시공 전·후 2회", "m", round(L * 2, 2), up["cctv_per_m"]),
        QuantityItem(pid, method, "부대공", "물돌리기", f"D{D} 우회펌핑", "구간", 1, price_by_diameter(up["bypass_per_span"], D)),
    ]
    if n_lateral:
        items.append(QuantityItem(pid, method, "부대공", "돌출관 절단", "로봇 커터", "개소", n_lateral, up["lateral_cut_per_site"]))
    return items


def _patch_items(cfg: dict, pipe: Pipe, method: str, decision: PipeDecision) -> list[QuantityItem]:
    counts: dict[float, int] = defaultdict(int)
    for c in decision.patch_clusters:
        counts[c.unit_size_m] += c.n_sites
    return [
        QuantityItem(pipe.pipe_id, method, "비굴착보수공", "비굴착 부분보수", f"D{pipe.diameter_mm} L={size:.1f}m", "개소", n,
                     partial_repair_unit_cost(cfg, pipe.diameter_mm, size))
        for size, n in sorted(counts.items())
    ]


def takeoff_pipe(cfg: dict, pipe: Pipe, decision: PipeDecision) -> list[QuantityItem]:
    m = decision.method
    L = pipe.plan_length_m
    up = cfg["unit_prices"]
    D = pipe.diameter_mm
    items: list[QuantityItem] = []

    if m in (M_UPSIZE, M_REPLACE):
        new_d = decision.new_diameter_mm or D
        items += excavation_items(cfg, pipe, m, L, new_d, label="굴착교체")
        items.append(QuantityItem(pipe.pipe_id, m, "부대공", "물돌리기", f"D{D} 우회펌핑", "구간", 1,
                                  price_by_diameter(up["bypass_per_span"], D)))
        items.append(QuantityItem(pipe.pipe_id, m, "부대공", "CCTV 촬영", "준공 검사 1회", "m", round(L, 2), up["cctv_per_m"]))

    elif m == M_PARTIAL_DIG:
        dig_len = 0.0
        for c in decision.dig_clusters:
            items += excavation_items(cfg, pipe, m, c.length_m, D, label=f"부분굴착#{c.cluster_no}")
            dig_len += c.length_m
        if decision.sub_method == M_CIPP:
            rem = max(L - dig_len, 0.0)
            items.append(QuantityItem(pipe.pipe_id, m, "비굴착보수공", "비굴착 전체보수", f"D{D} (굴착구간 제외)", "m",
                                      round(rem, 2), price_by_diameter(up["cipp_full_per_m"], D)))
        elif decision.sub_method == M_PATCH:
            items += _patch_items(cfg, pipe, m, decision)
        items += _nondig_ancillary(cfg, pipe, m, L, decision.n_lateral_cut)

    elif m == M_CIPP:
        items.append(QuantityItem(pipe.pipe_id, m, "비굴착보수공", "비굴착 전체보수", f"D{D}", "m", round(L, 2),
                                  price_by_diameter(up["cipp_full_per_m"], D)))
        items += _nondig_ancillary(cfg, pipe, m, L, decision.n_lateral_cut)

    elif m == M_PATCH:
        items += _patch_items(cfg, pipe, m, decision)
        items += _nondig_ancillary(cfg, pipe, m, L, decision.n_lateral_cut)

    elif m == M_CLEAN:
        items += [
            QuantityItem(pipe.pipe_id, m, "부대공", "관로 준설", f"D{D}", "m", round(L, 2), price_by_diameter(up["dredging_per_m"], D)),
            QuantityItem(pipe.pipe_id, m, "부대공", "고압 세정", f"D{D}", "m", round(L, 2), price_by_diameter(up["cleaning_per_m"], D)),
            QuantityItem(pipe.pipe_id, m, "부대공", "CCTV 촬영", "준설 후 확인 1회", "m", round(L, 2), up["cctv_per_m"]),
        ]
        if decision.n_lateral_cut:
            items.append(QuantityItem(pipe.pipe_id, m, "부대공", "돌출관 절단", "로봇 커터", "개소", decision.n_lateral_cut,
                                      up["lateral_cut_per_site"]))
    elif m == M_NONE:
        pass
    return [i for i in items if i.qty > 0]


def quantities_frame(items: list[QuantityItem]) -> pd.DataFrame:
    return pd.DataFrame([{
        "관번": q.pipe_id, "정비공법": q.method, "공종구분": q.group, "항목": q.item, "규격": q.spec,
        "단위": q.unit, "수량": q.qty, "단가(원)": q.unit_price, "금액(원)": q.amount,
    } for q in items], columns=["관번", "정비공법", "공종구분", "항목", "규격", "단위", "수량", "단가(원)", "금액(원)"])


def summarize_quantities(items: list[QuantityItem]) -> pd.DataFrame:
    """공종·항목·규격별 수량/금액 집계(총괄 수량표). 규격의 [구간라벨] 접두는 제거하여 합산."""
    df = quantities_frame(items)
    if df.empty:
        return df
    df["규격"] = df["규격"].str.replace(r"^\[[^\]]*\]\s*", "", regex=True)
    # 굴착 단면 규격은 구간마다 달라지므로 굴착방식만 남겨 집계
    df["규격"] = df["규격"].str.replace(r"\s*H=.*$", "", regex=True)
    g = (df.groupby(["공종구분", "항목", "규격", "단위"], as_index=False)
           .agg(수량=("수량", "sum"), 금액=("금액(원)", "sum")))
    g["평균단가(원)"] = (g["금액"] / g["수량"]).round(0)
    order = {"토공": 0, "가시설공": 1, "관부설공": 2, "포장공": 3, "비굴착보수공": 4, "부대공": 5}
    g["_o"] = g["공종구분"].map(order).fillna(9)
    g = g.sort_values(["_o", "항목", "규격"]).drop(columns="_o").rename(columns={"금액": "금액(원)"})
    g["수량"] = g["수량"].round(2)
    return g[["공종구분", "항목", "규격", "단위", "수량", "평균단가(원)", "금액(원)"]].reset_index(drop=True)
