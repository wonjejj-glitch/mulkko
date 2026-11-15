"""근접 결함 병합(Defect Merging) - 부분보수 / 부분굴착 구간 생성."""
from __future__ import annotations

import math

from common.models import Defect, Pipe, RepairCluster
from quantity_takeoff.costs import partial_repair_unit_cost


def _group_by_gap(defects: list[Defect], gap: float) -> list[list[Defect]]:
    """위치순 결함들을 이격거리(앞 결함 끝 ~ 다음 결함 시작) ≤ gap 기준으로 묶는다.
    위치가 추정값인 결함은 실제 이격을 알 수 없으므로 병합하지 않는다(설계 관행: 결함 1개소 = 보수 1개소)."""
    groups: list[list[Defect]] = []
    for d in sorted(defects, key=lambda x: x.start_cal):
        estimated = d.position_estimated or (groups and any(x.position_estimated for x in groups[-1]))
        if groups and not estimated and d.start_cal - max(x.end_cal for x in groups[-1]) <= gap + 1e-9:
            groups[-1].append(d)
        else:
            groups.append([d])
    return groups


def _fit_size(required: float, sizes: list[float]) -> tuple[float, int]:
    """필요연장을 덮는 최소 표준규격과 개소수. 최대 규격 초과 시 최대 규격 n개소로 분할."""
    sizes = sorted(sizes)
    for s in sizes:
        if required <= s + 1e-9:
            return s, 1
    mx = sizes[-1]
    return mx, math.ceil(required / mx - 1e-9)


def build_patch_clusters(pipe: Pipe, defects: list[Defect], cfg: dict) -> list[RepairCluster]:
    """비굴착 부분보수 대상 결함을 병합하여 보수 구간 목록을 만든다."""
    c = cfg["criteria"]
    gap, margin = float(c["merge_gap_m"]), float(c["repair_margin_m"])
    sizes = [float(s) for s in c["partial_repair_sizes_m"]]
    out: list[RepairCluster] = []
    for i, grp in enumerate(_group_by_gap(defects, gap), start=1):
        s = min(d.start_cal for d in grp)
        e = max(d.end_cal for d in grp)
        required = (e - s) + 2 * margin
        size, n = _fit_size(required, sizes)
        center = (s + e) / 2
        total = size * n
        rs = max(0.0, center - total / 2)
        re_ = min(pipe.plan_length_m, rs + total)
        rs = max(0.0, re_ - total)
        unit = partial_repair_unit_cost(cfg, pipe.diameter_mm, size)
        single = partial_repair_unit_cost(cfg, pipe.diameter_mm, sizes[0])
        # 병합하지 않았다면: 결함별(연속결함은 자체 연장 기준) 개별 부분보수
        separate = 0.0
        for d in grp:
            ds, dn = _fit_size((d.end_cal - d.start_cal) + 2 * margin, sizes)
            separate += partial_repair_unit_cost(cfg, pipe.diameter_mm, ds) * dn
        out.append(RepairCluster(
            pipe_id=pipe.pipe_id, cluster_no=i, kind="부분보수", defects=grp,
            span_start_m=round(s, 2), span_end_m=round(e, 2),
            repair_start_m=round(rs, 2), repair_end_m=round(re_, 2),
            required_length_m=round(required, 2), unit_size_m=size, n_sites=n,
            cost=unit * n, separate_cost=max(separate, single),
        ))
    return out


def build_dig_clusters(pipe: Pipe, blockers: list[Defect], cfg: dict) -> list[RepairCluster]:
    """비굴착 시공불가 결함을 부분굴착 구간으로 묶는다(인접 굴착구간은 하나로)."""
    c = cfg["criteria"]
    margin = float(c["partial_excavation_margin_m"])
    min_len = float(c["partial_excavation_min_length_m"])
    out: list[RepairCluster] = []
    for i, grp in enumerate(_group_by_gap(blockers, min_len), start=1):
        s = min(d.start_cal for d in grp)
        e = max(d.end_cal for d in grp)
        length = max(min_len, (e - s) + 2 * margin)
        center = (s + e) / 2
        rs = max(0.0, center - length / 2)
        re_ = min(pipe.plan_length_m, rs + length)
        rs = max(0.0, re_ - length)
        out.append(RepairCluster(
            pipe_id=pipe.pipe_id, cluster_no=i, kind="부분굴착", defects=grp,
            span_start_m=round(s, 2), span_end_m=round(e, 2),
            repair_start_m=round(rs, 2), repair_end_m=round(re_, 2),
            required_length_m=round(re_ - rs, 2), unit_size_m=round(re_ - rs, 2), n_sites=1,
        ))
    return out


def defect_rate_pct(pipe: Pipe, defects: list[Defect], cfg: dict) -> float:
    """구조적 불량률(%).

    joint_count : 결함 발생 관 본수 / 전체 관 본수 (관종별 1본 길이 적용)
    length      : 병합 보수연장 합계 / 관로연장
    """
    from common.config import pipe_unit_length

    L = pipe.plan_length_m
    if L <= 0 or not defects:
        return 0.0
    if cfg["criteria"].get("defect_rate_method", "joint_count") == "length":
        clusters = build_patch_clusters(pipe, defects, cfg)
        return min(100.0, sum(cl.length_m for cl in clusters) / L * 100)
    unit = pipe_unit_length(cfg, pipe.material)
    total = max(1, math.ceil(L / unit - 1e-9))
    bad: set[int] = set()
    for d in defects:
        a = min(int(d.start_cal // unit), total - 1)
        b = min(int(d.end_cal // unit), total - 1)
        bad.update(range(a, b + 1))
    return len(bad) / total * 100
