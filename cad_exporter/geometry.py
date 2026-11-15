"""관로 선형 위 거리→좌표 변환 및 좌표 미보유 관로의 임시 배치."""
from __future__ import annotations

import math

from common.models import Pipe


def poly_len(vs: list[tuple[float, float]]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(vs, vs[1:]))


def _locate(vs, dist: float, total: float):
    """도면연장 기준 dist(m)를 다중 정점 선형 위의 (점, 진행방향 단위벡터)로 변환(선형 길이에 비례 배분)."""
    pl = poly_len(vs)
    s = 0.0 if total <= 0 else max(0.0, min(1.0, dist / total)) * pl
    acc = 0.0
    for a, b in zip(vs, vs[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if seg <= 0:
            continue
        if s <= acc + seg + 1e-9:
            t = (s - acc) / seg
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), ((b[0] - a[0]) / seg, (b[1] - a[1]) / seg)
        acc += seg
    a, b = vs[-2], vs[-1]
    seg = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    return b, ((b[0] - a[0]) / seg, (b[1] - a[1]) / seg)


def along(vs, dist: float, total: float) -> tuple[float, float]:
    return _locate(vs, dist, total)[0]


def frame_at(vs, dist: float, total: float):
    """(점, 진행방향 단위벡터, 좌측 법선 단위벡터)"""
    pt, u = _locate(vs, dist, total)
    return pt, u, (-u[1], u[0])


def sub_polyline(vs, d0: float, d1: float, total: float) -> list[tuple[float, float]]:
    """도면연장 d0~d1 구간에 해당하는 선형 부분(내부 정점 포함)."""
    pl = poly_len(vs)
    s0 = max(0.0, min(1.0, d0 / total)) * pl if total > 0 else 0.0
    s1 = max(0.0, min(1.0, d1 / total)) * pl if total > 0 else pl
    out = [along(vs, d0, total)]
    acc = 0.0
    for a, b in zip(vs, vs[1:]):
        acc += math.hypot(b[0] - a[0], b[1] - a[1])
        if s0 + 1e-9 < acc < s1 - 1e-9:
            out.append(b)
    out.append(along(vs, d1, total))
    return out


def vec_angle(u) -> float:
    """진행방향 벡터의 글자 회전각(도, -90~90)."""
    a = math.degrees(math.atan2(u[1], u[0]))
    return a - 180 if a > 90 else (a + 180 if a < -90 else a)


def pipe_endpoints(pipes: list[Pipe]) -> dict[str, tuple[tuple[float, float], tuple[float, float], bool]]:
    """관번 → (시점좌표, 종점좌표, 임시배치여부). 좌표 없는 관로는 기존 도면 아래쪽에 행 단위로 배치."""
    out = {}
    with_xy = [p for p in pipes if p.has_coords]
    if with_xy:
        min_x = min(min(p.from_x, p.to_x) for p in with_xy)
        min_y = min(min(p.from_y, p.to_y) for p in with_xy)
    else:
        min_x = min_y = 0.0
    row = 0
    for p in pipes:
        if p.has_coords:
            vs = p.vertices
            if vs and len(vs) >= 2:
                out[p.pipe_id] = (tuple(vs[0][:2]), tuple(vs[-1][:2]), False)
            else:
                out[p.pipe_id] = ((p.from_x, p.from_y), (p.to_x, p.to_y), False)
        else:
            y = min_y - 30.0 - row * 20.0
            out[p.pipe_id] = ((min_x, y), (min_x + p.plan_length_m, y), True)
            row += 1
    return out


def point_at(p0: tuple[float, float], p1: tuple[float, float], dist: float, length: float) -> tuple[float, float]:
    """시점 p0에서 도면연장 기준 dist(m) 떨어진 점. 좌표상 길이가 도면연장과 달라도 비례 배분."""
    t = 0.0 if length <= 0 else max(0.0, min(1.0, dist / length))
    return (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)


def unit_vectors(p0, p1) -> tuple[tuple[float, float], tuple[float, float]]:
    """(진행방향 단위벡터, 좌측 법선 단위벡터)"""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    n = math.hypot(dx, dy) or 1.0
    u = (dx / n, dy / n)
    return u, (-u[1], u[0])


def text_angle(p0, p1) -> float:
    """관로 방향 각도(도). 글자가 뒤집히지 않도록 -90~90°로 정규화."""
    a = math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0]))
    if a > 90:
        a -= 180
    elif a < -90:
        a += 180
    return a
