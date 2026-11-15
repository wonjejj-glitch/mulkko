"""결함 '개수 집계형' 조서 변환기.

지원 양식
  1) 설계 수량산출서 비굴착보수공 조서(실제 사업·실제 사업 8.0 비굴착보수공): 머리글 1행 + A/B/C 1행
  2) 2015 매뉴얼 '하수관로 정밀조사 집계표(이상항목집계표 포함)': 머리글 3~4행에 걸친 다단 머리글
관로 1행에 결함유형별 A/B/C(또는 대/중/소) 개수가 펼쳐진 표를, 시트마다 열 순서가 달라도 머리글로 찾아 읽는다.
맨홀 결함 열(구조 및 운영적 내부결함(맨홀) 등)과 소계 열은 제외한다.

매뉴얼 판별: 결함 항목명으로 시트별 자동 판별(2015 고유 항목: 관침하·관천공·관파손·변형(강성관/연성관)·
기타장애물(영구/임시)·이음부결함·관단절 → 2015, 그 외 → 2017). --manual로 지정하면 그 값을 쓴다.
예) 실제 사업 수량산출서: CASE1 시트(설계 추가 CCTV) = 2017, CASE2 시트(2016 정밀조사) = 2015

결함 '위치(m)'가 없으므로 CCTV 주행구간 안에 균등 간격으로 임시 배치한다(조서에 '위치추정' 표시, 근접결함 병합 제외).
원본의 설계 판정(보수형식·보수수량)은 '설계보수형식/설계보수수량' 열로, 조사기관의 상태등급(2015 집계표)은
'조사기관 구조상태(최대/평균)' 열로 보존되어 대조·검증에 쓰인다.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

# 원본 결함 유형명 → 2017 기준 결함코드 (먼저 일치하는 항목 적용). PF·TM은 2015 전용.
LABEL_MAP: list[tuple[str, str]] = [
    ("연결관돌출", "LP"), ("연결관접합", "LS"), ("이음부이탈", "JS"), ("이음부단차", "JD"), ("이음부손상", "JF"),
    ("이음부결함", "JF"), ("균열(길이)", "CL"), ("균열(원주)", "CC"), ("균열(복합)", "CM"), ("균열", "CL"),
    ("표면손상", "SD"), ("부식", "SD"), ("라이닝", "LD"), ("좌굴", "BC"), ("변형(연성관)", "PF"), ("변형", "DF"),
    ("파손", "BK"), ("천공", "HL"), ("단절", "TM"), ("붕괴", "CX"), ("장애물(영구)", "PO"), ("영구장애물", "PO"),
    ("장애물(임시)", "TO"), ("임시장애물", "TO"), ("침하", "SG"), ("역경사", "NS"), ("내피", "DE"), ("침입수", "IF"),
    ("뿌리", "RI"), ("퇴적", "DS"), ("폐유", "DG"), ("기름", "DG"), ("장애물", "TO"),
]
OPERATIONAL_STATE = {"DE", "DS", "DG", "TO", "RI", "IF"}   # 2017 기준 운영적(등급은 운영등급 열로 기록)
# 2017 코드 → 다른 매뉴얼 코드 (같은 코드도 매뉴얼마다 뜻이 달라 변환 필요: 예 DE = 2017 내피생성 / 2015 토사퇴적)
TO_MANUAL = {
    "2015": {"DE": "ED", "DS": "DE", "BK": "PB", "CX": "PX", "SG": "DP", "NS": "RF", "HL": "PH", "LD": "PL",
             "PO": "OP", "TO": "OT", "LS": "LF", "JS": "JO", "IF": "IP"},
    "2025": {"RI": "RT"},
}
LABELS_2015 = {"관침하", "관천공", "관파손", "관단절", "관붕괴", "변형(강성관)", "변형(연성관)", "기타장애물(영구)",
               "기타장애물(임시)", "이음부결함"}
MATERIAL_MAP = {"HP": "흄관", "흄관(HP)": "흄관", "흄관": "흄관", "H.P.": "흄관", "PE": "PE관", "GRP": "GRP관",
                "CIPP": "CIPP관", "CSP": "CSP관", "PSP": "PSP관", "PVC": "PVC관", "VR": "VR관", "CP": "콘크리트관"}
GRADE = {"A": 5, "B": 4, "C": 3, "대": 5, "중": 4, "소": 3}
GRADE_HEADS = (("A", "B", "C"), ("대", "중", "소"))
STATE_NAMES_2015 = {"매우좋음": 1, "좋음": 2, "양호": 3, "부실": 4, "불량": 5}


def _norm(v: Any) -> str:
    return re.sub(r"\s+", "", str(v if v is not None else ""))


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
        return float(v)
    s = re.sub(r"[^0-9.\-]", "", str(v or ""))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _id(v: Any) -> str:
    if v is None or str(v).strip() in ("", "#N/A", "0"):
        return ""
    n = _num(v)
    return str(int(n)) if n is not None and float(n).is_integer() and re.fullmatch(r"[\d.\s]+", str(v)) else str(v).strip()


def _method(v: Any) -> str:
    """원본 보수형식 표기('전체보수','전체','부분' 등)를 '전체보수'/'부분보수'로 통일. 그 외는 빈 값."""
    s = _norm(v)
    return "전체보수" if s.startswith("전체") else ("부분보수" if s.startswith("부분") else "")


def map_label(label: str) -> str | None:
    t = _norm(label).replace("(관로)", "")
    for key, code in LABEL_MAP:
        if key in t:
            return code
    return None


def detect_manual(labels: list[str]) -> str:
    """결함 항목명으로 매뉴얼 판별(2015 고유 항목이 있으면 2015, 아니면 2017)."""
    norm = {_norm(x).replace("(관로)", "") for x in labels}
    return "2015" if norm & LABELS_2015 else "2017"


@dataclass
class CountRecord:
    pipe_id: str
    cctv_id: str
    zone: str
    material: str
    diameter: float
    length: float
    run: float | None
    run_pct: float | None
    design_method: str
    design_sites: float | None
    from_mh: str
    to_mh: str
    sheet: str
    counts: dict[tuple[str, str, int], int] = field(default_factory=dict)   # (원본유형, 2017코드, 등급) → 개수
    manual: str = ""
    agency: dict[str, str] = field(default_factory=dict)                     # 조사기관 상태등급(2015 집계표)


@dataclass
class SheetLayout:
    header_row: int
    data_start: int
    fields: dict[str, int]
    groups: list[tuple[int, str, str | None]]     # (A열 위치, 원본 유형명, 2017코드)
    singles: list[tuple[int, str, str]]           # 등급 구분 없는 단일열 결함(관단절·관붕괴) → '대'로 계상
    manual: str = "2017"


def detect_layout(rows: list[tuple]) -> SheetLayout | None:
    """'관경' 머리글 행과 그 아래(최대 5행) A/B/C 하위머리글 행을 찾아 열 배치를 만든다(다단 머리글 지원)."""
    n = len(rows)
    for h in range(min(12, n - 1)):
        top = [_norm(v) for v in rows[h]]
        if not any(x.startswith("관경") for x in top):
            continue
        sub_idx = None
        for s in range(h + 1, min(h + 6, n)):
            sub = [_norm(v) for v in rows[s]] + [""] * 3
            if any(tuple(sub[j:j + 3]) in GRADE_HEADS for j in range(len(sub) - 2)):
                sub_idx = s
                break
        if sub_idx is None:
            continue
        hdr = [[_norm(v) for v in rows[i]] for i in range(h, sub_idx)]
        sub = [_norm(v) for v in rows[sub_idx]] + [""] * 3
        width = max([len(r) for r in hdr] + [len(sub)])

        def labs(j: int) -> list[str]:
            return [r[j] for r in hdr if j < len(r) and r[j]]

        def parent(j: int) -> str:                      # 맨 윗 머리글 행에서 j 이하 가장 가까운 라벨
            row = hdr[0]
            for k in range(min(j, len(row) - 1), -1, -1):
                if row[k]:
                    return row[k]
            return ""

        f: dict[str, int] = {}
        for j in range(width):
            for lb in labs(j):
                up = lb.upper()
                if "GIS" in up and "관로" in lb:
                    f["pipe_id"] = j
                elif lb == "관로번호":
                    f.setdefault("pipe_id_plain", j)
                elif "CCTV" in up and "관로" in lb:
                    f["cctv_id"] = j
                elif (lb in ("분구", "처리분구") or "배수분구" in lb) and "zone" not in f:
                    f["zone"] = j
                elif lb == "보수형식":
                    f["method"] = j
                elif lb == "보수구분":
                    f.setdefault("method", j)
                elif lb.startswith("관종"):
                    f.setdefault("material", j)
                elif lb.startswith("관경"):
                    f.setdefault("diameter", j)
                elif lb in ("연장", "연장(m)"):
                    f.setdefault("length", j)
                elif lb == "주행거리" or lb.startswith("조사길이"):
                    f.setdefault("run", j)
                elif lb.startswith("주행율"):
                    f.setdefault("run_pct", j)
                elif lb == "적용보수수량":
                    f["sites"] = j
                elif lb in ("보수개소(반영)", "보수수량"):
                    f.setdefault("sites", j)
                elif lb in ("맨홀", "맨홀번호") and "from_mh" not in f:
                    f["from_mh"], f["to_mh"] = j, j + 1
                elif lb.startswith("본관구조적상태등급"):
                    f["ag_struct_max"], f["ag_struct_avg"] = j, j + 1
                elif lb.startswith("본관운영적상태등급"):
                    f["ag_oper_max"], f["ag_oper_avg"] = j, j + 1
        if "pipe_id" not in f:
            f["pipe_id"] = f.get("pipe_id_plain", f.get("cctv_id", -1))
        f.pop("pipe_id_plain", None)

        groups, singles, taken = [], [], set()
        for j in range(width):
            if tuple(sub[j:j + 3]) not in GRADE_HEADS:
                continue
            lab = next((r[j] for r in reversed(hdr) if j < len(r) and r[j]), "")
            if not lab or lab in ("계", "소계") or lab.startswith("맨홀") or "맨홀" in parent(j):
                continue
            groups.append((j, lab, map_label(lab)))
            taken.update({j, j + 1, j + 2})
        for j in range(width):
            if j in taken:
                continue
            lab = next((r[j] for r in reversed(hdr) if j < len(r) and r[j]), "")
            code = map_label(lab) if lab else None
            if code in ("TM", "CX") and not sub[j] and "맨홀" not in parent(j):
                singles.append((j, lab, code))
        if f.get("pipe_id", -1) >= 0 and "diameter" in f and groups:
            manual = detect_manual([g[1] for g in groups] + [s[1] for s in singles])
            return SheetLayout(h, sub_idx + 1, f, groups, singles, manual)
    return None


def read_count_workbook(path, sheets: list[str] | None = None,
                        manual: str | None = None) -> tuple[list[CountRecord], list[str]]:
    """집계형 조서 엑셀에서 관로별 결함 개수 레코드를 읽는다(시트·머리글·매뉴얼 자동 판별)."""
    wb = load_workbook(path, read_only=True, data_only=True)
    out: list[CountRecord] = []
    log: list[str] = []
    unmapped: set[str] = set()
    for ws in wb.worksheets:
        if sheets and ws.title not in sheets:
            continue
        rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
        lay = detect_layout(rows)
        if lay is None:
            continue
        f = lay.fields
        sheet_manual = manual or lay.manual

        def get(r, k, default=None):
            j = f.get(k)
            return r[j] if j is not None and 0 <= j < len(r) else default

        ok = skipped = 0
        sheet_method = "전체보수" if "전체" in ws.title else ("부분보수" if "부분" in ws.title else "")
        for r in rows[lay.data_start:]:
            dia, pid = _num(get(r, "diameter")), _id(get(r, "pipe_id"))
            length = _num(get(r, "length")) or _num(get(r, "run"))
            if not pid or not dia or not length:
                if any(v not in (None, "", 0, "#N/A") for v in r[:12]):
                    skipped += 1
                continue
            mat_raw = str(get(r, "material") or "").strip()
            rec = CountRecord(
                pipe_id=pid, cctv_id=_id(get(r, "cctv_id")), zone=str(get(r, "zone") or "").strip(),
                material=MATERIAL_MAP.get(mat_raw, mat_raw or "흄관"), diameter=dia, length=length,
                run=_num(get(r, "run")), run_pct=_num(get(r, "run_pct")),
                design_method=_method(get(r, "method")) or sheet_method, design_sites=_num(get(r, "sites")),
                from_mh=_id(get(r, "from_mh")), to_mh=_id(get(r, "to_mh")), sheet=ws.title, manual=sheet_manual)
            for key in ("ag_struct_max", "ag_struct_avg", "ag_oper_max", "ag_oper_avg"):
                v = _norm(get(r, key))
                if v:
                    rec.agency[key] = v
            for j, label, code in lay.groups:
                for k, g in enumerate("ABC"):
                    cnt = _num(r[j + k] if j + k < len(r) else None)
                    if cnt and cnt > 0:
                        if code is None:
                            unmapped.add(label)
                            continue
                        key = (label, code, GRADE[g])
                        rec.counts[key] = rec.counts.get(key, 0) + int(round(cnt))
            for j, label, code in lay.singles:
                cnt = _num(r[j] if j < len(r) else None)
                if cnt and cnt > 0:
                    rec.counts[(label, code, 5)] = rec.counts.get((label, code, 5), 0) + int(round(cnt))
            out.append(rec)
            ok += 1
        log.append(f"[{ws.title}] 머리글 {lay.header_row + 1}~{lay.data_start}행 · 결함유형 {len(lay.groups) + len(lay.singles)}종 · "
                   f"{sheet_manual} 매뉴얼{'(자동판별)' if not manual else ''} · 관로 {ok}개"
                   + (f" · 값 누락(#N/A 등)으로 제외 {skipped}행" if skipped else ""))
    if unmapped:
        log.append("표준코드로 매핑하지 못한 결함유형(제외): " + ", ".join(sorted(unmapped)))
    return out, log


def merge_records(recs: list[CountRecord]) -> tuple[list[CountRecord], list[str]]:
    """같은 관로가 여러 시트에 있으면 합친다.
    같은 매뉴얼: 유형·등급별 최댓값(중복 계상 방지) / 다른 매뉴얼: 최신 매뉴얼 레코드를 채택(코드 체계가 달라 합산 불가)."""
    by: dict[str, CountRecord] = {}
    log: list[str] = []
    dup = cross = 0
    for r in recs:
        if r.pipe_id not in by:
            by[r.pipe_id] = r
            continue
        base = by[r.pipe_id]
        if base.manual != r.manual:
            cross += 1
            if r.manual > base.manual:
                r.sheet = base.sheet + "→" + r.sheet
                by[r.pipe_id] = r
            continue
        dup += 1
        for k, n in r.counts.items():
            base.counts[k] = max(base.counts.get(k, 0), n)
        if "전체" in r.design_method and "전체" not in base.design_method:
            base.design_method = r.design_method
        base.sheet += "+" + r.sheet
    if dup:
        log.append(f"여러 시트에 중복된 관로 {dup}건 병합(유형·등급별 최댓값)")
    if cross:
        log.append(f"서로 다른 매뉴얼 시트에 중복된 관로 {cross}건 - 최신 매뉴얼 조사 채택")
    return list(by.values()), log


def write_survey(recs: list[CountRecord], path: str | Path, manual: str | None = None) -> Path:
    """표준 조서(관로제원·결함내역)로 저장. 결함 위치는 주행구간 균등 배치(추정).
    관로마다 레코드의 매뉴얼(또는 지정 manual) 코드로 기록하고 '적용매뉴얼' 열을 채운다."""
    wb = Workbook()
    ws = wb.active
    ws.title = "관로제원"
    ws.append(["관번", "시점맨홀", "종점맨홀", "관종", "관경(mm)", "도면연장(m)", "촬영연장(m)", "처리분구",
               "CCTV관로번호", "주행거리(m)", "주행율(%)", "설계보수형식", "설계보수수량", "출처시트", "적용매뉴얼",
               "조사기관 구조상태(최대)", "조사기관 구조상태(평균)", "조사기관 운영상태(최대)", "조사기관 운영상태(평균)"])
    ws2 = wb.create_sheet("결함내역")
    ws2.append(["관번", "거리(m)", "결함코드", "결함내용", "구조등급", "운영등급", "비고"])
    for r in recs:
        m = manual or r.manual or None
        trans = TO_MANUAL.get(str(m or ""), {})
        # 촬영연장 = 도면연장(결함은 도면 기준 조사구간 0~주행거리 안에 배치, 연장보정 없음).
        # 주행거리(조사길이)는 별도 열로 기록 → 2015 평균값 분모로 사용
        run = r.run if r.run and 0 < r.run <= r.length else r.length
        ws.append([r.pipe_id, r.from_mh or "-", r.to_mh or "-", r.material, int(r.diameter), r.length, r.length, r.zone,
                   r.cctv_id, r.run, r.run_pct, r.design_method, r.design_sites, r.sheet, m,
                   r.agency.get("ag_struct_max"), r.agency.get("ag_struct_avg"),
                   r.agency.get("ag_oper_max"), r.agency.get("ag_oper_avg")])
        items = [(label, code, g) for (label, code, g), n in sorted(r.counts.items(), key=lambda kv: -kv[0][2]) for _ in range(n)]
        if not items:
            continue
        step = run / len(items)
        for i, (label, code, g) in enumerate(items):
            pos = round((i + 0.5) * step, 2)
            struct = code not in OPERATIONAL_STATE
            ws2.append([r.pipe_id, pos, trans.get(code, code), label, g if struct else None, None if struct else g, "위치추정(균등배치)"])
    for w in (ws, ws2):
        for c in w[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="DDEBF7")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def read_aggregate_full(src) -> dict[int, float]:
    """워크북 안의 수량 집계표에서 '전체보수' 관경별 연장(m)을 찾는다(없으면 빈 dict).
    형식: | 전체보수 | 300 | m | 1317.1 | … (공종명은 첫 행에만, 다음 행은 빈칸)"""
    wb = load_workbook(src, read_only=True, data_only=True)
    for ws in wb.worksheets:
        if "집계" not in ws.title:
            continue
        agg: dict[int, float] = {}
        in_full = False
        for r in ws.iter_rows(values_only=True):
            if len(r) < 4:
                continue
            head = _norm(r[0])
            if head:
                in_full = head.startswith("전체보수")
            if in_full and _num(r[1]) and _norm(r[2]) == "m" and _num(r[3]) is not None:
                agg[int(_num(r[1]))] = agg.get(int(_num(r[1])), 0.0) + float(_num(r[3]))
        if agg:
            return agg
    return {}


def cross_check(src, recs: list[CountRecord], tol: float = 0.10) -> list[str]:
    """변환된 '전체보수' 관로 연장을 같은 워크북의 집계표와 관경별로 비교한다.
    크게 어긋나면 상세 시트가 다른 사업의 서식 잔존 데이터일 수 있다
    (실제 사업 8.0 비굴착보수공의 상세 시트가 실제 사업(실제 사업) 데이터였던 실제 사례)."""
    agg = read_aggregate_full(src)
    if not agg or sum(agg.values()) <= 0:
        return ["집계표 교차검산: 워크북에서 '전체보수' 관경별 집계를 찾지 못해 생략"]
    got: dict[int, float] = {}
    for r in recs:
        if r.design_method == "전체보수":
            got[int(r.diameter)] = got.get(int(r.diameter), 0.0) + r.length
    total_a, total_g = sum(agg.values()), sum(got.values())
    worst = sorted(((abs(got.get(d, 0) - a), d, a, got.get(d, 0)) for d, a in agg.items() if a > 0), reverse=True)[:3]
    if total_a and abs(total_g - total_a) / total_a <= tol and all(abs(g - a) <= max(tol * a, 1.0) for _, _, a, g in worst):
        return [f"집계표 교차검산 통과: 전체보수 {total_g:,.1f}m / 집계 {total_a:,.1f}m"]
    detail = ", ".join(f"D{d} 집계 {a:,.1f}m↔변환 {g:,.1f}m" for _, d, a, g in worst)
    return [f"⚠ 집계표 교차검산 불일치: 변환 전체보수 {total_g:,.1f}m vs 집계 {total_a:,.1f}m ({detail}) "
            f"- 상세 시트가 이 사업의 데이터가 아닐 수 있습니다(구역·분구·관경 분포 확인 필요). 대조 결과를 사용하지 마십시오."]


def zone_summary(recs: list[CountRecord]) -> str:
    from collections import Counter
    zones = Counter(r.zone or "(미기재)" for r in recs).most_common(6)
    dias = Counter(int(r.diameter) for r in recs).most_common(4)
    mans = Counter(r.manual or "-" for r in recs).most_common()
    return ("변환 대상 분구: " + ", ".join(f"{z} {n}" for z, n in zones)
            + " · 주요 관경: " + ", ".join(f"D{d} {n}" for d, n in dias)
            + " · 매뉴얼: " + ", ".join(f"{m} {n}" for m, n in mans))


def convert_count_workbook(src: str | Path, out: str | Path, sheets: list[str] | None = None,
                           manual: str | None = None) -> tuple[Path, list[str], int]:
    recs, log = read_count_workbook(src, sheets, manual)
    recs, log2 = merge_records(recs)
    log2.append(zone_summary(recs))
    log2.extend(cross_check(src, recs))
    partial_run = sum(1 for r in recs if r.run and r.run < r.length * 0.98)
    if partial_run:
        log2.append(f"주행거리 < 연장인 관로 {partial_run}개: 미조사 구간의 결함은 반영되지 않음")
    path = write_survey(recs, out, manual)
    return path, log + log2, len(recs)
