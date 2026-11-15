"""SWMM 모델·결과를 CCTV 조서 관로에 결합하고, SWMM 모델에서 조서 양식을 만든다.

우선순위: 관종·관경·연장·심도는 CCTV 조서(실측) 우선, 수리 항목(조도계수·설계유량·통수능비)과
조서에 비어 있는 관저고·좌표는 SWMM 값으로 채운다. 관로 매칭은 관번=Conduit 이름, 없으면 시·종점 맨홀명.
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from common.models import Pipe
from defect_processor.template import DEFECT_COLS
from swmm_link.inp_parser import LinkResult, SwmmConduit, SwmmModel, steady_flows_lps


def match_conduit(pipe: Pipe, model: SwmmModel) -> SwmmConduit | None:
    lower = {k.lower(): c for k, c in model.conduits.items()}
    c = model.conduits.get(pipe.pipe_id) or lower.get(pipe.pipe_id.lower())
    if c:
        return c
    for c in model.conduits.values():
        if c.from_node == pipe.from_mh and c.to_node == pipe.to_mh:
            return c
    return None


def _insufficient(res: LinkResult, c: dict) -> tuple[bool, str]:
    flow_lim = float(c.get("swmm_flow_ratio_limit", 1.0))
    hours_lim = float(c.get("swmm_surcharge_hours_limit", 1.0))
    if res.flow_ratio >= flow_lim:
        return True, f"Max/Full Flow {res.flow_ratio:.2f}"
    if res.surcharge_hours is not None and res.surcharge_hours >= hours_lim:
        return True, f"만관(Surcharge) {res.surcharge_hours:.1f}h"
    return False, ""


def apply_swmm(pipes: list[Pipe], model: SwmmModel, results: dict[str, LinkResult] | None, cfg: dict) -> list[str]:
    """조서 관로에 SWMM 정보를 채우고 경고 목록을 반환한다."""
    warns = [f"SWMM: {w}" for w in model.warnings]
    c_cfg = cfg["criteria"]
    matched = 0
    used: set[str] = set()
    steady_all = steady_flows_lps(model) if model.routing == "STEADY" else {}
    for p in pipes:
        c = match_conduit(p, model)
        if c is None:
            warns.append(f"[{p.pipe_id}] SWMM 모델에서 대응 관로를 찾지 못함(관번·시종점 맨홀 불일치)")
            continue
        matched += 1
        used.add(c.name)
        if p.roughness_n is None and c.n > 0:
            p.roughness_n = c.n
        inv_f, inv_t = model.inverts(c)
        nf, nt = model.nodes.get(c.from_node), model.nodes.get(c.to_node)
        if p.from_invert_el is None and p.to_invert_el is None and (nf and nt):
            p.from_invert_el, p.to_invert_el = round(inv_f, 3), round(inv_t, 3)
            if p.from_ground_el is None and nf.ground is not None:
                p.from_ground_el = round(nf.ground, 3)
            if p.to_ground_el is None and nt.ground is not None:
                p.to_ground_el = round(nt.ground, 3)
        if not p.has_coords and nf and nt and None not in (nf.x, nf.y, nt.x, nt.y):
            p.from_x, p.from_y, p.to_x, p.to_y = nf.x, nf.y, nt.x, nt.y
        if c.diameter and abs(c.diameter * 1000 - p.diameter_mm) / p.diameter_mm > 0.02:
            warns.append(f"[{p.pipe_id}] 관경 불일치: 조서 D{p.diameter_mm} / SWMM D{c.diameter * 1000:.0f} (조서 우선)")
        if c.length and abs(c.length - p.plan_length_m) / p.plan_length_m > 0.05:
            warns.append(f"[{p.pipe_id}] 연장 불일치: 조서 {p.plan_length_m:.1f}m / SWMM {c.length:.1f}m (조서 우선)")
        steady = steady_all.get(c.name)
        if p.design_flow_lps is None and steady:
            p.design_flow_lps = round(steady, 3)           # [DWF] 누적값(정확) 우선
        r = (results or {}).get(c.name)
        if r is None:
            continue
        if p.design_flow_lps is None:
            p.design_flow_lps = round(r.max_flow_lps, 2)
        if p.capacity_ratio is None:
            p.capacity_ratio = round(r.flow_ratio, 3)
        bad, why = _insufficient(r, c_cfg)
        if bad:
            p.capacity_insufficient = True
            p.warnings.append(f"SWMM 통수능 부족: {why}")
    if results is None:
        warns.append("SWMM 결과(.rpt) 없음: 설계유량·통수능비는 조서 값 또는 Manning 검토에 의존")
    extra = [n for n in model.conduits if n not in used]
    if extra:
        warns.append(f"SWMM에만 있는 관로 {len(extra)}개(조사 미실시): " + ", ".join(extra[:8]) + (" ..." if len(extra) > 8 else ""))
    warns.append(f"SWMM 결합: 조서 관로 {len(pipes)}개 중 {matched}개 매칭")
    return warns


def write_survey_from_swmm(model: SwmmModel, results: dict[str, LinkResult] | None, path: str | Path) -> Path:
    """SWMM 모델의 관로 제원을 미리 채운 조서 양식을 만든다(결함은 CCTV 조사 후 입력)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "관로제원"
    heads = ["관번", "시점맨홀", "종점맨홀", "관종", "관경(mm)", "도면연장(m)", "촬영연장(m)", "평균심도(m)", "조도계수",
             "설계유량(L/s)", "통수능비", "시점X", "시점Y", "종점X", "종점Y", "시점지반고", "시점관저고", "종점지반고", "종점관저고"]
    ws.append(heads)
    steady = steady_flows_lps(model) if model.routing == "STEADY" else {}
    for name, c in model.conduits.items():
        nf, nt = model.nodes.get(c.from_node), model.nodes.get(c.to_node)
        inv_f, inv_t = model.inverts(c)
        gf = nf.ground if nf else None
        gt = nt.ground if nt else None
        depth = None
        if gf is not None and gt is not None:
            depth = round(((gf - inv_f) + (gt - inv_t)) / 2, 2)    # 평균 관저 매설심도 = 지반고 - 관저고
        r = (results or {}).get(name)
        flow = steady.get(name) or (r.max_flow_lps if r else None)       # STEADY 모델은 [DWF] 누적값(정확) 우선
        ws.append([name, c.from_node, c.to_node, "PE관" if c.n <= 0.0115 else "흄관", round(c.diameter * 1000), round(c.length, 2),
                   None, depth, c.n, round(flow, 3) if flow else None, round(r.flow_ratio, 3) if r else None,
                   nf.x if nf else None, nf.y if nf else None, nt.x if nt else None, nt.y if nt else None,
                   round(gf, 3) if gf is not None else None, round(inv_f, 3), round(gt, 3) if gt is not None else None, round(inv_t, 3)])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws2 = wb.create_sheet("결함내역")
    ws2.append([n for n, _r, _d in DEFECT_COLS])
    for cell in ws2[1]:
        cell.font = Font(bold=True)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
