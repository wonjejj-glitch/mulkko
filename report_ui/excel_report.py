"""실시설계 총괄조서 다중 시트 Excel 출력."""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from common.models import ProjectResult
from report_ui import tables as T

HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
HEAD_FONT = Font(bold=True, color="FFFFFF")
TOTAL_FILL = PatternFill("solid", fgColor="DDEBF7")
TITLE_FONT = Font(bold=True, size=14)
THIN = Side(style="thin", color="A6A6A6")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP_COLS = {"선정사유", "검토사항", "포함결함(위치·코드·등급)", "세부내용", "결함내용", "규격"}


def _write_block(ws: Worksheet, df: pd.DataFrame, start_row: int, title: str | None = None,
                 total_last: bool = False) -> int:
    """DataFrame을 지정 행부터 서식과 함께 기록하고, 다음 빈 행 번호를 반환."""
    r = start_row
    if title:
        ws.cell(row=r, column=1, value=title).font = Font(bold=True, size=11, color="1F4E78")
        r += 1
    for j, col in enumerate(df.columns, start=1):
        c = ws.cell(row=r, column=j, value=col)
        c.fill, c.font, c.border = HEAD_FILL, HEAD_FONT, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    header_row = r
    for i, row in enumerate(df.itertuples(index=False), start=1):
        for j, (col, val) in enumerate(zip(df.columns, row), start=1):
            if isinstance(val, float) and pd.isna(val):
                val = None
            c = ws.cell(row=r + i, column=j, value=val)
            c.border = BORDER
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                if "원" in col:
                    c.number_format = "#,##0"
                elif "%" in col or "(m)" in col or "m)" in col or "수량" in col or "계수" in col:
                    c.number_format = "#,##0.00" if "계수" not in col else "0.0000"
            c.alignment = Alignment(vertical="top", wrap_text=col in WRAP_COLS)
            if total_last and i == len(df):
                c.fill = TOTAL_FILL
                c.font = Font(bold=True)
    ws.row_dimensions[header_row].height = 30
    return r + len(df) + 2


def _autowidth(ws: Worksheet, df_cols: list[str], max_w: int = 60) -> None:
    for j, col in enumerate(df_cols, start=1):
        letter = get_column_letter(j)
        width = max(len(str(col)) * 1.8, 8)
        for cell in ws[letter][1:200]:
            if cell.value is not None:
                s = str(cell.value)
                w = sum(1.8 if ord(ch) > 127 else 1.0 for ch in s[:80])
                width = max(width, w)
        ws.column_dimensions[letter].width = min(width + 2, max_w if col in WRAP_COLS else 40)


def _sheet(writer, name: str) -> Worksheet:
    wb = writer.book
    ws = wb.create_sheet(name)
    return ws


def build_workbook(res: ProjectResult, project_name: str = "노후하수관로 정비사업") -> bytes:
    summary = T.summary_by_method(res)
    by_dia = T.length_by_diameter(res)
    crit = T.criteria_table(res)
    detail = T.pipe_detail(res)
    merge = T.merge_detail(res)
    q_sum, q_detail = T.quantity_tables(res)
    defects = T.defect_detail(res)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        wb = writer.book
        # 1. 총괄표
        ws = _sheet(writer, "1_정비계획총괄표")
        ws.cell(row=1, column=1, value=f"{project_name} 정비계획 총괄표").font = TITLE_FONT
        ws.cell(row=2, column=1, value=f"작성일 {date.today():%Y-%m-%d} · 관로 {len(res.pipes)}개 · "
                                         f"총 연장 {sum(p.plan_length_m for p in res.pipes):,.1f}m · "
                                         f"개략공사비 {res.total_cost:,.0f}원 (직접공사비"
                                         f"{' + 제경비' if res.config['criteria'].get('overhead_ratio') else ''})")
        r = _write_block(ws, summary, 4, "■ 정비공법별 총괄", total_last=True)
        if not by_dia.empty:
            r = _write_block(ws, by_dia, r, "■ 관경별·공법별 연장(m)")
        r = _write_block(ws, crit, r, "■ 적용 판정기준")
        _autowidth(ws, list(summary.columns) + [""] * 5)
        ws.column_dimensions["A"].width = 34

        # 2. 관로별 상세
        ws = _sheet(writer, "2_관로별상세정비조서")
        _write_block(ws, detail, 1)
        _autowidth(ws, list(detail.columns), max_w=90)
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions

        # 3. 부분보수 병합내역
        ws = _sheet(writer, "3_부분보수병합내역")
        _write_block(ws, merge, 1)
        _autowidth(ws, list(merge.columns))
        ws.freeze_panes = "B2"
        if not merge.empty:
            ws.auto_filter.ref = ws.dimensions

        # 4. 공종별 수량 및 개략공사비
        ws = _sheet(writer, "4_공종별수량및개략공사비")
        ws.cell(row=1, column=1, value="공종별 수량 및 개략공사비 집계").font = TITLE_FONT
        if not q_sum.empty:
            direct = q_sum["금액(원)"].sum()
            oh = float(res.config["criteria"].get("overhead_ratio", 0.0))
            tot = pd.DataFrame([
                {"공종구분": "직접공사비 계", "항목": "", "규격": "", "단위": "", "수량": None, "평균단가(원)": None, "금액(원)": direct},
            ] + ([{"공종구분": f"제경비({oh * 100:.0f}%)", "항목": "", "규격": "", "단위": "", "수량": None,
                    "평균단가(원)": None, "금액(원)": round(direct * oh)}] if oh else []) + [
                {"공종구분": "개략공사비 합계", "항목": "", "규격": "", "단위": "", "수량": None, "평균단가(원)": None,
                 "금액(원)": round(direct * (1 + oh))},
            ])
            q_out = pd.concat([q_sum, tot], ignore_index=True)
        else:
            q_out = q_sum
        _write_block(ws, q_out, 3, total_last=False)
        _autowidth(ws, list(q_out.columns))

        # 부속 시트
        ws = _sheet(writer, "5_관로별수량상세")
        _write_block(ws, q_detail, 1)
        _autowidth(ws, list(q_detail.columns))
        ws.freeze_panes = "A2"
        if not q_detail.empty:
            ws.auto_filter.ref = ws.dimensions

        ws = _sheet(writer, "6_결함위치보정내역")
        _write_block(ws, defects, 1)
        _autowidth(ws, list(defects.columns))
        ws.freeze_panes = "B2"
        if not defects.empty:
            ws.auto_filter.ref = ws.dimensions

        cap = T.capacity_comparison(res)
        if not cap.empty:
            ws = _sheet(writer, "7_정비전후통수능")
            ws.cell(row=1, column=1, value="정비 전·후 만관 통수능·유속 비교 (Manning 개략, 유량 ㎥/일, 유속 기준 KDS 61 40 00:2022 오수 0.6~3.0·우수/합류 0.8~3.0 m/s)")
            _write_block(ws, cap, 3)
            _autowidth(ws, list(cap.columns))

        ws = _sheet(writer, "8_검토경고")
        wdf = pd.DataFrame({"검토사항": res.warnings or ["특이사항 없음"]})
        _write_block(ws, wdf, 1)
        ws.column_dimensions["A"].width = 110

        for name in list(wb.sheetnames):
            if name == "Sheet":
                del wb[name]
    return buf.getvalue()


def export_excel(res: ProjectResult, path: str | Path, project_name: str = "노후하수관로 정비사업") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_workbook(res, project_name))
    return path
