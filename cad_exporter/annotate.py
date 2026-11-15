"""도면 주석 문자열 생성 (DXF·LSP 공용)."""
from __future__ import annotations

from common.models import (M_CIPP, M_CLEAN, M_NONE, M_PARTIAL_DIG, M_PATCH, M_REPLACE, M_UPSIZE, Defect,
                           PipeDecision)


def defect_action(dec: PipeDecision, d: Defect) -> str:
    """해당 결함에 적용되는 보수공법 표기."""
    if dec.method in (M_UPSIZE, M_REPLACE):
        return dec.method
    for c in dec.dig_clusters:
        if d in c.defects or (c.repair_start_m <= d.start_cal and d.end_cal <= c.repair_end_m):
            return f"부분굴착#{c.cluster_no}"
    if not d.is_repair_target:
        return "유지관리"
    if d.remedy == "돌출관절단":
        return "돌출관절단"
    if d.category == "operational":
        return d.remedy
    if dec.method == M_CIPP or (dec.method == M_PARTIAL_DIG and dec.sub_method == M_CIPP):
        return "비굴착 전체보수"
    for c in dec.patch_clusters:
        if d in c.defects:
            tag = "병합" if c.merged else ""
            return f"부분보수#{c.cluster_no}{tag}(L={c.unit_size_m:.1f}m)"
    if dec.method in (M_CLEAN, M_NONE, M_PATCH):
        return dec.method
    return "-"


def defect_note(dec: PipeDecision, d: Defect) -> str:
    pos = f"{d.start_cal:.1f}~{d.end_cal:.1f}m" if d.end_distance_cal is not None else f"{d.distance_cal:.1f}m"
    return f"{pos} | {d.name}({d.code}) {d.level_label or d.level}{d.score} | {defect_action(dec, d)}"


def defect_color(d: Defect) -> int:
    """AutoCAD 색상번호: 시공불가=적색, 보수대상=황색, 운영결함=청록, 경미=회색."""
    if d.is_blocker:
        return 1
    if not d.is_repair_target:
        return 8
    if d.category == "operational":
        return 4
    return 2


def safe_layer(name: str) -> str:
    for ch in '<>/\\":;?*|=`':
        name = name.replace(ch, "_")
    return name
