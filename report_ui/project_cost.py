"""총사업비 산정 (노후하수관로 정비사업 재정계획 '소요사업비' 표 구성).

실제 사업 설계보고서 제7장 재정계획 <표 7.1-1> 구성을 따른다.
  총사업비 = Ⅰ. 총공사비 + Ⅱ. 시설부대경비
  Ⅰ. 총공사비  = 1. 도급공사비(공법별 + 맨홀보수 + 관급자재대) + 2. 폐기물처리 + 3. CCTV조사 및 수밀시험 + 4. GIS DB 구축
  Ⅱ. 시설부대경비 = 1. 설계비(실제 계약금액) + 2. 감리비(요율) + 3. 시설부대비(요율)
감리비·시설부대비 요율은 「하수도분야 보조금 편성 및 집행관리 실무요령(2025.1, 환경부)」 기준을 사업마다 입력한다.
"""
from __future__ import annotations

import pandas as pd

from common.models import ProjectResult
from report_ui import tables as T

# 기본값: 실제 사업 사례(도급공사비 대비 비율)를 참고한 추정치. 사업마다 실제 금액으로 바꿔 쓴다.
DEFAULTS = {
    "waste_ratio": 0.0153,          # 폐기물처리 / 도급공사비
    "cctv_ratio": 0.0068,           # CCTV조사 및 수밀시험 / 도급공사비
    "gis_ratio": 0.0104,            # GIS DB 구축 / 도급공사비
    "design_ratio": 0.0714,         # 설계비 / 총공사비 (실제 계약금액이 있으면 그 값)
    "supervision_rate": 0.1052,     # 감리비 요율 (관급자재 제외 도급공사비 기준)
    "facility_rate": 0.0024,        # 시설부대비 요율 (관급자재 제외 도급공사비 기준)
}


def project_cost_table(res: ProjectResult, contract_total: float, manhole_direct: float = 0.0, gwangeup: float = 0.0,
                       waste: float | None = None, cctv: float | None = None, gis: float | None = None,
                       design_fee: float | None = None, opts: dict | None = None) -> pd.DataFrame:
    """총사업비 표(원). contract_total = 원가계산서 총공사비(도급, 관로 공종).

    공법별 금액은 직접공사비 비율로 도급 총액을 배분한다. 맨홀보수 직접비는 같은 비율(도급/직접)로 환산해 더한다.
    waste·cctv·gis·design_fee 를 주지 않으면 DEFAULTS 비율로 추정한다.
    """
    o = {**DEFAULTS, **(opts or {})}
    s = T.summary_by_method(res)
    body = s[(s["정비공법"] != "합계") & (s["공사비(원)"] > 0)]
    direct = float(body["공사비(원)"].sum())
    k = contract_total / direct if direct else 1.0               # 도급/직접공사비 환산계수(간접비·이윤 등)
    rows = [(f"    1.{i} {r['정비공법']}", r["공사비(원)"] * k, "") for i, (_, r) in enumerate(body.iterrows(), 1)]
    n = len(rows)
    if manhole_direct:
        n += 1
        rows.append((f"    1.{n} 맨홀보수", manhole_direct * k, "맨홀 탭 결과, 같은 환산계수 적용"))
    if gwangeup:
        n += 1
        rows.append((f"    1.{n} 관급자재대", gwangeup, "입력값"))
    dogeup = sum(v for _, v, _ in rows)
    dogeup_excl = dogeup - gwangeup
    waste = dogeup * o["waste_ratio"] if waste is None else waste
    cctv = dogeup * o["cctv_ratio"] if cctv is None else cctv
    gis = dogeup * o["gis_ratio"] if gis is None else gis
    construction = dogeup + waste + cctv + gis
    design_est = design_fee is None
    design_fee = construction * o["design_ratio"] if design_est else design_fee
    supervision = dogeup_excl * o["supervision_rate"]
    facility = dogeup_excl * o["facility_rate"]
    overhead = design_fee + supervision + facility
    total = construction + overhead
    out = [("총 사업비", total, ""), ("Ⅰ. 총 공사비", construction, ""), ("  1. 도급 공사비", dogeup, f"원가계산 반영(직접공사비×{k:.3f})")]
    out += rows
    out += [("  2. 폐기물처리", waste, "추정(도급 대비 %.2f%%)" % (o["waste_ratio"] * 100) if waste == dogeup * o["waste_ratio"] else "입력값"),
            ("  3. CCTV조사 및 수밀시험", cctv, "추정(도급 대비 %.2f%%)" % (o["cctv_ratio"] * 100) if cctv == dogeup * o["cctv_ratio"] else "입력값"),
            ("  4. GIS DB 구축", gis, "추정(도급 대비 %.2f%%)" % (o["gis_ratio"] * 100) if gis == dogeup * o["gis_ratio"] else "입력값"),
            ("Ⅱ. 시설부대경비", overhead, ""),
            ("  1. 설계비", design_fee, "추정(총공사비 대비 %.2f%%)" % (o["design_ratio"] * 100) if design_est else "실제 계약금액"),
            ("  2. 감리비", supervision, "%.2f%% (관급자재 제외 도급공사비 기준)" % (o["supervision_rate"] * 100)),
            ("  3. 시설부대비", facility, "%.2f%% (관급자재 제외 도급공사비 기준)" % (o["facility_rate"] * 100))]
    df = pd.DataFrame(out, columns=["구분", "금액(원)", "비고"])
    df["금액(백만원)"] = (df["금액(원)"] / 1e6).round(0).astype(int)
    df["금액(원)"] = df["금액(원)"].round(0).astype("int64")
    return df[["구분", "금액(백만원)", "금액(원)", "비고"]]
