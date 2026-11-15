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


def observed_unit_scores(pipe, cfg: dict, boundaries: list[float]) -> tuple[float, float, int]:
    """보고서에 표시된 실제 이음부를 본 경계로 사용. 이음부 결함은 전·후 본에 적용."""
    import bisect
    import math
    from rehab_decision_ve.scoring import band, section_score
    joints = sorted(set(float(x) for x in boundaries if math.isfinite(float(x)) and 0 < float(x) < pipe.plan_length_m))
    scores = []
    for state in ("structural", "operational"):
        worst = [0] * (len(joints)+1)
        for d in pipe.defects:
            if d.state != state:
                continue
            a = bisect.bisect_right(joints, d.start_cal)
            b = bisect.bisect_right(joints, d.end_cal)
            units = set(range(a,b+1))
            if d.code in cfg["condition_grading"].get("joint_codes", []) and d.end_distance_cal is None:
                for k,x in enumerate(joints):
                    if abs(d.distance_cal-x) <= 1e-6:
                        units.update((k,k+1))
            for k in units:
                worst[k] = max(worst[k],d.score)
        scores.append(section_score([band(x,cfg) for x in worst]))
    return scores[0], scores[1], len(joints)+1


def check_survey_grades(survey_path: str | Path, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """2017 평균 점수는 ±0.05, 2015 문자 등급은 동등 비교. 미확정 위치 관로는 집계 제외."""
    import copy
    import json
    from common.manuals import manual_view
    from defect_processor.calibration import calibrate_pipe
    from defect_processor.loader import load_survey, read_tables
    from rehab_decision_ve.scoring import evaluate

    raw = pd.read_excel(survey_path, sheet_name="관로제원", dtype=object)
    agency = {str(r["관번"]).strip(): r for _, r in raw.iterrows()}
    _, defects = read_tables(survey_path, cfg)
    missing = set(defects.loc[pd.to_numeric(defects["distance_m"], errors="coerce").isna(), "pipe_id"].astype(str)) if len(defects) else set()
    pipes, _w = load_survey(survey_path, cfg)
    def clean(v):
        return "" if pd.isna(v) else str(v).strip()
    def number(v):
        try:
            x = float(v)
            return x if pd.notna(x) else None
        except (ValueError, TypeError):
            return None
    rows = []
    for p in copy.deepcopy(pipes):
        a = agency.get(p.pipe_id)
        if a is None:
            continue
        pcfg = manual_view(cfg, p.manual)
        calibrate_pipe(p, pcfg)
        ev = evaluate(p, pcfg)
        names = pcfg["condition_grading"].get("grade_names", {})
        ag_max = clean(a.get("조사기관 구조상태(최대)"))
        ag_avg = clean(a.get("조사기관 구조상태(평균)"))
        ag_oper = clean(a.get("조사기관 운영상태(평균)"))
        tool_max = names.get(ev["condition_grade"], ev["condition_grade"])
        tool_avg = names.get(str(ev.get("struct_avg_grade", "")), "")
        valid = p.pipe_id not in missing
        row = {"관번":p.pipe_id,"매뉴얼":pcfg["manual"],"결함수":len(p.defects),
               "검증상태":"대조 가능" if valid else "위치 미확정: 대조 제외",
               "도구 최대값(점)":ev.get("struct_max"),"도구 최대등급":tool_max,"조사기관 최대등급":ag_max,
               "최대 일치":tool_max == ag_max if ag_max and valid else None,
               "도구 평균값":ev.get("struct_avg"),"도구 평균등급":tool_avg,
               "조사기관 평균등급":ag_avg,"평균 일치":tool_avg == ag_avg if ag_avg and valid else None}
        if pcfg["condition_grading"].get("method") == "unit_max_avg":
            struct, oper = ev["condition_score"],ev["oper_score"]
            basis,n_units = "기본 관종별 본 길이",None
            raw_bounds = clean(a.get("관 본 경계(m)"))
            if raw_bounds:
                bounds = json.loads(raw_bounds)
                struct,oper,n_units = observed_unit_scores(p,pcfg,bounds)
                basis = "보고서 이음부 표기 기준(누락 가능)"
            ag_s, ag_o = number(ag_avg),number(ag_oper)
            row.update({"도구 평균값":struct,"도구 평균등급":None,"조사기관 평균등급":ag_s,
                        "구조 차이":round(struct-ag_s,2) if ag_s is not None and valid else None,
                        "평균 일치":abs(struct-ag_s)<=0.050000001 if ag_s is not None and valid else None,
                        "도구 운영평균":oper,"조사기관 운영평균":ag_o,
                        "운영 차이":round(oper-ag_o,2) if ag_o is not None and valid else None,
                        "운영 일치":abs(oper-ag_o)<=0.050000001 if ag_o is not None and valid else None,
                        "본수":n_units,"본 경계 기준":basis,
                        "기본간격 구조평균":ev["condition_score"],"기본간격 운영평균":ev["oper_score"]})
        if not valid:
            for col in ("도구 최대값(점)","도구 최대등급","도구 평균값","도구 평균등급","도구 운영평균",
                        "기본간격 구조평균","기본간격 운영평균"):
                if col in row:
                    row[col] = None
        rows.append(row)
    df = pd.DataFrame(rows)
    def result(column):
        v = df[column].dropna() if column in df else pd.Series(dtype=bool)
        return len(v),float(v.astype(bool).mean()*100) if len(v) else 0.0
    nmax,maxrate=result("최대 일치"); ns,srate=result("평균 일치"); no,orate=result("운영 일치")
    cols = [c for c in ("최대 일치","평균 일치","운영 일치") if c in df]
    n = int(df[cols].notna().any(axis=1).sum()) if cols else 0
    return df,{"n":n,"max_n":nmax,"avg_n":ns,"oper_n":no,"max_match":maxrate,"avg_match":srate,"oper_match":orate}


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
