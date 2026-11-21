"""결과 → 보고서용 DataFrame 변환 (Excel 출력과 대시보드 공용)."""
from __future__ import annotations

import pandas as pd

from cad_exporter.annotate import defect_action
from common.models import M_CIPP, M_PARTIAL_DIG, METHODS, ProjectResult
from quantity_takeoff.takeoff import quantities_frame, summarize_quantities


def summary_by_method(res: ProjectResult) -> pd.DataFrame:
    rows = []
    total = sum(d.total_cost for d in res.decisions.values()) or 1.0
    for m in METHODS:
        decs = [d for d in res.decisions.values() if d.method == m]
        pipes = [p for p in res.pipes if res.decisions[p.pipe_id].method == m]
        length = sum(p.plan_length_m for p in pipes)
        cost = sum(d.total_cost for d in decs)
        rows.append({
            "정비공법": m,
            "관로수": len(decs),
            "연장(m)": round(length, 2),
            "부분보수(개소)": sum(d.n_patch_sites for d in decs),
            "부분굴착(개소)": sum(len(d.dig_clusters) for d in decs if d.method == M_PARTIAL_DIG),
            "공사비(원)": round(cost),
            "구성비(%)": round(cost / total * 100, 1),
            "m당 평균(원/m)": round(cost / length) if length else 0,
        })
    df = pd.DataFrame(rows)
    tot = {"정비공법": "합계", "관로수": df["관로수"].sum(), "연장(m)": round(df["연장(m)"].sum(), 2),
           "부분보수(개소)": df["부분보수(개소)"].sum(), "부분굴착(개소)": df["부분굴착(개소)"].sum(),
           "공사비(원)": df["공사비(원)"].sum(), "구성비(%)": 100.0 if df["공사비(원)"].sum() else 0.0,
           "m당 평균(원/m)": round(df["공사비(원)"].sum() / df["연장(m)"].sum()) if df["연장(m)"].sum() else 0}
    return pd.concat([df, pd.DataFrame([tot])], ignore_index=True)


def length_by_diameter(res: ProjectResult) -> pd.DataFrame:
    rows = [{"관경": f"D{p.diameter_mm}", "_d": p.diameter_mm, "정비공법": res.decisions[p.pipe_id].method,
             "연장": p.plan_length_m} for p in res.pipes]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    pv = df.pivot_table(index=["_d", "관경"], columns="정비공법", values="연장", aggfunc="sum", fill_value=0.0)
    pv = pv.reindex(columns=[m for m in METHODS if m in pv.columns])
    pv["합계"] = pv.sum(axis=1)
    pv = pv.round(2).reset_index().drop(columns="_d")
    return pv


def criteria_table(res: ProjectResult) -> pd.DataFrame:
    c = res.config["criteria"]
    used = sorted({d.manual for d in res.decisions.values()})
    from common.manuals import load_manual
    ov = res.config.get("criteria_override", {})          # 사용자가 정한 값(프리셋·화면)이 매뉴얼 기본보다 우선
    def _rs(m, k):
        return ov.get(k, load_manual(m)["criteria"][k])
    items = [("적용 매뉴얼", " / ".join(load_manual(m)["title"] for m in used) or res.config.get("manual_title", "")),
             ("보수대상 결함점수 기준", ", ".join(f"{m}: 구조 {_rs(m, 'repair_min_score')}점·운영 "
                                       f"{_rs(m, 'oper_min_score')}점 이상" for m in used)
              + (" (사용자 조정)" if "repair_min_score" in ov else ""))]
    items += [
        ("부분보수 병합 이격거리", f"{c['merge_gap_m']} m"),
        ("부분보수 양측 여유길이", f"{c['repair_margin_m']} m"),
        ("부분보수 표준규격", ", ".join(f"{s}m" for s in c["partial_repair_sizes_m"])),
        ("불량률 산정방식", "결함 관본수/전체 관본수" if c["defect_rate_method"] == "joint_count" else "보수연장/관로연장"),
        ("전체보수 전환 불량률 기준", f"{c['defect_rate_threshold_pct']} %" if c.get("use_rate_rule", True) else "미적용"),
        ("부분보수/전체보수 손익분기 비율", f"{float(c['breakeven_ratio']) * 100:.0f} %" if c.get("use_breakeven_rule", True) else "미적용"),
        ("매뉴얼 전체보수 규칙(상태점수 4.0 등)", "적용" if c.get("manual_full_rule", True) else "미적용"),
        ("부분굴착 최소연장 / 양측여유", f"{c['partial_excavation_min_length_m']} m / {c['partial_excavation_margin_m']} m"),
        ("굴착이 필요한 결함(시공불가 판단)", {"default": "기본(붕괴·역경사 전 등급, 파손·변형·침하·이음부단차 중 이상, 좌굴·이음부이탈 대)",
                                         "A_only": "대 등급만 굴착(붕괴·역경사는 전 등급)",
                                         "none": "굴착 최소화(붕괴·역경사만 굴착)"}.get(c.get("blocker_policy", "default"), c.get("blocker_policy"))),
        ("부분굴착 허용 개소수(초과 시 굴착교체)", f"{c['max_partial_excavations']} 개소"),
        ("부분굴착 연장비 한도(이상 시 굴착교체)", f"{float(c['partial_excavation_max_length_ratio']) * 100:.0f} %"),
        ("부분굴착+비굴착 / 전체굴착 공사비 한도", f"{float(c['partial_vs_full_excavation_ratio']) * 100:.0f} %"),
        ("연장오차 경고 기준", f"{float(c['length_mismatch_warn_ratio']) * 100:.0f} %"),
        ("제경비율(개략)", f"{float(c.get('overhead_ratio', 0)) * 100:.0f} %"),
    ]
    return pd.DataFrame(items, columns=["판정기준 항목", "적용값"])


