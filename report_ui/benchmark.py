"""실제 설계 판정과 도구 판정의 대조(benchmark) 및 판정기준 보정(calibration).

조서 관로제원 시트의 '설계보수형식'(전체보수/부분보수)·'설계보수수량' 열을 정답으로 사용한다.
"""
from __future__ import annotations

import copy
import itertools
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font

from common.config import with_criteria
from common.models import (M_CIPP, M_CLEAN, M_NONE, M_PARTIAL_DIG, M_PATCH, M_REPLACE, M_UPSIZE, Pipe,
                           ProjectResult)
from common.pipeline import run_pipes
from defect_processor.loader import _build_alias_lookup, _map_columns
from report_ui.excel_report import _autowidth, _write_block

DESIGN_TO_METHOD = {"전체보수": M_CIPP, "부분보수": M_PATCH}
DIG = {M_UPSIZE, M_REPLACE, M_PARTIAL_DIG}


def read_design_labels(survey_path: str | Path) -> pd.DataFrame:
    """조서에서 관번별 설계 판정을 읽는다(없으면 빈 표)."""
    raw = pd.read_excel(survey_path, sheet_name="관로제원", dtype=object)
    lookup = _build_alias_lookup({"pipe_id": ["관번", "관로번호", "PIPE_ID"], "design": ["설계보수형식", "설계공법"],
                                  "sites": ["설계보수수량", "설계보수개소"], "zone": ["처리분구", "분구"]})
    df = raw.rename(columns=_map_columns(list(raw.columns), lookup))
    keep = [c for c in ("pipe_id", "design", "sites", "zone") if c in df.columns]
    df = df[keep].copy()
    df["pipe_id"] = df["pipe_id"].astype(str).str.strip()
    return df


def join_design_with_survey(design_path: str | Path, survey_path: str | Path, out_path: str | Path,
                            key: str = "CCTV관로번호") -> tuple[Path, dict]:
    """설계 판정 조서(convert-count 결과, '설계보수형식' 보유)와 결함 위치 조서(cctv-pdf 결과)를
    CCTV 보고서 관로번호로 이어 붙여, 위치 기반 대조용 조서를 만든다(관번은 설계 쪽 번호 사용)."""
    des = pd.read_excel(design_path, sheet_name="관로제원", dtype=object)
    sp = pd.read_excel(survey_path, sheet_name="관로제원", dtype=object)
    sd = pd.read_excel(survey_path, sheet_name="결함내역", dtype=object)
    norm = lambda v: str(v).strip().split(".")[0] if v is not None and str(v) not in ("nan", "None", "") else ""  # noqa: E731
    des["_k"] = des[key].map(norm) if key in des.columns else ""
    sp["_k"] = sp["관번"].map(norm)
    sd["_k"] = sd["관번"].map(norm)
    keep = ["관번", "설계보수형식", "설계보수수량", "처리분구", "출처시트"]
    m = sp.merge(des[[c for c in keep if c in des.columns] + ["_k"]].rename(columns={"관번": "_design_id"}),
                 on="_k", how="inner")
    m = m.drop_duplicates("_k")
    idmap = dict(zip(m["_k"], m["_design_id"]))
    m["CCTV관로번호"] = m["_k"]
    m["관번"] = m["_design_id"]
    defects = sd[sd["_k"].isin(idmap)].copy()
    defects["관번"] = defects["_k"].map(idmap)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        m.drop(columns=["_k", "_design_id"]).to_excel(w, sheet_name="관로제원", index=False)
        defects.drop(columns=["_k"]).to_excel(w, sheet_name="결함내역", index=False)
    with_key = int((des["_k"] != "").sum())
    return out, {"design_with_key": with_key, "joined": len(m), "defects": len(defects)}


def _cause(design: str, tool: str, d) -> str:
    if DESIGN_TO_METHOD.get(design) == tool:
        return "일치"
    if tool in DIG:
        return "시공불가 결함(Blocker) → 도구는 굴착 판정"
    if tool == M_CIPP and design == "부분보수":
        return ("불량률 기준 초과" if d.defect_rate_pct >= 25 else "손익분기 초과") + " → 도구는 전체보수"
    if tool == M_PATCH and design == "전체보수":
        return "불량률·손익분기 미달 → 도구는 부분보수"
    if tool in (M_CLEAN, M_NONE):
        return "보수대상 구조결함 없음(등급 기준 미달) → 도구는 " + tool
    return "기타"


