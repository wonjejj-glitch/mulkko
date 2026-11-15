"""하수관로 상태등급 판정 — 적용 매뉴얼별 방식.

[2017·2025 표준매뉴얼] method = unit_max_avg
  1단계 단위구간(관 1본): 본 안 결함점수 최댓값 → 상태등급 1~5 (70↑5 · 40↑4 · 20↑3 · 10↑2 · 그 외 1)
  2단계 조사구간(맨홀~맨홀): 단위구간 등급 평균(소수 둘째자리) = 상태점수
  이음부 결함(단차·손상·이탈)이 이음부 위치에 있으면 전·후 단위구간 모두에 적용
[2015 정밀조사 매뉴얼] method = meter_sum
  1m 연장마다 발견된 모든 결함 등급점수를 합산
  최대값 = 1m 합산값의 최대 → 5단계, 평균값 = 전체 합산점수 / 조사연장(m) → 5단계
  불량비 = 이상항목 개소수 / 연장(m)
"""
from __future__ import annotations

import math

from common.config import pipe_unit_length
from common.models import Pipe, PipeDecision


def round_half_up(x: float, nd: int) -> float:
    """매뉴얼·엑셀식 사사오입(파이썬 round는 3.675 → 3.67)."""
    from decimal import ROUND_HALF_UP, Decimal
    return float(Decimal(repr(x)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP))


def _band(value: float, bands) -> int:
    for lo, grade in bands:
        if value >= lo - 1e-9:
            return int(grade)
    return 1


def band(score: float, cfg: dict) -> int:
    return _band(score, cfg["condition_grading"]["unit_grade_bands"])


