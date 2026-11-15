"""CCTV 주행(촬영)연장 ↔ 도면연장 오차 보정 및 결함 분류(매뉴얼별 결함점수)."""
from __future__ import annotations

from common.manuals import grade_label, is_flexible
from common.models import Defect, Pipe


def classify_defect(d: Defect, cfg: dict, flexible: bool = False) -> None:
    """적용 매뉴얼(cfg에 적용된 2015/2017/2025)의 결함점수로 보수대상을, 결함코드 기준으로 시공불가를 판정한다.

    - 점수: 매뉴얼 표의 등급별 결함점수(2025는 강성관/연성관 구분)
    - 보수대상: 결함점수 ≥ repair_min_score(구조) / oper_min_score(운영)
        2017·2025: 20점(단위구간 상태등급 3 '보통'), 2015: 15.1점(1m 최대값 '양호' 구간 하한)
    - 2015 연성관의 변형(DF)은 변형(연성관, PF)로 자동 전환
    """
    code = cfg.get("code_aliases", {}).get(d.code, d.code)
    if flexible:
        code = (cfg.get("flexible_variants") or {}).get(code, code)
    spec = cfg["defect_codes"][code]
    d.code = code
    d.name = spec["name"]
    d.category = spec["category"]
    d.state = spec.get("state", spec["category"])
    c = cfg["criteria"]
    d.remedy = spec["remedy"]
    if d.remedy == "돌출관절단" and c.get("lateral_as_patch"):
        d.remedy = "부분보수"                      # 설계 관행: 연결관돌출도 부분보수 개소로 계상
    d.blocker_scope = spec.get("blocker_scope", "point")
    scores = spec.get("scores_flexible" if flexible else "scores_rigid") or spec.get("scores")
    d.score = int(scores[d.level])
    d.level_label = grade_label(cfg, d.level)
    if d.category == "operational":
        d.is_repair_target = d.score >= float(c.get("oper_min_score", 20))
    else:
        d.is_repair_target = bool(c.get("repair_all_grades")) or d.score >= float(c.get("repair_min_score", 20))
    d.is_blocker = _is_blocker(code, spec, d, c.get("blocker_policy", "default"))
    if d.is_blocker:
        d.is_repair_target = True


COLLAPSE_CODES = {"CX", "PX", "TM"}     # 붕괴·관단절: 정책과 무관하게 굴착


def _is_blocker(code: str, spec: dict, d: Defect, policy: str) -> bool:
    """시공불가(굴착 필수) 판정.
    default: 매뉴얼 파일의 blocker_min_grade / A_only: 국부 결함은 '대'(A)만 / none: 국부 결함은 비굴착 전환(붕괴류·관로전체 결함만 굴착)."""
    bmin = spec.get("blocker_min_grade")
    if bmin is None:
        return False
    if spec.get("blocker_scope") == "pipe" or code in COLLAPSE_CODES:
        return d.grade >= int(bmin)
    if policy == "none":
        return False
    if policy == "A_only":
        return d.grade >= max(int(bmin), 5)
    return d.grade >= int(bmin)


def apply_infiltration_bonus(pipe: Pipe, cfg: dict) -> None:
    """2025 매뉴얼: 구조적 결함 지점에 침입수(IF) 중·대 유입 시 구조적 결함점수에 +10/+20."""
    rule = cfg.get("infiltration_bonus")
    if not rule:
        return
    win = float(rule.get("window_m", 0.5))
    infs = [d for d in pipe.defects if d.code == "IF" and d.level in rule["bonus"]]
    for d in pipe.defects:
        if d.code not in rule["codes"]:
            continue
        near = [i for i in infs if i.start_cal - win <= d.end_cal and d.start_cal <= i.end_cal + win]
        if near:
            add = max(int(rule["bonus"][i.level]) for i in near)
            d.score += add
            d.bonus = add
            min_score = cfg["criteria"].get("repair_min_score", 20)
            d.is_repair_target = d.is_repair_target or d.score >= float(min_score)


def calibrate_pipe(pipe: Pipe, cfg: dict) -> None:
    """결함 위치를 도면연장 기준으로 보정하고 결함을 분류한다.

    보정위치 = (순방향 거리) × (도면연장 / 촬영연장)
    역방향 촬영(종점→시점)은 촬영연장 - 거리로 변환 후 보정한다.
    """
    plan, cctv = pipe.plan_length_m, pipe.cctv_length_m
    scale = plan / cctv if cctv and cctv > 0 else 1.0
    pipe.scale_factor = scale
    warn_ratio = cfg["criteria"].get("length_mismatch_warn_ratio", 0.05)
    pipe.warnings = [w for w in pipe.warnings if not w.startswith("연장오차")]
    if abs(scale - 1.0) > warn_ratio:
        pipe.warnings.append(
            f"연장오차 {abs(plan - cctv):.2f}m ({(scale - 1) * 100:+.1f}%) - 맨홀 위치·촬영 시작점 재확인 필요"
        )

    def conv(x: float) -> float:
        base = (cctv - x) if pipe.survey_direction == "역방향" else x
        return round(min(max(base * scale, 0.0), plan), 2)

    flexible = is_flexible(cfg, pipe.material)
    for d in pipe.defects:
        if d.distance_raw > cctv * 1.02:
            if not any("촬영연장 초과" in w for w in pipe.warnings):
                pipe.warnings.append("촬영연장 초과 위치의 결함 존재 - 관말단으로 보정")
        d.distance_cal = conv(d.distance_raw)
        d.end_distance_cal = conv(d.end_distance_raw) if d.end_distance_raw is not None else None
        d.bonus = 0
        classify_defect(d, cfg, flexible)
    apply_infiltration_bonus(pipe, cfg)
    pipe.defects.sort(key=lambda d: d.start_cal)