def pipe_detail(res: ProjectResult) -> pd.DataFrame:
    rows = []
    for p in res.pipes:
        d = res.decisions[p.pipe_id]
        rows.append({
            "관번": p.pipe_id, "시점맨홀": p.from_mh, "종점맨홀": p.to_mh, "관종": p.material,
            "관경(mm)": p.diameter_mm, "도면연장(m)": p.plan_length_m, "촬영연장(m)": p.cctv_length_m,
            "연장보정계수": round(p.scale_factor, 4), "조사방향": p.survey_direction, "평균심도(m)": p.avg_depth_m,
            "포장": p.pavement, "설계유량(㎥/일)": round(p.design_flow_lps * 86.4) if p.design_flow_lps else None,
            "만관통수능(㎥/일)": round(p.full_capacity_lps * 86.4) if p.full_capacity_lps else None,
            "통수능비": p.capacity_ratio, "통수능부족": "Y" if p.capacity_insufficient else "",
            "적용매뉴얼": d.manual, "구조적 상태점수": d.condition_score, "최대 상태등급": int(d.condition_grade),
            "등급3이상 본비율(%)": d.bad_unit_ratio_pct, "불량비(2015)": d.bad_ratio or None,
            "긴급보수(2015)": "Y" if d.emergency else "", "운영적 상태점수": d.oper_score,
            "상태평가(매뉴얼 표기)": d.state_text, "정비우선순위": d.priority,
            "구조결함(보수대상)": d.n_structural, "운영결함": d.n_operational, "경미결함": d.n_minor,
            "돌출관": d.n_lateral_cut, "불량률(%)": round(d.defect_rate_pct, 1),
            "부분보수 개소(병합후)": sum(c.n_sites for c in (d.evaluated_clusters or d.patch_clusters)),
            "부분보수비(원)": round(d.patch_cost), "전체보수비(원)": round(d.full_cipp_cost),
            "부분/전체 비율(%)": round(d.patch_to_full_ratio * 100, 1),
            "정비공법": d.method, "세부내용": d.detail,
            "개량관경(mm)": d.new_diameter_mm, "공사비(원)": round(d.total_cost), "선정사유": d.reason,
            "검토사항": " / ".join(p.warnings),
        })
    return pd.DataFrame(rows)


def merge_detail(res: ProjectResult) -> pd.DataFrame:
    rows = []
    for p in res.pipes:
        d = res.decisions[p.pipe_id]
        counted = {id(c) for c in d.patch_clusters}
        clusters = list(d.evaluated_clusters) + [c for c in d.patch_clusters if c not in d.evaluated_clusters]
        for c in clusters:
            if id(c) in counted:
                note = "계상"
            elif d.method == M_CIPP or (d.method == M_PARTIAL_DIG and d.sub_method == M_CIPP):
                note = "전체보수 전환으로 미계상(검토용)"
            else:
                note = "미계상"
            rows.append(_cluster_row(p, c, note))
        for c in d.dig_clusters:
            note = "계상" if d.method == M_PARTIAL_DIG else "굴착교체 전환으로 미계상(검토용)"
            rows.append(_cluster_row(p, c, note))
    cols = ["관번", "관경(mm)", "구분", "구간번호", "포함결함수", "포함결함(위치·코드·등급)", "결함시작(m)", "결함끝(m)",
            "결함간 최대이격(m)", "필요연장(m)", "적용규격(m)", "시공구간(m)", "계상개소", "병합적용 공사비(원)",
            "개별계상 공사비(원)", "병합 절감액(원)", "비고"]
    return pd.DataFrame(rows, columns=cols)


