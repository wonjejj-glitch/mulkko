"""공사비 내역서 · 원가계산서 Excel (수식 연동).

- 공법별로 공종·품명·규격·수량을 나열하고 단가를 재료비/노무비/경비로 분해한다.
- 금액·소계·집계·원가계산서는 엑셀 수식이므로, 수량·단가·요율 칸을 고치면 전체가 재계산된다.
- 재료/노무/경비 분해 비율과 제경비 요율은 `config/default_criteria.json`의 `cost_statement`(예시값)이다.
  공식 품셈 코드·일위대가는 포함하지 않으며, 제출용으로는 해당 연도 단가·요율로 교체해야 한다.
"""
from __future__ import annotations

import math
import re
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from common.models import METHODS, ProjectResult

GROUP_ORDER = ["포장공", "토공", "가시설공", "관부설공", "비굴착보수공", "부대공"]
SHEET_SUM, SHEET_COST, SHEET_DETAIL, SHEET_BASIS = "1_공사비집계표", "2_원가계산서", "3_공사비내역서", "4_단가근거"

HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
HEAD_FONT = Font(bold=True, color="FFFFFF")
SECTION_FILL = PatternFill("solid", fgColor="FCE4D6")
SUB_FILL = PatternFill("solid", fgColor="DDEBF7")
TOTAL_FILL = PatternFill("solid", fgColor="FFE699")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Side(style="thin", color="A6A6A6")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
KRW = "#,##0"

SOURCE = {
    "비굴착 전체보수": "unit_prices.cipp_full_per_m (관경별)",
    "비굴착 부분보수": "unit_prices.partial_repair_per_site × criteria.partial_repair_size_factor",
    "관로 준설": "unit_prices.dredging_per_m (관경별)", "고압 세정": "unit_prices.cleaning_per_m (관경별)",
    "CCTV 촬영": "unit_prices.cctv_per_m", "물돌리기": "unit_prices.bypass_per_span (관경별)",
    "돌출관 절단": "unit_prices.lateral_cut_per_site", "터파기": "unit_prices.excavation_per_m3",
    "되메우기": "unit_prices.backfill_per_m3", "모래기초": "unit_prices.sand_foundation_per_m3",
    "잔토처리": "unit_prices.surplus_soil_per_m3", "간이흙막이": "unit_prices.simple_shoring_per_m2",
    "강재토류판": "unit_prices.steel_sheathing_per_m2", "포장깨기": "unit_prices.pavement_break_per_m2",
    "포장복구": "unit_prices.pavement_restore_per_m2", "관 부설(자재포함)": "unit_prices.pipe_install_per_m (관경별)",
    "기존관 철거": "unit_prices.pipe_removal_per_m (관경별)",
}

# (비목, 종류, 키/구성항목, 기준항목)  — link: 내역서 합계 / sum: 합산 / rate: 요율×기준
COST_ROWS = [
    ("재료비", "link", "M", []), ("직접노무비", "link", "L", []), ("산출경비", "link", "E", []),
    ("직접공사비", "sum", ["재료비", "직접노무비", "산출경비"], []),
    ("간접노무비", "rate", "indirect_labor", ["직접노무비"]),
    ("노무비계", "sum", ["직접노무비", "간접노무비"], []),
    ("산재보험료", "rate", "industrial_accident", ["노무비계"]),
    ("고용보험료", "rate", "employment", ["노무비계"]),
    ("건강보험료", "rate", "health", ["직접노무비"]),
    ("노인장기요양보험료", "rate", "care", ["건강보험료"]),
    ("국민연금보험료", "rate", "pension", ["직접노무비"]),
    ("퇴직공제부금비", "rate", "retirement", ["직접노무비"]),
    ("산업안전보건관리비", "rate", "safety", ["재료비", "직접노무비"]),
    ("환경보전비", "rate", "environment", ["직접공사비"]),
    ("기타경비", "rate", "other_expense", ["재료비", "노무비계"]),
    ("하도급대금지급보증수수료", "rate", "subcontract_guarantee", ["직접공사비"]),
    ("건설기계대여금지급보증서수수료", "rate", "equipment_guarantee", ["직접공사비"]),
    ("석면피해구제분담금", "rate", "asbestos", ["노무비계"]),
    ("임금채권부담금", "rate", "wage_claim", ["노무비계"]),
    ("경비계", "sum", ["산출경비", "산재보험료", "고용보험료", "건강보험료", "노인장기요양보험료", "국민연금보험료",
                     "퇴직공제부금비", "산업안전보건관리비", "환경보전비", "기타경비", "하도급대금지급보증수수료",
                     "건설기계대여금지급보증서수수료", "석면피해구제분담금", "임금채권부담금"], []),
    ("순공사원가", "sum", ["재료비", "노무비계", "경비계"], []),
    ("일반관리비", "rate", "general_admin", ["순공사원가"]),
    ("이윤", "rate", "profit", ["노무비계", "경비계", "일반관리비"]),
    ("공급가액", "sum", ["순공사원가", "일반관리비", "이윤"], []),
    ("부가가치세", "rate", "vat", ["공급가액"]),
    ("총공사비", "sum", ["공급가액", "부가가치세"], []),
]
BOLD_ROWS = {"직접공사비", "노무비계", "경비계", "순공사원가", "공급가액", "총공사비"}


