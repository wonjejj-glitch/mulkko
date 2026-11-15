"""시공성·경제성 기반 정비공법 의사결정 엔진.

판정 우선순위
  Step 1  수리적/구조적 Blocker
          - SWMM 통수능 부족 → 굴착교체(증경)
          - 관로 전체에 영향을 주는 시공불가 결함(역경사 등) → 굴착교체(동경)
          - 국부적 시공불가 결함(붕괴위험 파손·심한 침하·큰 단차 등)
              → 부분굴착 구간 수·연장·공사비가 기준 이내면 부분굴착+비굴착, 초과면 굴착교체(동경)
  Step 2  불량률 및 손익분기 VE
          - 구조적 불량률 ≥ 기준(25%) → 비굴착 전체보수
          - 병합 부분보수 공사비 ≥ 전체보수 공사비 × 손익분기(0.75) → 비굴착 전체보수
          - 그 외 → 비굴착 부분보수
          - 구조적 보수대상 없음: 운영결함/돌출관 → 준설/세정, 없으면 개량제외
  Step 3  선정 사유 자동 작문 (reason.py)
"""
from __future__ import annotations

from dataclasses import dataclass

from common.config import upsized_diameter
from common.models import (M_CIPP, M_CLEAN, M_NONE, M_PARTIAL_DIG, M_PATCH, M_REPLACE, M_UPSIZE, Defect, Pipe,
                           PipeDecision, RepairCluster)
from defect_processor.clustering import build_dig_clusters, build_patch_clusters, defect_rate_pct
from quantity_takeoff.costs import excavation_cost, full_cipp_cost
from rehab_decision_ve import reason as R
from rehab_decision_ve.scoring import fill_condition


@dataclass
class _NonDig:
    method: str                         # M_CIPP | M_PATCH | ""
    clusters: list[RepairCluster]
    patch_cost: float
    full_cost: float
    ratio: float
    rate: float
    trigger: str                        # rate | economy | patch | none


def _evaluate_nondig(pipe: Pipe, defects: list[Defect], cfg: dict, length_m: float) -> _NonDig:
    c = cfg["criteria"]
    full = full_cipp_cost(cfg, pipe.diameter_mm, length_m)
    if not defects:
        return _NonDig("", [], 0.0, full, 0.0, 0.0, "none")
    clusters = build_patch_clusters(pipe, defects, cfg)
    patch_cost = sum(cl.cost for cl in clusters)
    ratio = patch_cost / full if full > 0 else 0.0
    rate = defect_rate_pct(pipe, defects, cfg)
    if rate >= float(c["defect_rate_threshold_pct"]):
        return _NonDig(M_CIPP, clusters, patch_cost, full, ratio, rate, "rate")
    if ratio >= float(c["breakeven_ratio"]):
        return _NonDig(M_CIPP, clusters, patch_cost, full, ratio, rate, "economy")
    return _NonDig(M_PATCH, clusters, patch_cost, full, ratio, rate, "patch")


def _manual_mandate(dec: PipeDecision, ev: dict, cfg: dict) -> tuple[bool, str]:
    """적용 매뉴얼의 '전체보수/교체' 판단기준 충족 여부.

    2017·2025: 조사구간 구조적 상태점수 ≥ 4.0
    2015     : 불량비(이상항목 개소/연장) ≥ 0.2 AND 구조적 평균값 '불량'
    """
    if not cfg["criteria"].get("manual_full_rule", True):
        return False, ""
    r = cfg.get("rules") or {}
    mid = cfg.get("manual", "")
    th = r.get("full_if_section_state_ge")
    if th is not None and dec.condition_score >= float(th):
        return True, f"{mid} 매뉴얼 기준 조사구간 구조적 상태점수 {dec.condition_score:.2f} ≥ {float(th):.1f}로 구간 전체보수·교체 대상"
    br = r.get("full_if_bad_ratio_ge")
    if br is not None and dec.bad_ratio >= float(br) and ev.get("struct_avg_grade", 0) >= int(r.get("full_requires_avg_grade_ge", 5)):
        return True, (f"{mid} 매뉴얼 기준 불량비 {dec.bad_ratio:.3f} ≥ {float(br):.1f}이고 구조적 평균값 "
                      f"{ev.get('struct_avg', 0):.2f}('불량')로 전체보수 대상")
    return False, ""


def _inside(d: Defect, clusters: list[RepairCluster]) -> bool:
    return any(cl.repair_start_m - 1e-6 <= d.start_cal and d.end_cal <= cl.repair_end_m + 1e-6 for cl in clusters)


