"""실무 양식 수량산출서(Excel, 수식 연동).

시트 구성 (설계도서 수량산출서 관행):
  1.수량총괄표        공종·항목·규격별 총수량 = 5.관로별수량산출서 SUMIFS 수식
  2.비굴착보수 집계표  관로별 전체보수(m)·부분보수 규격별 개소·준설·세정·CCTV·물돌리기·돌출관 절단, 합계 SUM
  3.굴착공 집계표      굴착 구간별 단면(B·H·상단폭·단면적)과 터파기·모래기초·되메우기·잔토·포장·가시설 수량(수식)
  4.단면 산출근거      굴착 단면 규칙과 적용 계수
  5.관로별수량산출서   관로·공종별 수량 명세(총괄표 수식의 원본)
  6.맨홀보수 집계표    (맨홀 결과가 주어진 경우) 맨홀별 공법·수량
"""
from __future__ import annotations

import io
from collections import defaultdict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from common.models import M_CIPP, M_PARTIAL_DIG, M_PATCH, M_REPLACE, M_UPSIZE, ProjectResult
from quantity_takeoff.costs import trench_section

HEAD = PatternFill("solid", fgColor="DDEBF7")
SUM = PatternFill("solid", fgColor="FFF2CC")
THIN = Side(style="thin", color="999999")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
GROUP_ORDER = ["토공", "가시설공", "관부설공", "포장공", "비굴착보수공", "부대공"]
PATCH_SIZES = [1.0, 1.5, 2.0, 2.5, 3.0]


def _title(ws, text: str, sub: str, ncol: int) -> int:
    ws.cell(1, 1, text).font = Font(bold=True, size=14)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    ws.cell(1, 1).alignment = Alignment(horizontal="center")
    ws.cell(2, 1, sub).font = Font(size=9, color="666666")
    return 4


def _header(ws, row: int, cols: list[str], widths: list[float] | None = None) -> None:
    for j, c in enumerate(cols, 1):
        cell = ws.cell(row, j, c)
        cell.font = Font(bold=True)
        cell.fill = HEAD
        cell.border = BOX
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        if widths:
            ws.column_dimensions[get_column_letter(j)].width = widths[j - 1]
    ws.row_dimensions[row].height = 30


def _row(ws, row: int, vals: list, fmt: dict[int, str] | None = None, fill=None) -> None:
    for j, v in enumerate(vals, 1):
        cell = ws.cell(row, j, v)
        cell.border = BOX
        if fill:
            cell.fill = fill
        if fmt and j in fmt:
            cell.number_format = fmt[j]


def _sum_row(ws, row: int, first: int, last: int, cols: range, label_col: int = 1, label: str = "합 계") -> None:
    ws.cell(row, label_col, label).font = Font(bold=True)
    for j in range(1, ws.max_column + 1):
        ws.cell(row, j).fill = SUM
        ws.cell(row, j).border = BOX
    for j in cols:
        L = get_column_letter(j)
        c = ws.cell(row, j, f"=SUM({L}{first}:{L}{last})" if last >= first else 0)
        c.font = Font(bold=True)
        c.number_format = "#,##0.00"


def _dig_segments(res: ProjectResult):
    """굴착 구간 목록: (관로, 결정, 구간명, 연장, 신설관경, 굴착심도)."""
    for p in res.pipes:
        d = res.decisions.get(p.pipe_id)
        if d is None:
            continue
        if d.method in (M_UPSIZE, M_REPLACE):
            nd = d.new_diameter_mm or p.diameter_mm
            yield p, d, "전 구간", p.plan_length_m, nd, p.avg_depth_m + max(0, nd - p.diameter_mm) / 1000.0
        elif d.method == M_PARTIAL_DIG:
            for c in d.dig_clusters:
                yield p, d, f"부분굴착#{c.cluster_no} ({c.repair_start_m:.1f}~{c.repair_end_m:.1f}m)", c.length_m, \
                    p.diameter_mm, p.avg_depth_m