def rnd(x: float) -> int:
    """엑셀 ROUND(x,0)과 같은 사사오입(0에서 먼 쪽)."""
    return int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1)


@dataclass
class Line:
    group: str
    item: str
    spec: str
    unit: str
    qty: float
    mat_u: int
    lab_u: int
    exp_u: int
    pipes: list[str] = field(default_factory=list)

    @property
    def mat(self) -> int:
        return rnd(self.qty * self.mat_u)

    @property
    def lab(self) -> int:
        return rnd(self.qty * self.lab_u)

    @property
    def exp(self) -> int:
        return rnd(self.qty * self.exp_u)

    @property
    def unit_total(self) -> int:
        return self.mat_u + self.lab_u + self.exp_u

    @property
    def total(self) -> int:
        return self.mat + self.lab + self.exp


def split_unit(cfg: dict, group: str, unit_price: float) -> tuple[int, int, int]:
    table = cfg["cost_statement"]["cost_split"]
    r = table.get(group, table["default"])
    total = rnd(unit_price)
    m = rnd(total * r["material"])
    l = rnd(total * r["labor"])
    return m, l, max(total - m - l, 0)


def _clean_spec(spec: str) -> str:
    spec = re.sub(r"^\[[^\]]*\]\s*", "", spec)
    return re.sub(r"\s*H=.*$", "", spec)


def build_sections(res: ProjectResult) -> "OrderedDict[str, list[Line]]":
    """정비공법 → 내역 라인 목록(공종 순서). 같은 품명·규격은 합산한다."""
    cfg = res.config
    agg: dict[tuple, dict] = {}
    for q in res.quantities:
        key = (q.method, q.group, q.item, _clean_spec(q.spec), q.unit)
        a = agg.setdefault(key, {"qty": 0.0, "amount": 0.0, "pipes": [], "order": len(agg)})
        a["qty"] += q.qty
        a["amount"] += q.qty * q.unit_price
        if q.pipe_id not in a["pipes"]:
            a["pipes"].append(q.pipe_id)
    out: "OrderedDict[str, list[Line]]" = OrderedDict()
    for method in METHODS:
        rows = [(k, a) for k, a in agg.items() if k[0] == method]
        if not rows:
            continue
        rows.sort(key=lambda ka: (GROUP_ORDER.index(ka[0][1]) if ka[0][1] in GROUP_ORDER else 99, ka[1]["order"]))
        lines = []
        for (_m, group, item, spec, unit), a in rows:
            qty = round(a["qty"], 2)
            if qty <= 0:
                continue
            unit_price = a["amount"] / a["qty"]
            m, l, e = split_unit(cfg, group, unit_price)
            lines.append(Line(group, item, spec, unit, qty, m, l, e, a["pipes"]))
        out[method] = lines
    return out


def section_totals(lines: list[Line]) -> tuple[int, int, int]:
    return sum(x.mat for x in lines), sum(x.lab for x in lines), sum(x.exp for x in lines)


