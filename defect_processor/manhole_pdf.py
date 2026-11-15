"""맨홀 현장조사 결과보고서 PDF(조사 소프트웨어 출력, 텍스트 PDF) → 맨홀 조서 + 조사기관 등급 검증.

한 페이지 = 관로 1개의 맨홀부(상·하류 맨홀 제원) + 맨홀 결함 사진 설명 + 조사기관 맨홀 상태등급.
사진이 많으면 다음 페이지로 이어지므로(맨홀쌍·등급 동일) 한 기록으로 합친다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rehab_decision_ve.manhole import decide_repair, defect_score, grade_manhole, state_name

UP_SEG = re.compile(r"상류맨홀 \| (.+?) \| 하류맨홀 \| ")
DOWN_SEG = re.compile(r"하류맨홀 \| (.+?) \| 상류맨홀좌표")
DEFECT = re.compile(r"\(맨홀\)([^()|]+)\(([대중소])\) \[(\d\d:\d\d:\d\d)\]")
EVENT = re.compile(r"\(상태\)([^|]+?) \[\d\d:\d\d:\d\d\]")
PIPE = re.compile(r"관로 \| (?P<mat>[^|]+?) \| (?P<dia>[\d.]+) \| (?P<dr>[^|]+?) \| (?P<len>[\d.]+)m \| (?P<run>[\d.]+)m \| (?P<rest>[\d.]+)m")


@dataclass
class ManholeRecord:
    region: str
    page: int
    pages: int = 1
    zone: str = ""
    sub: str = ""
    date: str = ""
    up_no: str = ""
    down_no: str = ""
    up: tuple = ()        # (종류, 재질, 깊이, 인버트, 크기·사다리)
    down: tuple = ()
    pipe_material: str = ""
    pipe_dia: float = 0.0
    drain: str = ""
    pipe_len: float = 0.0
    agency_struct: float | None = None
    agency_oper: float | None = None
    defects: list = field(default_factory=list)     # (결함명, 등급, 시각)
    events: list = field(default_factory=list)

    @property
    def key(self):
        return (self.up_no, self.down_no, self.agency_struct, self.agency_oper)


def parse_page_text(text: str, region: str = "", page: int = 0) -> ManholeRecord | None:
    t = text.replace("\r", "").replace("\n", " | ")
    t = re.sub(r"(?: \| )+", " | ", t)

    def f(pat):
        m = re.search(pat, t)
        return m.group(1).strip() if m else ""
    sg = re.search(r"조사지점맨홀구조적상태등급\s*([\d.]+)", t)
    og = re.search(r"조사지점맨홀운영적상태등급\s*([\d.]+)", t)
    um, dm = UP_SEG.search(t), DOWN_SEG.search(t)
    if not um and not sg:
        return None
    p = PIPE.search(t)

    def seg(m):       # [번호, 종류, 재질, 깊이, 인버트, 크기·사다리(칸 수 가변)]
        v = [x.strip() for x in m.group(1).split(" | ")] if m else []
        v += [""] * (5 - len(v))
        return v[0], (v[1], v[2], v[3], v[4], " ".join(v[5:]).strip())
    (u_no, u_att), (d_no, d_att) = seg(um), seg(dm)
    return ManholeRecord(
        region=region, page=page, zone=f(r"처리구역 \| (.*?) \|"), sub=f(r"배수분구 \| (.*?) \|"), date=f(r"조사일자 \| (.*?) \|"),
        up_no=u_no, down_no=d_no, up=u_att, down=d_att,
        pipe_material=p["mat"] if p else "", pipe_dia=float(p["dia"]) if p else 0.0, drain=p["dr"] if p else "",
        pipe_len=float(p["len"]) if p else 0.0,
        agency_struct=float(sg.group(1)) if sg else None, agency_oper=float(og.group(1)) if og else None,
        defects=[(n.strip(), lv, tm) for n, lv, tm in DEFECT.findall(t)], events=EVENT.findall(t))


def merge_continuations(recs: list[ManholeRecord]) -> list[ManholeRecord]:
    """같은 맨홀쌍·같은 조사기관 등급이 연속되면 사진 이어짐 면이므로 한 기록으로 합친다."""
    out: list[ManholeRecord] = []
    for r in recs:
        if out and out[-1].region == r.region and out[-1].key == r.key:
            out[-1].defects += r.defects
            out[-1].events += r.events
            out[-1].pages += 1
        else:
            out.append(r)
    return out


def extract_pdfs(paths: list[Path], progress=None) -> list[ManholeRecord]:
    import warnings
    warnings.filterwarnings("ignore")
    import fitz
    recs: list[ManholeRecord] = []
    for pdf in paths:
        region = re.sub(r"^\d+\.", "", pdf.stem).split()[0]
        doc = fitz.open(pdf)
        got = []
        for i, pg in enumerate(doc):
            r = parse_page_text(pg.get_text(), region, i + 1)
            if r:
                got.append(r)
        recs += merge_continuations(got)
        if progress:
            progress(pdf.name, doc.page_count, len(got))
    return recs


def grade_records(recs: list[ManholeRecord]) -> list[dict]:
    rows = []
    for k, r in enumerate(recs, 1):
        g = grade_manhole([(n, lv) for n, lv, _ in r.defects], r.events)
        rows.append({"기록번호": k, "지역": r.region, "페이지": r.page, "처리분구": r.sub, "조사일자": r.date,
                     "상류맨홀": r.up_no, "하류맨홀": r.down_no, "맨홀종류": r.up[0] if r.up else "", "맨홀재질": r.up[1] if r.up else "",
                     "맨홀깊이": r.up[2] if r.up else "", "하류맨홀깊이": r.down[2] if r.down else "", "인버트": r.up[3] if r.up else "", "크기·사다리": r.up[4] if r.up else "",
                     "관종": r.pipe_material, "관경(mm)": r.pipe_dia, "배수방식": r.drain, "관로연장(m)": r.pipe_len,
                     "결함수": len(r.defects), "블록이음부수": g["units"] - 1,
                     "구조적 상태등급(도구)": g["struct_grade"], "구조 최대점수": g["struct_max_score"],
                     "구조 최대값등급(매뉴얼)": g["struct_max_grade"], "운영적 상태등급(도구)": g["oper_grade"], "운영 최대점수": g["oper_score"],
                     "구조적 상태등급(조사기관)": r.agency_struct, "운영적 상태등급(조사기관)": r.agency_oper,
                     "구조 일치": (None if r.agency_struct is None else abs(g["struct_grade"] - r.agency_struct) < 0.06),
                     "운영 일치": (None if r.agency_oper is None else abs(g["oper_grade"] - r.agency_oper) < 0.06),
                     "보수대상": "Y" if g["repair"] else "", "상태": state_name(g["struct_grade"]),
                     "미등록 결함": ",".join(sorted(set(g["unknown"])))})
    return rows


def _depth(v) -> float | None:
    m = re.search(r"[\d.]+", str(v or ""))
    return float(m.group()) if m else None


def repair_rows(recs: list[ManholeRecord], rows: list[dict]) -> list[dict]:
    """맨홀별 보수공법·수량·금액(예시단가). 공법이 없는 맨홀은 제외."""
    out = []
    for r, row in zip(recs, rows):
        names = [(n, lv) for n, lv, _ in r.defects]
        for w in decide_repair(names, row["구조적 상태등급(도구)"], row["운영적 상태등급(도구)"], _depth(r.up[2] if r.up else None)):
            out.append({"기록번호": row["기록번호"], "지역": r.region, "상류맨홀": r.up_no, "하류맨홀": r.down_no,
                        "구조등급": row["구조적 상태등급(도구)"], "운영등급": row["운영적 상태등급(도구)"],
                        "보수대상": row["보수대상"], **w})
    return out


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0, "struct_match": 0.0, "oper_match": 0.0, "struct_match_plain_max": 0.0, "by_region": {}}
    sm = [r["구조 일치"] for r in rows if r["구조 일치"] is not None]
    om = [r["운영 일치"] for r in rows if r["운영 일치"] is not None]
    mx = [abs(r["구조 최대값등급(매뉴얼)"] - r["구조적 상태등급(조사기관)"]) < 0.06 for r in rows
          if r["구조적 상태등급(조사기관)"] is not None]
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["지역"], []).append(r)
    pct = lambda xs: 100 * sum(1 for x in xs if x) / len(xs) if xs else 0.0  # noqa: E731
    return {"n": n, "struct_match": pct(sm), "oper_match": pct(om), "struct_match_plain_max": pct(mx),
            "by_region": {k: (len(v), pct([r["구조 일치"] for r in v if r["구조 일치"] is not None]),
                              pct([r["운영 일치"] for r in v if r["운영 일치"] is not None])) for k, v in by.items()}}


def write_workbook(recs: list[ManholeRecord], rows: list[dict], path: str | Path) -> Path:
    import pandas as pd
    rep = repair_rows(recs, rows)
    drows = []
    for k, r in enumerate(recs, 1):
        for n, lv, tm in r.defects:
            sc = defect_score(n, lv)
            drows.append({"기록번호": k, "지역": r.region, "상류맨홀": r.up_no, "하류맨홀": r.down_no, "결함명": n, "등급": lv,
                          "구분": {"structural": "구조", "operational": "운영"}.get(sc[1], "미등록") if sc else "미등록",
                          "결함점수": sc[0] if sc else None, "시각": tm})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    s = summarize(rows)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame([("맨홀 기록 수", s["n"]), ("구조적 등급 일치율(%) - 단위구간 평균", round(s["struct_match"], 1)),
                      ("구조적 등급 일치율(%) - 매뉴얼 최대값만", round(s["struct_match_plain_max"], 1)),
                      ("운영적 등급 일치율(%)", round(s["oper_match"], 1))], columns=["항목", "값"]
                     ).to_excel(w, sheet_name="1_요약", index=False)
        pd.DataFrame([{"지역": k, "기록": v[0], "구조 일치(%)": round(v[1], 1), "운영 일치(%)": round(v[2], 1)}
                      for k, v in s["by_region"].items()]).to_excel(w, sheet_name="1_요약", index=False, startrow=7)
        pd.DataFrame(rows).to_excel(w, sheet_name="2_맨홀조서", index=False)
        pd.DataFrame(drows).to_excel(w, sheet_name="3_맨홀결함", index=False)
        rdf = pd.DataFrame(rep, columns=["기록번호", "지역", "상류맨홀", "하류맨홀", "구조등급", "운영등급", "보수대상", "공법", "단위", "수량", "단가", "금액", "단가근거"])
        rdf.to_excel(w, sheet_name="4_보수공법", index=False)
        if len(rdf):
            from rehab_decision_ve.manhole import repair_rules
            works = repair_rules()["works"]
            sm = rdf.groupby(["공법", "단위"], as_index=False, sort=False).agg(개소=("기록번호", "count"), 수량=("수량", "sum"), 금액=("금액", "sum"))
            sm["단가근거"] = [works.get(k, {}).get("price_basis", "") for k in sm["공법"]]
            sm["설계 수량(참고)"] = [works.get(k, {}).get("design_qty") for k in sm["공법"]]
            sm.to_excel(w, sheet_name="5_공법별수량", index=False)
    return path