def unit_grades(pipe: Pipe, cfg: dict, state: str = "structural") -> list[int]:
    """[2017·2025] 단위구간(본)별 상태등급."""
    g = cfg["condition_grading"]
    u = pipe_unit_length(cfg, pipe.material)
    n = max(1, math.ceil(pipe.plan_length_m / u - 1e-9))
    joint_codes = set(g.get("joint_codes", []))
    tol = float(g.get("joint_tolerance_m", 0.3))
    worst = [0] * n
    for d in pipe.defects:
        if d.state != state:
            continue
        a = min(int(d.start_cal // u), n - 1)
        b = min(int(d.end_cal // u), n - 1)
        units = set(range(a, b + 1))
        if d.code in joint_codes and d.end_distance_cal is None:
            k = round(d.distance_cal / u)                 # 가장 가까운 이음부(본 경계)
            if 0 < k < n and abs(d.distance_cal - k * u) <= tol:
                units |= {k - 1, k}                       # 이음부 전·후 본 모두 적용
        for i in units:
            worst[i] = max(worst[i], d.score)
    return [band(s, cfg) for s in worst]


def section_score(grades: list[int]) -> float:
    return round_half_up(sum(grades) / len(grades), 2) if grades else 1.0


def meter_sums(pipe: Pipe, cfg: dict, state: str = "structural") -> list[float]:
    """[2015] 1m 구간별 결함점수 합산(연속결함은 걸친 모든 1m 구간에 반영)."""
    step = float(cfg["condition_grading"].get("bin_m", 1.0))
    n = max(1, math.ceil(pipe.plan_length_m / step - 1e-9))
    sums = [0.0] * n
    for d in pipe.defects:
        if d.state != state:
            continue
        a = min(int(d.start_cal // step), n - 1)
        b = min(int(d.end_cal // step), n - 1)
        for i in range(a, b + 1):
            sums[i] += d.score
    return sums


def evaluate(pipe: Pipe, cfg: dict) -> dict:
    """적용 매뉴얼 방식으로 구조적·운영적 상태를 평가한다."""
    g = cfg["condition_grading"]
    names = g.get("grade_names", {})
    if g.get("method") == "meter_sum":
        # 평균값 분모: 조사길이(촬영연장). 2015 집계표 조사기관 판정과 일치(조사길이 90.8% vs 관로연장 82%)
        survey = next((v for v in (pipe.survey_length_m, pipe.cctv_length_m, pipe.plan_length_m) if v and v > 0), 1.0)
        L = max(survey if g.get("avg_denominator") == "survey_length" else pipe.plan_length_m, 1e-9)
        s_sum, o_sum = meter_sums(pipe, cfg, "structural"), meter_sums(pipe, cfg, "operational")
        s_max, o_max = max(s_sum), max(o_sum)
        nd = int(g.get("avg_round", 6))
        s_avg, o_avg = round_half_up(sum(s_sum) / L, nd), round_half_up(sum(o_sum) / L, nd)
        s_max_g, s_avg_g = _band(s_max, g["struct_max_bands"]), _band(s_avg, g["struct_avg_bands"])
        o_max_g, o_avg_g = _band(o_max, g["oper_max_bands"]), _band(o_avg, g["oper_avg_bands"])
        n_items = sum(1 for d in pipe.defects if d.state == "structural")
        return {
            "condition_score": float(s_avg_g), "oper_score": float(o_avg_g), "condition_grade": str(s_max_g),
            "bad_unit_ratio_pct": 0.0, "bad_ratio": round(n_items / max(pipe.plan_length_m, 1e-9), 3),   # 맨홀간 연장 기준
            "emergency": s_max >= float(cfg.get("rules", {}).get("emergency_if_max_ge", 1e9)),
            "struct_max": s_max, "struct_avg": round(s_avg, 2), "struct_avg_grade": s_avg_g,
            "state_text": (f"구조 최대 {s_max:g}점({names.get(str(s_max_g), s_max_g)})·평균 {s_avg:.2f}"
                           f"({names.get(str(s_avg_g), s_avg_g)}) / 운영 최대 {o_max:g}점({names.get(str(o_max_g), o_max_g)})"),
        }
    sg, og = unit_grades(pipe, cfg, "structural"), unit_grades(pipe, cfg, "operational")
    sc, oc = section_score(sg), section_score(og)
    return {
        "condition_score": sc, "oper_score": oc, "condition_grade": str(max(sg)),
        "bad_unit_ratio_pct": round(sum(1 for x in sg if x >= 3) / len(sg) * 100, 1), "bad_ratio": 0.0,
        "emergency": False,
        "state_text": f"구조 {sc:.2f}(최대 본 {max(sg)}등급 {names.get(str(max(sg)), '')}) / 운영 {oc:.2f}",
    }


def fill_condition(pipe: Pipe, dec: PipeDecision, cfg: dict) -> dict:
    ev = evaluate(pipe, cfg)
    for k in ("condition_score", "oper_score", "condition_grade", "bad_unit_ratio_pct", "bad_ratio", "emergency", "state_text"):
        setattr(dec, k, ev[k])
    dec.manual = cfg.get("manual", "2017")
    return ev


def assign_priority(pipes: list[Pipe], decisions: dict[str, PipeDecision]) -> None:
    """우선순위: 긴급보수 → 구조적 상태점수 ↓ → 최대 등급 ↓ → 운영적 상태점수 ↓ → 통수능 부족."""
    ranked = sorted(pipes, key=lambda p: (not decisions[p.pipe_id].emergency,
                                          -decisions[p.pipe_id].condition_score,
                                          -int(decisions[p.pipe_id].condition_grade),
                                          -decisions[p.pipe_id].oper_score,
                                          not p.capacity_insufficient))
    for i, p in enumerate(ranked, start=1):
        decisions[p.pipe_id].priority = i


def assign_condition(pipes: list[Pipe], decisions: dict[str, PipeDecision], cfg: dict) -> None:
    """(하위호환) 모든 관로를 같은 매뉴얼로 평가하고 우선순위를 매긴다."""
    for p in pipes:
        fill_condition(p, decisions[p.pipe_id], cfg)
    assign_priority(pipes, decisions)