def compute_cost_table(mat: int, lab: int, exp: int, cfg: dict) -> "OrderedDict[str, dict]":
    """원가계산서 값(파이썬 계산) - 엑셀 수식과 동일한 순서·반올림."""
    rates = {r["key"]: float(r["rate"]) for r in cfg["cost_statement"]["rates"]}
    vals: "OrderedDict[str, dict]" = OrderedDict()
    link = {"M": mat, "L": lab, "E": exp}
    for name, kind, spec, base in COST_ROWS:
        if kind == "link":
            v, rate = link[spec], None
        elif kind == "sum":
            v, rate = sum(vals[n]["amount"] for n in spec), None
        else:
            rate = rates.get(spec, 0.0)
            v = rnd(rate * sum(vals[n]["amount"] for n in base))
        vals[name] = {"amount": v, "rate": rate}
    return vals


# ================================================================ 엑셀 작성
def _hdr(ws, row: int, headers: list[str]) -> None:
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=row, column=j, value=h)
        c.fill, c.font, c.border = HEAD_FILL, HEAD_FONT, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _style_row(ws, row: int, ncols: int, fill=None, bold=False) -> None:
    for j in range(1, ncols + 1):
        c = ws.cell(row=row, column=j)
        c.border = BORDER
        if fill:
            c.fill = fill
        if bold:
            c.font = Font(bold=True)


def _write_detail(ws, sections, res: ProjectResult) -> dict:
    ws.cell(row=1, column=1, value="공사비 내역서").font = Font(bold=True, size=14)
    ws.cell(row=2, column=1, value="※ 수량·단가를 수정하면 금액·소계·원가계산서가 자동 재계산됩니다. (노란색 칸 = 입력 가능)")
    heads = ["번호", "공종", "품명", "규격", "단위", "수량", "재료비\n단가", "재료비\n금액", "노무비\n단가", "노무비\n금액",
             "경비\n단가", "경비\n금액", "합계\n단가", "합계\n금액", "비고(관번)"]
    _hdr(ws, 4, heads)
    ws.row_dimensions[4].height = 32
    r = 5
    sub_rows: dict[str, int] = {}
    no = 0
    for method, lines in sections.items():
        ws.cell(row=r, column=1, value=f"■ {method}")
        _style_row(ws, r, 15, SECTION_FILL, True)
        r += 1
        first = r
        for ln in lines:
            no += 1
            vals = [no, ln.group, ln.item, ln.spec, ln.unit, ln.qty, ln.mat_u, None, ln.lab_u, None, ln.exp_u, None, None, None,
                    ", ".join(ln.pipes[:8]) + (f" 외 {len(ln.pipes) - 8}" if len(ln.pipes) > 8 else "")]
            for j, v in enumerate(vals, start=1):
                ws.cell(row=r, column=j, value=v)
            ws.cell(row=r, column=8, value=f"=ROUND(F{r}*G{r},0)")
            ws.cell(row=r, column=10, value=f"=ROUND(F{r}*I{r},0)")
            ws.cell(row=r, column=12, value=f"=ROUND(F{r}*K{r},0)")
            ws.cell(row=r, column=13, value=f"=G{r}+I{r}+K{r}")
            ws.cell(row=r, column=14, value=f"=H{r}+J{r}+L{r}")
            _style_row(ws, r, 15)
            for col in (6, 7, 9, 11):
                ws.cell(row=r, column=col).fill = INPUT_FILL
            for col in range(7, 15):
                ws.cell(row=r, column=col).number_format = KRW
            ws.cell(row=r, column=6).number_format = "#,##0.00"
            r += 1
        last = r - 1
        ws.cell(row=r, column=3, value=f"{method} 소계")
        for col in (8, 10, 12, 14):
            L = get_column_letter(col)
            ws.cell(row=r, column=col, value=f"=SUM({L}{first}:{L}{last})").number_format = KRW
        _style_row(ws, r, 15, SUB_FILL, True)
        sub_rows[method] = r
        r += 2
    ws.cell(row=r, column=3, value="합  계")
    for col in (8, 10, 12, 14):
        L = get_column_letter(col)
        ws.cell(row=r, column=col, value="=" + "+".join(f"{L}{s}" for s in sub_rows.values()) if sub_rows else 0).number_format = KRW
    _style_row(ws, r, 15, TOTAL_FILL, True)
    widths = [6, 12, 20, 24, 7, 11, 11, 13, 11, 13, 11, 13, 12, 14, 26]
    for j, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = "A5"
    return {"sub": sub_rows, "total": r}