def compare(res: ProjectResult, labels: pd.DataFrame) -> pd.DataFrame:
    lab = labels.set_index("pipe_id")
    rows = []
    for p in res.pipes:
        if p.pipe_id not in lab.index:
            continue
        info = lab.loc[p.pipe_id]
        design = str(info.get("design") or "").strip()
        if design not in DESIGN_TO_METHOD:
            continue
        d = res.decisions[p.pipe_id]
        sites = pd.to_numeric(info.get("sites"), errors="coerce")
        rows.append({
            "관번": p.pipe_id, "처리분구": info.get("zone"), "관경(mm)": p.diameter_mm, "연장(m)": p.plan_length_m,
            "구조결함수": d.n_structural, "불량률(%)": round(d.defect_rate_pct, 1),
            "부분/전체 비율(%)": round(d.patch_to_full_ratio * 100, 1),
            "설계 공법": design, "설계 보수수량": None if pd.isna(sites) else sites,
            "도구 공법": d.method, "도구 부분보수 개소": d.n_patch_sites if d.method == M_PATCH else None,
            "일치": DESIGN_TO_METHOD[design] == d.method, "차이 원인": _cause(design, d.method, d),
        })
    return pd.DataFrame(rows)


def summarize(cmp: pd.DataFrame) -> dict:
    if cmp.empty:
        return {"n": 0, "agreement": 0.0}
    n = len(cmp)
    nondig = cmp["도구 공법"].isin([M_CIPP, M_PATCH])
    both_patch = cmp[(cmp["설계 공법"] == "부분보수") & (cmp["도구 공법"] == M_PATCH)].dropna(subset=["설계 보수수량"])
    return {
        "n": n,
        "agreement": cmp["일치"].mean() * 100,
        "agreement_nondig": cmp.loc[nondig, "일치"].mean() * 100 if nondig.any() else 0.0,
        "full_recall": cmp.loc[cmp["설계 공법"] == "전체보수", "일치"].mean() * 100 if (cmp["설계 공법"] == "전체보수").any() else 0.0,
        "patch_recall": cmp.loc[cmp["설계 공법"] == "부분보수", "일치"].mean() * 100 if (cmp["설계 공법"] == "부분보수").any() else 0.0,
        "dig": int(cmp["도구 공법"].isin(DIG).sum()),
        "sites_design": float(both_patch["설계 보수수량"].sum()) if len(both_patch) else 0.0,
        "sites_tool": float(both_patch["도구 부분보수 개소"].sum()) if len(both_patch) else 0.0,
    }


def confusion(cmp: pd.DataFrame) -> pd.DataFrame:
    if cmp.empty:
        return pd.DataFrame()
    ct = pd.crosstab(cmp["설계 공법"], cmp["도구 공법"], margins=True, margins_name="계")
    return ct.reset_index().rename(columns={"설계 공법": "설계 \\ 도구"})


BLOCKER_MODES = {"기본": "default", "A등급만": "A_only", "없음(시공성 전환)": "none"}


def blockers_a_only(cfg: dict) -> dict:
    """국부 시공불가 기준을 '대'(A)로만 한정한 설정(붕괴·관로전체 결함은 유지). 모든 매뉴얼에 적용된다."""
    return with_criteria(cfg, blocker_policy="A_only")