def decide_pipe(pipe: Pipe, cfg: dict) -> PipeDecision:
    c = cfg["criteria"]
    L, D = pipe.plan_length_m, pipe.diameter_mm
    defs = pipe.defects
    struct_t = [d for d in defs if d.category == "structural" and d.is_repair_target and d.remedy != "돌출관절단"]
    laterals = [d for d in defs if d.is_repair_target and d.remedy == "돌출관절단"]
    oper_t = [d for d in defs if d.category == "operational" and d.is_repair_target]
    minor = [d for d in defs if not d.is_repair_target]
    blockers = [d for d in struct_t if d.is_blocker]
    pipe_blockers = [d for d in blockers if d.blocker_scope == "pipe"]

    dec = PipeDecision(pipe_id=pipe.pipe_id, method=M_NONE, detail="", reason="",
                       n_structural=len(struct_t), n_operational=len(oper_t), n_minor=len(minor),
                       n_lateral_cut=len(laterals), full_cipp_cost=full_cipp_cost(cfg, D, L))
    dec.defect_rate_pct = defect_rate_pct(pipe, struct_t, cfg)
    ev = fill_condition(pipe, dec, cfg)                       # 적용 매뉴얼 상태평가(공법 판정 전)
    mandate, mandate_text = _manual_mandate(dec, ev, cfg)
    notes = R.calibration_note(pipe)
    if any(d.position_estimated for d in defs):
        notes += " ※ 결함위치 미상(주행구간 균등배치 추정) - 근접결함 병합 미적용"
    notes += f" [{dec.manual} 매뉴얼 {dec.state_text}]"
    if dec.emergency:
        notes += " ※ 2015 매뉴얼 긴급보수 대상(구조적 최대값 '불량')"

    # ---------------- Step 1-a : 통수능 부족 → 증경 굴착교체
    if pipe.capacity_insufficient:
        new_d = pipe.required_diameter_mm if pipe.required_diameter_mm and pipe.required_diameter_mm > D \
            else upsized_diameter(cfg, D)
        dec.method, dec.new_diameter_mm = M_UPSIZE, new_d
        dec.detail = f"D{D} → D{new_d} 증경, L={L:.1f}m"
        dec.reason = R.upsize(pipe, new_d, struct_t) + notes
        return dec

    # ---------------- Step 1-b : 관로 전체 영향 Blocker(역경사 등) → 동경 굴착교체
    if pipe_blockers:
        dec.method, dec.new_diameter_mm = M_REPLACE, D
        dec.detail = f"D{D} 동경 교체, L={L:.1f}m"
        dec.reason = R.pipe_blocker(pipe_blockers) + notes
        return dec

    # ---------------- Step 1-c : 국부 Blocker → 부분굴착 or 전체 굴착교체
    if blockers:
        digs = build_dig_clusters(pipe, blockers, cfg)
        dig_len = sum(cl.length_m for cl in digs)
        remaining = [d for d in struct_t if not d.is_blocker and not _inside(d, digs)]
        rem_len = max(L - dig_len, 0.0)
        nd = _evaluate_nondig(pipe, remaining, cfg, rem_len)
        dig_cost = sum(excavation_cost(cfg, pipe, cl.length_m, D) for cl in digs)
        nondig_cost = nd.full_cost if nd.method == M_CIPP else (nd.patch_cost if nd.method == M_PATCH else 0.0)
        partial_total = dig_cost + nondig_cost
        full_exc = excavation_cost(cfg, pipe, L, D)

        if mandate:                                           # 매뉴얼 전체보수/교체 대상 + 굴착 필수 결함 → 전체 굴착교체
            dec.method, dec.new_diameter_mm = M_REPLACE, D
            dec.dig_clusters = digs
            dec.detail = f"D{D} 동경 교체, L={L:.1f}m"
            dec.reason = R.manual_full_replace(blockers, mandate_text) + notes
            return dec

        too_many = len(digs) > int(c["max_partial_excavations"])
        too_long = L > 0 and dig_len / L >= float(c["partial_excavation_max_length_ratio"])
        too_costly = full_exc > 0 and partial_total >= full_exc * float(c["partial_vs_full_excavation_ratio"])
        if too_many or too_long or too_costly:
            dec.method, dec.new_diameter_mm = M_REPLACE, D
            dec.dig_clusters = digs
            dec.detail = f"D{D} 동경 교체, L={L:.1f}m"
            dec.reason = R.local_blocker_to_replace(
                blockers, digs, dig_len, L, too_many, too_long, too_costly, partial_total, full_exc, cfg) + notes
            return dec

        dec.method, dec.dig_clusters, dec.sub_method = M_PARTIAL_DIG, digs, nd.method
        dec.patch_clusters = nd.clusters if nd.method == M_PATCH else []
        dec.evaluated_clusters = nd.clusters
        dec.patch_cost, dec.patch_to_full_ratio = nd.patch_cost, nd.ratio
        sub = {M_CIPP: f"비굴착 전체보수 L={rem_len:.1f}m",
               M_PATCH: f"비굴착 부분보수 {sum(x.n_sites for x in nd.clusters)}개소",
               "": "잔여구간 보수 불요"}[nd.method]
        dec.detail = f"부분굴착 {len(digs)}개소(L={dig_len:.1f}m) + {sub}"
        dec.reason = R.partial_dig(blockers, digs, dig_len, remaining, nd, partial_total, full_exc, cfg) + notes
        return dec

    # ---------------- Step 2 : 비굴착 판정
    if not struct_t:
        if oper_t or laterals:
            dec.method = M_CLEAN
            dec.detail = f"준설·세정 L={L:.1f}m" + (f", 돌출관 절단 {len(laterals)}개소" if laterals else "")
            dec.reason = R.clean_only(oper_t, laterals, minor) + notes
        else:
            dec.method = M_NONE
            dec.detail = "-"
            dec.reason = R.no_action(minor) + notes
        return dec

    nd = _evaluate_nondig(pipe, struct_t, cfg, L)
    dec.evaluated_clusters, dec.patch_cost, dec.patch_to_full_ratio = nd.clusters, nd.patch_cost, nd.ratio
    if mandate:
        dec.method = M_CIPP
        dec.detail = f"비굴착 전체보수 D{D} L={L:.1f}m"
        dec.reason = R.manual_full(struct_t, mandate_text) + notes
        return dec
    if nd.method == M_CIPP:
        dec.method = M_CIPP
        dec.detail = f"비굴착 전체보수 D{D} L={L:.1f}m"
    else:
        dec.method = M_PATCH
        dec.patch_clusters = nd.clusters
        dec.detail = f"부분보수 {sum(x.n_sites for x in nd.clusters)}개소 (결함 {len(struct_t)}개소 병합)"
    dec.reason = R.nondig(struct_t, nd, cfg, oper_t, laterals) + notes
    return dec
