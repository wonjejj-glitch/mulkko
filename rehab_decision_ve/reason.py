"""정비공법 선정 사유 자동 작문 (설계보고서·일상감사 대응용 문구)."""
from __future__ import annotations

from collections import Counter

from common.models import Defect, Pipe, RepairCluster


def _won(x: float) -> str:
    if x >= 1e8:
        return f"{x / 1e8:.2f}억원"
    return f"{x / 1e4:,.0f}만원"


def _pos_list(ds: list[Defect], limit: int = 4) -> str:
    parts = []
    for d in ds[:limit]:
        pos = f"{d.start_cal:.1f}~{d.end_cal:.1f}m" if d.end_distance_cal is not None else f"{d.distance_cal:.1f}m"
        parts.append(f"{d.name}({d.level_label or d.level},{d.score}점) @{pos}")
    more = f" 외 {len(ds) - limit}개소" if len(ds) > limit else ""
    return ", ".join(parts) + more


def _kind_summary(ds: list[Defect]) -> str:
    cnt = Counter(d.name for d in ds)
    return "·".join(f"{k} {v}" for k, v in cnt.most_common())


def calibration_note(pipe: Pipe) -> str:
    if abs(pipe.scale_factor - 1.0) < 1e-3 and pipe.survey_direction == "순방향":
        return ""
    s = f" ※ 결함위치 연장보정 적용(촬영 {pipe.cctv_length_m:.1f}m → 도면 {pipe.plan_length_m:.1f}m, 계수 {pipe.scale_factor:.3f}"
    if pipe.survey_direction == "역방향":
        s += ", 역방향 촬영 환산"
    return s + ")"


def upsize(pipe: Pipe, new_d: int, struct_t: list[Defect]) -> str:
    cap = f"(설계유량/통수능 = {pipe.capacity_ratio:.2f})" if pipe.capacity_ratio else ""
    s = (f"수리검토(SWMM/Manning) 결과 통수능 부족{cap}으로 비굴착 보수 시 단면 축소에 따른 통수능 악화가 우려되어 "
         f"D{pipe.diameter_mm} → D{new_d} 증경 굴착교체 선정")
    if struct_t:
        s += f", 구조적 결함 {len(struct_t)}개소({_kind_summary(struct_t)}) 동시 해소"
    return s


def pipe_blocker(blockers: list[Defect]) -> str:
    return (f"관로 전체 구배 불량 결함({_pos_list(blockers)})은 비굴착 보수로 구배 개선이 불가하므로 "
            f"굴착교체(동경) 선정")


def local_blocker_to_replace(blockers, digs: list[RepairCluster], dig_len: float, L: float,
                             too_many: bool, too_long: bool, too_costly: bool,
                             partial_total: float, full_exc: float, cfg: dict) -> str:
    c = cfg["criteria"]
    why = []
    if too_many:
        why.append(f"부분굴착 필요 {len(digs)}개소로 기준({c['max_partial_excavations']}개소) 초과")
    if too_long:
        why.append(f"부분굴착 연장 {dig_len:.1f}m(관로연장의 {dig_len / L * 100:.1f}%)로 "
                   f"기준({float(c['partial_excavation_max_length_ratio']) * 100:.0f}%) 이상")
    if too_costly:
        why.append(f"부분굴착+비굴착 공사비 {_won(partial_total)}가 전체 굴착교체 {_won(full_exc)}의 "
                   f"{partial_total / full_exc * 100:.1f}%(≥ {float(c['partial_vs_full_excavation_ratio']) * 100:.0f}%)")
    return (f"비굴착 시공불가 결함 {len(blockers)}개소({_pos_list(blockers)}) 발생, "
            + ", ".join(why) + "로 시공성·경제성 고려 굴착교체(동경) 선정")


