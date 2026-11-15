"""맨홀 상태등급 산정 (환경부 2017 표준매뉴얼 3.3.2) - 점수표·최대값 등급·이음부 단위구간 평균.

매뉴얼: 맨홀 1m 단위로 결함을 판독해 구조적/운영적 결함점수의 최대값으로 5단계 등급을 정한다.
조사기관 실무(실제 사업 실제 사업 4,515건 대조): 구조적 상태는 블록 이음부로 나뉜 단위구간별 최대등급의 평균으로 산정한다.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config" / "manuals" / "manhole_2017.json"


@lru_cache(maxsize=1)
def tables() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def to_grade(score: float) -> int:
    for lo, g in tables()["grade_bands"]:
        if score >= lo:
            return g
    return 1


def defect_score(name: str, level: str) -> tuple[float, str] | None:
    """(점수, 'structural'|'operational'). 매뉴얼 표에 없는 결함명이면 None."""
    t = tables()
    for cat in ("structural", "operational"):
        if name in t[cat]:
            return float(t[cat][name]["scores"][level]), cat
    return None


def grade_manhole(defects: list[tuple[str, str]], events: list[str] | None = None) -> dict:
    """defects: [(결함명, 대/중/소)], events: 상태 항목 이름(예: '블록이음부존재(중)').

    반환: struct_max_score/struct_max_grade(매뉴얼 최대값 등급), struct_grade(단위구간 평균, 이음부 있을 때),
          oper_score/oper_grade, units(단위구간 수), unknown(표에 없는 결함명), repair(보수대상 여부)
    """
    t = tables()
    um = t["unit_model"]
    s: list[tuple[str, float]] = []
    o: list[float] = []
    unknown: list[str] = []
    for name, lv in defects:
        r = defect_score(name, lv)
        if r is None:
            unknown.append(name)
        elif r[1] == "structural":
            s.append((name, r[0]))
        else:
            o.append(r[0])
    smax = max((sc for _, sc in s), default=0.0)
    n_event = sum(1 for e in (events or []) if e.startswith(um["joint_event_prefix"]))
    joint_sc = [sc for n, sc in s if n in um["joint_defects"]]
    joints = max(n_event, 1 if joint_sc else 0)
    if joints == 0:
        struct = float(to_grade(smax))
        units = 1
    else:
        units = joints + 1
        bins: list[list[float]] = [[] for _ in range(units)]
        for n, sc in s:
            if n in um["joint_defects"]:
                bins[0].append(sc)
                bins[1].append(sc)        # 이음부 결함은 앞뒤 단위구간 모두에 적용
            elif n in um["bottom"] or n in um["lateral"]:
                bins[-1].append(sc)
            else:                          # 뚜껑·표면단차·벽체 결함(표면손상·균열 등)은 맨 위 단위
                bins[0].append(sc)
        struct = round(sum(to_grade(max(b, default=0.0)) for b in bins) / units, 2)
    oper_score = max(o, default=0.0)
    rt = t["repair_target"]
    return {"struct_max_score": smax, "struct_max_grade": to_grade(smax), "struct_grade": struct, "units": units,
            "oper_score": oper_score, "oper_grade": to_grade(oper_score), "unknown": unknown,
            "repair": struct >= rt["struct_ge"] or to_grade(oper_score) >= rt["oper_ge"]}


def state_name(grade: float) -> str:
    return tables()["grade_names"][str(min(5, max(1, int(round(grade + 1e-9)))))]


@lru_cache(maxsize=1)
def repair_rules() -> dict:
    return json.loads((CONFIG.parent.parent / "manhole_repair.json").read_text(encoding="utf-8"))


def _price(v: dict, depth: float) -> float:
    if "price_by_depth" in v:
        for hmax, pr in v["price_by_depth"]:
            if depth <= hmax:
                return pr
        return v["price_by_depth"][-1][1]
    return v["price"]


def decide_repair(defect_names: list, struct_grade: float, oper_grade: float, depth_m: float | None = None) -> list[dict]:
    """맨홀 1개소의 보수공법 목록 [{공법, 단위, 수량, 단가, 금액, 단가근거}].

    defect_names: [(결함명, 대/중/소)] 또는 결함명 목록(등급 미상은 '중'으로 봄).
    결함별 공법(trigger_defects·trigger_levels) + 구조등급 규칙(5등급 재설치, 4등급대 내벽라이닝).
    """
    r = repair_rules()
    items = [(d, "중") if isinstance(d, str) else tuple(d) for d in defect_names]
    depth = depth_m if depth_m and depth_m > 0 else r["default_depth_m"]
    gr = r["grade_rule"]
    picked: list[str] = []
    if struct_grade >= gr["replace_struct_ge"]:
        picked.append("맨홀재설치")
    elif struct_grade >= gr["lining_struct_ge"]:
        picked.append("내벽라이닝")
    replaced_by = {"맨홀재설치": {"맨홀뚜껑설치", "인버트설치", "맨홀내부파손보수", "맨홀관거접합부보수", "맨홀주변부보수", "균열보수", "사다리교체"},
                   "내벽라이닝": {"맨홀내부파손보수", "균열보수"}}
    skip = replaced_by.get(picked[0], set()) if picked else set()
    for w, v in r["works"].items():
        trig = set(v.get("trigger_defects", []))
        if not trig or w in skip:
            continue
        lv_ok = set(v.get("trigger_levels", ["대", "중", "소"]))
        if any(n in trig and lv in lv_ok for n, lv in items):
            picked.append(w)
    out = []
    for w in picked:
        v = r["works"][w]
        q = round(depth, 1) if v["unit"] == "m" else 1
        pr = _price(v, depth)
        out.append({"공법": w, "단위": v["unit"], "수량": q, "단가": pr, "금액": int(round(q * pr)),
                    "단가근거": v.get("price_basis", "")})
    return out