def build_quantity_book(res: ProjectResult, project: str = "노후하수관로 정비사업", manhole_works=None) -> bytes:
    cfg = res.config
    ex = cfg["excavation"]
    wb = Workbook()

    # ---------------------------------------------------------------- 5. 관로별수량산출서 (원본)
    ws5 = wb.active
    ws5.title = "5.관로별수량산출서"
    r = _title(ws5, "관로별 수량산출서", f"{project} · 정비공법 판정 결과에 따른 관로·공종별 수량", 9)
    cols5 = ["관번", "시점맨홀", "종점맨홀", "정비공법", "공종구분", "항목", "규격", "단위", "수량"]
    _header(ws5, r, cols5, [12, 11, 11, 16, 12, 18, 34, 7, 12])
    first5 = r + 1
    pmap = {p.pipe_id: p for p in res.pipes}
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    items = sorted(res.quantities, key=lambda q: (q.pipe_id, order.get(q.group, 9)))
    for q in items:
        r += 1
        p = pmap.get(q.pipe_id)
        spec = q.spec.split("] ", 1)[-1] if q.spec.startswith("[") else q.spec
        spec_key = spec.split(" H=")[0]
        _row(ws5, r, [q.pipe_id, p.from_mh if p else "", p.to_mh if p else "", q.method, q.group, q.item, spec_key, q.unit,
                      round(q.qty, 2)], {9: "#,##0.00"})
    last5 = r
    ws5.freeze_panes = ws5.cell(first5, 1)
    ws5.auto_filter.ref = f"A{first5 - 1}:I{last5}"

    # ---------------------------------------------------------------- 1. 수량총괄표 (SUMIFS)
    ws1 = wb.create_sheet("1.수량총괄표", 0)
    r = _title(ws1, "수 량 총 괄 표", f"{project} · 수량은 '5.관로별수량산출서'의 합계 수식(SUMIFS)으로 연동", 6)
    _header(ws1, r, ["공종구분", "항목", "규격", "단위", "수량", "비고"], [14, 22, 36, 8, 14, 24])
    keys = sorted({(q.group, q.item, (q.spec.split('] ', 1)[-1] if q.spec.startswith('[') else q.spec).split(" H=")[0], q.unit)
                   for q in res.quantities}, key=lambda k: (order.get(k[0], 9), k[1], k[2]))
    rng = lambda c: f"'5.관로별수량산출서'!${c}${first5}:${c}${max(last5, first5)}"  # noqa: E731
    for g, it, sp, u in keys:
        r += 1
        f = (f'=SUMIFS({rng("I")},{rng("E")},A{r},{rng("F")},B{r},{rng("G")},C{r},{rng("H")},D{r})')
        _row(ws1, r, [g, it, sp, u, f, ""], {5: "#,##0.00"})
    ws1.freeze_panes = ws1.cell(5, 1)

    # ---------------------------------------------------------------- 2. 비굴착보수 집계표
    ws2 = wb.create_sheet("2.비굴착보수 집계표", 1)
    cols2 = (["관번", "시점", "종점", "관종", "관경(mm)", "연장(m)", "정비공법", "전체보수(m)"]
             + [f"부분보수 {s:.1f}m(개소)" for s in PATCH_SIZES]
             + ["준설(m)", "고압세정(m)", "CCTV(m)", "물돌리기(구간)", "돌출관절단(개소)"])
    r = _title(ws2, "비굴착 보수공 수량 집계표", f"{project} · 부분보수 규격은 근접결함 병합 후 표준규격", len(cols2))
    _header(ws2, r, cols2, [11, 10, 10, 8, 9, 9, 16, 11] + [10] * len(PATCH_SIZES) + [10, 10, 10, 10, 11])
    first2 = r + 1
    by_pipe: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    for q in res.quantities:
        by_pipe[q.pipe_id][q.item] += q.qty
    for p in res.pipes:
        d = res.decisions.get(p.pipe_id)
        if d is None or d.method not in (M_CIPP, M_PATCH, M_PARTIAL_DIG):
            continue
        sizes = defaultdict(int)
        for c in d.patch_clusters:
            sizes[c.unit_size_m] += c.n_sites
        b = by_pipe[p.pipe_id]
        r += 1
        _row(ws2, r, [p.pipe_id, p.from_mh, p.to_mh, p.material, p.diameter_mm, p.plan_length_m, d.method,
                      round(b.get("비굴착 전체보수", 0), 2)] + [sizes.get(s, 0) or None for s in PATCH_SIZES]
             + [round(b.get("관로 준설", 0), 2), round(b.get("고압 세정", 0), 2), round(b.get("CCTV 촬영", 0), 2),
                b.get("물돌리기", 0) or None, b.get("돌출관 절단", 0) or None], {6: "#,##0.00", 8: "#,##0.00"})
    last2 = r
    r += 1
    _sum_row(ws2, r, first2, last2, [6] + list(range(8, len(cols2) + 1)))
    ws2.freeze_panes = ws2.cell(first2, 2)

    # ---------------------------------------------------------------- 3. 굴착공 집계표 (수식)
    ws3 = wb.create_sheet("3.굴착공 집계표", 2)
    cols3 = ["관번", "구간", "정비공법", "기존관경", "신설관경", "연장 L(m)", "굴착방식", "굴착깊이 H(m)", "바닥폭 B(m)",
             "상단폭(m)", "단면적 A(㎡)", "모래기초 단면(㎡)", "관 단면(㎡)", "터파기(㎥)", "모래기초(㎥)", "되메우기(㎥)",
             "잔토처리(㎥)", "포장폭(m)", "포장깨기·복구(㎡)", "가시설(㎡)", "포장종류"]
    r = _title(ws3, "굴착공 수량 집계표", f"{project} · 터파기 = A×L, 되메우기 = (A − 모래기초 − 관)×L, "
               f"잔토 = (터파기 − 되메우기)×{ex['soil_bulking_factor']}, 가시설 = 2×H×L", len(cols3))
    _header(ws3, r, cols3, [11, 26, 14, 8, 8, 9, 10, 9, 9, 9, 10, 10, 9, 11, 11, 11, 11, 9, 12, 11, 9])
    first3 = r + 1
    bulk = float(ex["soil_bulking_factor"])
    for p, d, seg, L, nd, depth in _dig_segments(res):
        s = trench_section(cfg, nd, depth)
        r += 1
        R = r
        _row(ws3, R, [p.pipe_id, seg, d.method, p.diameter_mm, nd, round(L, 2), s.method, s.depth_m, s.bottom_width_m,
                      s.top_width_m, round(s.area_m2, 4), round(s.sand_area_m2, 4), round(s.pipe_area_m2, 4),
                      f"=K{R}*F{R}", f"=L{R}*F{R}", f"=MAX(N{R}-O{R}-M{R}*F{R},0)", f"=(N{R}-P{R})*{bulk}",
                      round(s.pave_width_m, 3), f"=R{R}*F{R}", (f"=2*H{R}*F{R}" if s.shoring else 0), p.pavement],
             {k: "#,##0.00" for k in range(6, 21)})
    last3 = r
    r += 1
    _sum_row(ws3, r, first3, last3, [6] + list(range(14, 18)) + [19, 20])
    ws3.freeze_panes = ws3.cell(first3, 3)

    # ---------------------------------------------------------------- 4. 단면 산출근거
    ws4 = wb.create_sheet("4.단면 산출근거", 3)
    r = _title(ws4, "굴착 단면 산출근거", "설정 파일 excavation 항목 기준", 3)
    _header(ws4, r, ["구분", "산출식 · 기준", "적용값"], [22, 70, 16])
    rows4 = [
        ("관 외경 Do", "관경 D × 외경계수", ex["outer_diameter_factor"]),
        ("굴착깊이 H", "평균 관저심도 + 기초두께", f"기초 {ex['foundation_thickness_m']}m"),
        ("사면굴착", f"H < {ex['slope_cut_max_depth_m']}m · 바닥폭 B = max(최소폭, Do + 2×작업여유) · 단면 A = (B + n·H)·H",
         f"n = 1:{ex['slope_ratio']}"),
        ("간이흙막이", f"{ex['slope_cut_max_depth_m']} ≤ H < {ex['simple_shoring_max_depth_m']}m · 수직굴착 A = B·H", "가시설 2·H·L"),
        ("강재토류판", f"H ≥ {ex['simple_shoring_max_depth_m']}m · 수직굴착 A = B·H", "가시설 2·H·L"),
        ("최소 바닥폭", "굴착 바닥폭 하한", f"{ex['min_bottom_width_m']} m"),
        ("작업여유(사면/수직)", "관 양측 작업공간", f"{ex['work_space_each_side_m']} / {ex['shoring_work_space_each_side_m']} m"),
        ("모래기초", "기초두께 + Do + 관상단 덮개, 관 단면 제외", f"관상단 +{ex['sand_cover_above_crown_m']} m"),
        ("되메우기", "터파기 − 모래기초 − 관 체적", "-"),
        ("잔토처리", "(터파기 − 되메우기) × 토량환산계수", bulk),
        ("포장 깨기·복구 폭", "굴착 상단폭 + 양측 여유", f"+{ex['pavement_extra_width_m']} m × 2"),
    ]
    for v in rows4:
        r += 1
        _row(ws4, r, list(v))
        ws4.cell(r, 2).alignment = Alignment(wrap_text=True)

    # ---------------------------------------------------------------- 6. 맨홀보수 집계표
    if manhole_works is not None and len(manhole_works):
        ws6 = wb.create_sheet("6.맨홀보수 집계표")
        works = sorted(manhole_works["공법"].unique().tolist())
        cols6 = ["맨홀번호"] + [f"{w}({manhole_works[manhole_works['공법'] == w]['단위'].iloc[0]})" for w in works]
        r = _title(ws6, "맨홀 보수 수량 집계표", f"{project} · 맨홀별 보수공법 수량", len(cols6))
        _header(ws6, r, cols6, [14] + [14] * len(works))
        first6 = r + 1
        piv = manhole_works.pivot_table(index="맨홀번호", columns="공법", values="수량", aggfunc="sum", sort=False)
        for mh, rowv in piv.iterrows():
            r += 1
            _row(ws6, r, [mh] + [(None if rowv.get(w) != rowv.get(w) else rowv.get(w)) for w in works])
        last6 = r
        r += 1
        _sum_row(ws6, r, first6, last6, range(2, len(cols6) + 1))

    for ws in wb.worksheets:
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToHeight = 0
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