def partial_dig(blockers, digs, dig_len, remaining, nd, partial_total, full_exc, cfg) -> str:
    s = (f"비굴착 시공불가 결함 {len(blockers)}개소({_pos_list(blockers)})는 부분굴착 {len(digs)}개소"
         f"(L={dig_len:.1f}m)로 처리")
    if nd.method == "비굴착 전체보수":
        if nd.trigger == "rate":
            s += (f", 잔여 구조적 결함 {len(remaining)}개소(불량률 {nd.rate:.1f}% ≥ 기준 "
                  f"{float(cfg['criteria']['defect_rate_threshold_pct']):.1f}%)는 비굴착 전체보수 적용")
        else:
            s += (f", 잔여 결함 부분보수 {sum(x.n_sites for x in nd.clusters)}개소 공사비가 전체보수 대비 "
                  f"{nd.ratio * 100:.1f}%로 경제성 고려 비굴착 전체보수 적용")
    elif nd.method == "비굴착 부분보수":
        merged = sum(1 for x in nd.clusters if x.merged)
        s += (f", 잔여 구조적 결함 {len(remaining)}개소는 비굴착 부분보수 {sum(x.n_sites for x in nd.clusters)}개소"
              + (f"(근접결함 병합 {merged}건)" if merged else "") + " 적용")
    else:
        s += ", 잔여 구간은 보수대상 결함 없음"
    if full_exc:
        s += (f" (부분굴착+비굴착 공사비 {_won(partial_total)}, 전체 굴착교체 {_won(full_exc)} 대비 "
              f"{partial_total / full_exc * 100:.1f}%로 경제적)")
    return s


def manual_full(struct_t: list[Defect], mandate_text: str) -> str:
    return f"{mandate_text} - 구조적 결함 {len(struct_t)}개소({_kind_summary(struct_t)}) → 비굴착 전체보수 선정"


def manual_full_replace(blockers: list[Defect], mandate_text: str) -> str:
    return (f"{mandate_text}이며 필수 굴착 결함({_pos_list(blockers)})이 있어 비굴착 전체보수 불가 "
            f"→ 굴착교체(동경) 선정")


def clean_only(oper: list[Defect], laterals: list[Defect], minor: list[Defect]) -> str:
    parts = []
    if oper:
        parts.append(f"운영결함({_kind_summary(oper)}) 해소")
    if laterals:
        parts.append(f"연결관 돌출 {len(laterals)}개소 절단")
    s = "구조적 보수대상 결함 없음, " + "·".join(parts) + "을 위한 준설/세정 선정"
    if minor:
        s += f" (경미결함 {len(minor)}개소는 유지관리 대상)"
    return s


def no_action(minor: list[Defect]) -> str:
    if minor:
        return f"보수기준 미달 경미결함 {len(minor)}개소({_kind_summary(minor)})로 개량제외(유지관리 대상)"
    return "CCTV 조사 결과 결함 없음 - 개량제외"


def nondig(struct_t: list[Defect], nd, cfg: dict, oper: list[Defect], laterals: list[Defect]) -> str:
    c = cfg["criteria"]
    th = float(c["defect_rate_threshold_pct"])
    be = float(c["breakeven_ratio"]) * 100
    n_sites = sum(x.n_sites for x in nd.clusters)
    merged = [x for x in nd.clusters if x.merged]
    merge_txt = (f", 근접결함 병합 {len(merged)}건(이격 {float(c['merge_gap_m']):.1f}m 이내)" if merged else "")
    head = f"구조적 결함 {len(struct_t)}개소({_kind_summary(struct_t)}, 불량률 {nd.rate:.1f}%)"
    if nd.trigger == "rate":
        s = f"{head}로 불량률 기준({th:.1f}%) 이상이므로 비굴착 전체보수 선정"
    elif nd.trigger == "economy":
        s = (f"{head}이나 부분보수 {n_sites}개소{merge_txt} 공사비({_won(nd.patch_cost)})가 전체보수"
             f"({_won(nd.full_cost)}) 대비 {nd.ratio * 100:.1f}%(≥ 손익분기 {be:.0f}%)로 "
             f"경제성 및 구조적 안정성 고려 비굴착 전체보수 선정")
    else:
        s = (f"{head}로 불량률 기준({th:.1f}%) 미만이며 부분보수 {n_sites}개소{merge_txt} 공사비가 전체보수 대비 "
             f"{nd.ratio * 100:.1f}%(< {be:.0f}%)로 비굴착 부분보수 선정")
    extra = []
    if laterals:
        extra.append(f"돌출관 절단 {len(laterals)}개소")
    if oper:
        extra.append(f"운영결함({_kind_summary(oper)})은 사전 준설로 해소")
    if extra:
        s += " / " + ", ".join(extra)
    return s