def _write_cost(ws, cfg: dict, tot_row: int, values: "OrderedDict[str, dict]") -> dict:
    ws.cell(row=1, column=1, value="원가계산서").font = Font(bold=True, size=14)
    src = (cfg.get("price_source") or {}).get("title")
    ws.cell(row=2, column=1, value=(f"※ 요율(노란색)은 「{src}」 총괄설계내역서 요율입니다. " if src else
                                    "※ 요율(노란색)은 개략 검토용 예시값입니다. ")
            + "해당 연도 제비율로 수정하면 전체가 재계산됩니다.")
    _hdr(ws, 4, ["순번", "비 목", "산 출 근 거", "요율", "금 액(원)"])
    rates = {r["key"]: float(r["rate"]) for r in cfg["cost_statement"]["rates"]}
    pos: dict[str, int] = {}
    r = 5
    for i, (name, kind, spec, base) in enumerate(COST_ROWS, start=1):
        pos[name] = r
        ws.cell(row=r, column=1, value=i)
        ws.cell(row=r, column=2, value=name)
        if kind == "link":
            col = {"M": "H", "L": "J", "E": "L"}[spec]
            ws.cell(row=r, column=3, value="공사비 내역서 합계")
            ws.cell(row=r, column=5, value=f"='{SHEET_DETAIL}'!{col}{tot_row}")
        elif kind == "sum":
            ws.cell(row=r, column=3, value=" + ".join(spec))
            ws.cell(row=r, column=5, value="=" + "+".join(f"E{pos[n]}" for n in spec))
        else:
            ws.cell(row=r, column=3, value=f"({' + '.join(base)}) × 요율")
            c = ws.cell(row=r, column=4, value=rates.get(spec, 0.0))
            c.number_format, c.fill = "0.00%", INPUT_FILL
            ws.cell(row=r, column=5, value=f"=ROUND(D{r}*(" + "+".join(f"E{pos[n]}" for n in base) + "),0)")
        ws.cell(row=r, column=5).number_format = KRW
        _style_row(ws, r, 5, TOTAL_FILL if name == "총공사비" else (SUB_FILL if name in BOLD_ROWS else None), name in BOLD_ROWS)
        r += 1
    for j, w in enumerate([6, 22, 44, 9, 18], start=1):
        ws.column_dimensions[get_column_letter(j)].width = w
    return pos


def _write_summary(ws, res: ProjectResult, sections, detail_pos: dict, cost_pos: dict, project: str) -> None:
    ws.cell(row=1, column=1, value=f"{project} 공사비 집계표").font = Font(bold=True, size=14)
    ws.cell(row=2, column=1, value=f"작성일 {date.today():%Y-%m-%d} · 관로 {len(res.pipes)}개 · "
                                    f"총 연장 {sum(p.plan_length_m for p in res.pipes):,.1f}m")
    _hdr(ws, 4, ["정비공법", "관로수", "연장(m)", "재료비", "노무비", "경비", "직접공사비", "구성비"])
    r = 5
    first = r
    for method, _lines in sections.items():
        s = detail_pos["sub"][method]
        pipes = [p for p in res.pipes if res.decisions[p.pipe_id].method == method]
        ws.cell(row=r, column=1, value=method)
        ws.cell(row=r, column=2, value=len(pipes))
        ws.cell(row=r, column=3, value=round(sum(p.plan_length_m for p in pipes), 2)).number_format = "#,##0.00"
        for col, src in ((4, "H"), (5, "J"), (6, "L")):
            ws.cell(row=r, column=col, value=f"='{SHEET_DETAIL}'!{src}{s}").number_format = KRW
        ws.cell(row=r, column=7, value=f"=D{r}+E{r}+F{r}").number_format = KRW
        _style_row(ws, r, 8)
        r += 1
    last = r - 1
    for rr in range(first, last + 1):
        ws.cell(row=rr, column=8, value=f"=IF($G${r}=0,0,G{rr}/$G${r})").number_format = "0.0%"
    ws.cell(row=r, column=1, value="직접공사비 계")
    for col in (2, 3, 4, 5, 6, 7):
        L = get_column_letter(col)
        ws.cell(row=r, column=col, value=f"=SUM({L}{first}:{L}{last})").number_format = (
            KRW if col > 3 else ("0" if col == 2 else "#,##0.00"))
    _style_row(ws, r, 8, SUB_FILL, True)
    ws.cell(row=r, column=8, value=f"=IF(G{r}=0,0,1)").number_format = "0.0%"
    direct = r
    r += 2
    items = [("직접공사비", f"=G{direct}"),
             ("간접노무비·경비·일반관리비·이윤", f"='{SHEET_COST}'!E{cost_pos['공급가액']}-G{direct}"),
             ("공급가액", f"='{SHEET_COST}'!E{cost_pos['공급가액']}"),
             ("부가가치세", f"='{SHEET_COST}'!E{cost_pos['부가가치세']}"),
             ("총공사비", f"='{SHEET_COST}'!E{cost_pos['총공사비']}")]
    for name, f in items:
        ws.cell(row=r, column=1, value=name)
        ws.cell(row=r, column=7, value=f).number_format = KRW
        _style_row(ws, r, 8, TOTAL_FILL if name == "총공사비" else None, name in ("공급가액", "총공사비"))
        r += 1
    ws.cell(row=r + 1, column=1, value=res.config["cost_statement"]["note"])
    for j, w in enumerate([30, 8, 11, 15, 15, 15, 17, 9], start=1):
        ws.column_dimensions[get_column_letter(j)].width = w