def _cluster_row(p, c, note: str) -> dict:
    is_patch = c.kind == "부분보수"
    return {
        "관번": p.pipe_id, "관경(mm)": p.diameter_mm, "구분": c.kind, "구간번호": c.cluster_no,
        "포함결함수": len(c.defects),
        "포함결함(위치·코드·등급)": ", ".join(f"{d.distance_cal:.2f}m {d.code}{d.level}" for d in c.defects),
        "결함시작(m)": c.span_start_m, "결함끝(m)": c.span_end_m, "결함간 최대이격(m)": round(c.max_gap_m, 2),
        "필요연장(m)": c.required_length_m, "적용규격(m)": c.unit_size_m,
        "시공구간(m)": f"{c.repair_start_m:.2f}~{c.repair_end_m:.2f}", "계상개소": c.n_sites,
        "병합적용 공사비(원)": round(c.cost) if is_patch else None,
        "개별계상 공사비(원)": round(c.separate_cost) if is_patch else None,
        "병합 절감액(원)": round(c.separate_cost - c.cost) if is_patch and c.merged else (0 if is_patch else None),
        "비고": ("병합 " if c.merged else "") + note,
    }


def defect_detail(res: ProjectResult) -> pd.DataFrame:
    rows = []
    for p in res.pipes:
        dec = res.decisions[p.pipe_id]
        for d in p.defects:
            rows.append({
                "관번": p.pipe_id, "조사방향": p.survey_direction, "조서거리(m)": d.distance_raw,
                "조서종료거리(m)": d.end_distance_raw,
                "보정계수": round(p.scale_factor, 4), "보정위치(m)": d.distance_cal,
                "보정종료(m)": d.end_distance_cal,
                "결함코드": d.code, "결함명": d.name, "결함내용": d.description,
                "구분": "구조" if d.state == "structural" else "운영", "등급": d.level_label or d.level,
                "결함점수": d.score, "침입수가점": d.bonus or None,
                "보수대상": "Y" if d.is_repair_target else "", "비굴착불가": "Y" if d.is_blocker else "",
                "적용공법": defect_action(dec, d),
            })
    return pd.DataFrame(rows)


def capacity_comparison(res: ProjectResult) -> pd.DataFrame:
    """정비 전·후 만관 통수능(Manning 개략)과 유속 검토. 관저경사를 알 수 없는 관로는 제외.

    유량은 실무 표기인 ㎥/일(1 L/s = 86.4 ㎥/일). 유속은 설계유량이 있으면 그 유량이 흐를 때(부분수심),
    없으면 만관 유속이며, KDS 61 40 00:2022 기준(오수 0.6~3.0, 우수·합류 0.8~3.0 m/s)으로 판정한다.
    """
    from common.hydraulics import LPS_TO_CMD, velocity_check
    from common.rehab_hydraulics import capacity_before_after
    rows = []
    for p in res.pipes:
        d = res.decisions[p.pipe_id]
        r = capacity_before_after(p, d, res.config)
        if r is None:
            continue
        flow = p.design_flow_lps
        use = p.pipe_use or res.config["criteria"].get("default_pipe_use", "오수")
        judge, action = velocity_check(res.config, use, r["v1"])
        if r["v1"] < r["v0"] - 0.005 and r["v1"] < res.config["criteria"].get("velocity_limits", {}).get(use, [0.6])[0]:
            action = ("정비 후 유속이 정비 전보다 낮아짐(하수도설계기준 18.6: 기존 최소유속 유지 원칙) · " + action).rstrip(" · ")
        rows.append({
            "관번": p.pipe_id, "관로구분": use, "정비공법": d.method,
            "경사(‰)": round((p.slope if p.slope else (p.from_invert_el - p.to_invert_el) / p.plan_length_m) * 1000, 2),
            "정비전 내경(mm)": r["d0"], "정비전 n": round(r["n0"], 4), "정비전 통수능(㎥/일)": round(r["q0"] * LPS_TO_CMD),
            "정비후 내경(mm)": round(r["d1"], 1), "정비후 n": round(r["n1"], 4), "정비후 통수능(㎥/일)": round(r["q1"] * LPS_TO_CMD),
            "통수능 변화(%)": round(r["change_pct"], 1),
            "설계유량(㎥/일)": round(flow * LPS_TO_CMD) if flow else None,
            "정비전 유량/통수능": round(flow / r["q0"], 2) if flow else None,
            "정비후 유량/통수능": round(flow / r["q1"], 2) if flow else None,
            "유속 산정": "설계유량 흐를 때" if flow else "만관(유량 미입력)",
            "정비전 유속(m/s)": round(r["v0"], 2), "정비후 유속(m/s)": round(r["v1"], 2),
            "유속 검토(정비후)": judge, "유지관리·조치": action,
        })
    return pd.DataFrame(rows)


def quantity_tables(res: ProjectResult) -> tuple[pd.DataFrame, pd.DataFrame]:
    return summarize_quantities(res.quantities), quantities_frame(res.quantities)
