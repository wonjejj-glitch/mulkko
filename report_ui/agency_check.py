"""조사기관 상태등급 대조: 2015 정밀조사 집계표에 기록된 '본관 구조적 상태등급(평균)'과 도구 2015 계산 비교.

평균값(총점/조사길이)은 결함 위치와 무관하므로 집계형 자료로도 정확히 비교된다.
최대값(1m 합산)은 결함 위치가 필요해 단일 결함 최대점수(하한)로만 비교한다.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

from common.manuals import load_manual
from defect_processor.count_converter import STATE_NAMES_2015, TO_MANUAL, read_count_workbook
from rehab_decision_ve.scoring import round_half_up

LV = {5: "대", 4: "중", 3: "소"}
FLEX = ("PE", "PVC", "GRP", "CSP", "폴리", "FRP")
NAMES = {v: k for k, v in STATE_NAMES_2015.items()}


def _band(v: float, bands) -> int:
    for lo, gr in bands:
        if v >= lo - 1e-9:
            return int(gr)
    return 1


def check_survey_grades(survey_path: str | Path, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """결함 위치가 있는 조서(예: CCTV 보고서 PDF 추출본)로 도구 상태등급(최대·평균)을 계산해
    조서의 '조사기관 구조상태(최대/평균)' 열과 비교한다. 최대값(1m 합산)까지 정확히 검증된다."""
    import copy

    from common.manuals import manual_view
    from defect_processor.calibration import calibrate_pipe
    from defect_processor.loader import load_survey
    from rehab_decision_ve.scoring import evaluate

    raw = pd.read_excel(survey_path, sheet_name="관로제원", dtype=object)
    agency = {str(r["관번"]).strip(): r for _, r in raw.iterrows()}
    pipes, _w = load_survey(survey_path, cfg)
    rows = []
    for p in copy.deepcopy(pipes):
        a = agency.get(p.pipe_id)
        if a is None:
            continue
        pcfg = manual_view(cfg, p.manual)
        calibrate_pipe(p, pcfg)
        ev = evaluate(p, pcfg)
        names = pcfg["condition_grading"].get("grade_names", {})
        ag_max = str(a.get("조사기관 구조상태(최대)") or "").strip()
        ag_avg = str(a.get("조사기관 구조상태(평균)") or "").strip()
        tool_max = names.get(ev["condition_grade"], ev["condition_grade"])
        tool_avg = names.get(str(ev.get("struct_avg_grade", "")), "")
        rows.append({"관번": p.pipe_id, "매뉴얼": pcfg["manual"], "결함수": len(p.defects),
                     "도구 최대값(점)": ev.get("struct_max"), "도구 최대등급": tool_max, "조사기관 최대등급": ag_max,
                     "최대 일치": tool_max == ag_max, "도구 평균값": ev.get("struct_avg"), "도구 평균등급": tool_avg,
                     "조사기관 평균등급": ag_avg, "평균 일치": tool_avg == ag_avg})
    df = pd.DataFrame(rows)
    ok = df[(df["조사기관 최대등급"] != "") & (df["조사기관 최대등급"] != "nan")] if len(df) else df
    return df, {"n": len(ok), "max_match": float(ok["최대 일치"].mean() * 100) if len(ok) else 0.0,
                "avg_match": float(ok["평균 일치"].mean() * 100) if len(ok) else 0.0}


def check_agency(paths: list[str | Path]) -> tuple[pd.DataFrame, dict]:
    man = load_manual("2015")
    codes, g = man["defect_codes"], man["condition_grading"]
    rows = []
    for path in paths:
        recs, _log = read_count_workbook(path, manual="2015")
        for r in recs:
            ag_avg = STATE_NAMES_2015.get(r.agency.get("ag_struct_avg", ""))
            if not ag_avg:
                continue
            flex = any(k in r.material.upper() for k in FLEX)
            total = smax = 0.0
            for (_lab, c17, gr), n in r.counts.items():
                code = TO_MANUAL["2015"].get(c17, c17)
                if code == "DF" and flex:
                    code = "PF"
                spec = codes.get(code)
                if spec is None or spec["state"] != "structural":
                    continue
                s = spec["scores"][LV[gr]]
                total += s * n
                smax = max(smax, s)
            survey = r.run or r.length
            avg = round_half_up(total / survey, int(g.get("avg_round", 2))) if survey else 0.0
            tool_avg = _band(avg, g["struct_avg_bands"])
            ag_max = STATE_NAMES_2015.get(r.agency.get("ag_struct_max", ""))
            lower_max = _band(smax, g["struct_max_bands"])
            rows.append({"관로번호": r.pipe_id, "출처": f"{Path(str(path)).name}:{r.sheet}", "조사길이(m)": survey,
                         "구조 총점": total, "평균값": avg, "도구 평균등급": NAMES[tool_avg],
                         "조사기관 평균등급": NAMES[ag_avg], "평균 일치": tool_avg == ag_avg,
                         "단일결함 최대점수": smax, "조사기관 최대등급": NAMES.get(ag_max, ""),
                         "최대 하한 모순": bool(ag_max and lower_max > ag_max)})
    df = pd.DataFrame(rows)
    summary = {"n": len(df), "avg_match": float(df["평균 일치"].mean() * 100) if len(df) else 0.0,
               "max_contradiction": int(df["최대 하한 모순"].sum()) if len(df) else 0,
               "by_grade": Counter(zip(df["조사기관 평균등급"], df["도구 평균등급"])) if len(df) else Counter()}
    return df, summary