def _write_basis(ws, sections, cfg: dict) -> None:
    ws.cell(row=1, column=1, value="단가 근거 및 재료·노무·경비 분해").font = Font(bold=True, size=14)
    _hdr(ws, 3, ["공종", "품명", "규격", "단위", "단가(원)", "재료비", "노무비", "경비", "단가 출처(config 키)"])
    r = 4
    seen = set()
    for lines in sections.values():
        for ln in lines:
            key = (ln.item, ln.spec, ln.unit_total)
            if key in seen:
                continue
            seen.add(key)
            for j, v in enumerate([ln.group, ln.item, ln.spec, ln.unit, ln.unit_total, ln.mat_u, ln.lab_u, ln.exp_u,
                                   SOURCE.get(ln.item, "")], start=1):
                c = ws.cell(row=r, column=j, value=v)
                c.border = BORDER
                if 5 <= j <= 8:
                    c.number_format = KRW
            r += 1
    r += 1
    ws.cell(row=r, column=1, value="공종별 분해 비율(config.cost_statement.cost_split)").font = Font(bold=True)
    r += 1
    _hdr(ws, r, ["공종", "재료비", "노무비", "경비"])
    for g, v in cfg["cost_statement"]["cost_split"].items():
        r += 1
        for j, x in enumerate([g, v["material"], v["labor"], v["expense"]], start=1):
            c = ws.cell(row=r, column=j, value=x)
            c.border = BORDER
            if j > 1:
                c.number_format = "0%"
    for j, w in enumerate([14, 22, 28, 8, 12, 12, 12, 12, 52], start=1):
        ws.column_dimensions[get_column_letter(j)].width = w


def export_cost_statement(res: ProjectResult, path: str | Path, project_name: str = "노후하수관로 정비사업") -> Path:
    cfg = res.config
    sections = build_sections(res)
    wb = Workbook()
    ws_sum = wb.active
    ws_sum.title = SHEET_SUM
    ws_cost = wb.create_sheet(SHEET_COST)
    ws_det = wb.create_sheet(SHEET_DETAIL)
    ws_basis = wb.create_sheet(SHEET_BASIS)

    detail_pos = _write_detail(ws_det, sections, res)
    cost_pos = _write_cost(ws_cost, cfg, detail_pos["total"], compute_cost_table(*_totals(sections), cfg))
    _write_summary(ws_sum, res, sections, detail_pos, cost_pos, project_name)
    _write_basis(ws_basis, sections, cfg)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _totals(sections) -> tuple[int, int, int]:
    m = l = e = 0
    for lines in sections.values():
        a, b, c = section_totals(lines)
        m, l, e = m + a, l + b, e + c
    return m, l, e


def total_cost(res: ProjectResult) -> int:
    """파이썬 계산 총공사비(엑셀 원가계산서와 동일)."""
    return compute_cost_table(*_totals(build_sections(res)), res.config)["총공사비"]["amount"]