def calibrate(pipes: list[Pipe], labels: pd.DataFrame, cfg: dict,
              rates=(20, 30, 40, 50), breakevens=(0.6, 0.75, 0.9),
              blocker_modes=("기본", "A등급만", "없음(시공성 전환)"),
              repair_modes=(False, True), lateral_modes=(False, True),
              manual_rule_modes=(True, False)) -> pd.DataFrame:
    """판정기준 조합별 설계 판정 일치율. 일치율이 높은 순으로 반환.

    탐색 축: 시공불가 정책 × 보수대상(매뉴얼 점수 기준 / 전 등급) × 연결관돌출 계상(절단 / 부분보수)
            × 매뉴얼 전체보수 규칙 적용 여부 × 불량률 × 손익분기
    """
    rows = []
    for mode, all_g, lat, mrule, rate, be in itertools.product(blocker_modes, repair_modes, lateral_modes,
                                                              manual_rule_modes, rates, breakevens):
        c = with_criteria(cfg, blocker_policy=BLOCKER_MODES.get(mode, mode), repair_all_grades=bool(all_g),
                          lateral_as_patch=bool(lat), manual_full_rule=bool(mrule),
                          defect_rate_threshold_pct=float(rate), breakeven_ratio=float(be))
        res = run_pipes(copy.deepcopy(pipes), c)
        cmp = compare(res, labels)
        s = summarize(cmp)
        rows.append({"시공불가 기준": mode, "보수대상": "전 등급" if all_g else "매뉴얼 점수 기준",
                     "연결관돌출": "부분보수 계상" if lat else "돌출관 절단",
                     "매뉴얼 전체보수 규칙": "적용" if mrule else "미적용", "불량률 기준(%)": rate, "손익분기": be,
                     "일치율(%)": round(s["agreement"], 1), "전체보수 재현율(%)": round(s.get("full_recall", 0), 1),
                     "부분보수 재현율(%)": round(s.get("patch_recall", 0), 1), "굴착 판정 수": s.get("dig", 0),
                     "부분보수 개소(도구/설계)": f"{s.get('sites_tool', 0):.0f}/{s.get('sites_design', 0):.0f}"})
    return pd.DataFrame(rows).sort_values(["일치율(%)", "굴착 판정 수"], ascending=[False, True]).reset_index(drop=True)


def export_benchmark(cmp: pd.DataFrame, calib: pd.DataFrame | None, path: str | Path, note: str = "") -> Path:
    s = summarize(cmp)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        wb = w.book
        ws = wb.create_sheet("1_요약")
        ws.cell(row=1, column=1, value="실제 설계 판정 vs 도구 판정 대조").font = Font(bold=True, size=14)
        ws.cell(row=2, column=1, value=note)
        summ = pd.DataFrame([
            ("대조 관로 수", s["n"]), ("공법 일치율(%)", round(s["agreement"], 1)),
            ("설계 전체보수 재현율(%)", round(s.get("full_recall", 0), 1)),
            ("설계 부분보수 재현율(%)", round(s.get("patch_recall", 0), 1)),
            ("도구가 굴착(교체·부분굴착)으로 판정한 관로", s.get("dig", 0)),
            ("부분보수 일치 관로의 보수개소: 설계", s.get("sites_design", 0)),
            ("부분보수 일치 관로의 보수개소: 도구", s.get("sites_tool", 0)),
        ], columns=["항목", "값"])
        r = _write_block(ws, summ, 4, "■ 요약")
        conf = confusion(cmp)
        if not conf.empty:
            r = _write_block(ws, conf, r, "■ 혼동행렬 (행: 설계, 열: 도구)")
        if not cmp.empty:
            cause = cmp["차이 원인"].value_counts().rename_axis("차이 원인").reset_index(name="관로 수")
            _write_block(ws, cause, r, "■ 불일치 원인")
        ws.column_dimensions["A"].width = 44
        for col in "BCDEFGH":
            ws.column_dimensions[col].width = 16
        ws2 = wb.create_sheet("2_관로별대조")
        _write_block(ws2, cmp, 1)
        _autowidth(ws2, list(cmp.columns))
        ws2.freeze_panes = "B2"
        if calib is not None and not calib.empty:
            ws3 = wb.create_sheet("3_기준보정")
            ws3.cell(row=1, column=1, value="판정기준 조합별 일치율 (위가 최적)").font = Font(bold=True)
            _write_block(ws3, calib, 3)
            _autowidth(ws3, list(calib.columns))
        if "Sheet" in wb.sheetnames:
            del wb["Sheet"]
    return Path(path)
