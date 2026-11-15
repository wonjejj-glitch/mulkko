"""GIS 레이어(관로 선형·맨홀 점)를 조서 관로에 결합한다.

매칭 순서(관로선): ① 관번 속성 → ② 시·종점 맨홀명 속성 → ③ 공간(조서/SWMM 좌표와 양 끝점 허용오차 이내).
맨홀점: 관로선이 없는 관로의 시·종점 좌표를 맨홀명으로 채운다.
작업 좌표계: 지정값 > 입력이 투영좌표계면 그대로 > 위경도 입력이면 config.gis.default_crs(EPSG:5186)로 변환.
"""
from __future__ import annotations

import math
from pathlib import Path

from common.models import Pipe
from defect_processor.loader import _build_alias_lookup, _map_columns, _s
from gis_link import crs as C
from gis_link.readers import GisLayer, line_vertices, point_xy, read_layer


def _canon(props: dict, lookup: dict) -> dict:
    mapping = _map_columns(list(props.keys()), lookup)
    return {canon: props[col] for col, canon in mapping.items()}


def _d(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _poly_len(vs) -> float:
    return sum(_d(a, b) for a, b in zip(vs, vs[1:]))


def _work_crs(cfg: dict, layers: list[GisLayer], src_override: str | None, work: str | None) -> tuple[str, list[str]]:
    notes: list[str] = []
    default = C.normalize(cfg["gis"]["default_crs"])
    if work:
        return C.normalize(work), notes
    for ly in layers:
        crs = C.normalize(src_override) or ly.crs
        if crs and not C.is_geographic(crs):
            return crs, notes
    return default, notes


def apply_gis(pipes: list[Pipe], cfg: dict, lines: str | None = None, points: str | None = None,
              source_crs: str | None = None, work_crs: str | None = None,
              use_gis_length: bool | None = None) -> tuple[list[str], str | None]:
    """조서 관로에 GIS 선형·좌표를 채우고 (경고 목록, 작업 좌표계)를 반환한다."""
    g = cfg["gis"]
    warns: list[str] = []
    line_layer = read_layer(lines) if lines else None
    point_layer = read_layer(points) if points else None
    layers = [x for x in (line_layer, point_layer) if x is not None]
    if not layers:
        return warns, None
    work, _ = _work_crs(cfg, layers, source_crs, work_crs)
    tol = float(g["match_tolerance_m"])
    use_len = g["use_gis_length"] if use_gis_length is None else use_gis_length
    if C.is_geographic(work):
        warns.append(f"작업 좌표계 {work}가 위경도입니다. 연장·허용오차가 도(°) 단위가 되므로 투영좌표계(EPSG:5186 등)를 지정하세요.")

    def to_work(ly: GisLayer, pts):
        src = C.normalize(source_crs) or ly.crs
        if src is None:
            src = C.normalize(g["default_crs"])
            warns.append(f"{Path(ly.path).name}: 좌표계 정보 없음 → {src} 가정")
        return C.transform_points(pts, src, work)

    pipe_lookup = _build_alias_lookup(cfg["column_aliases"]["pipes"])
    node_lookup = _build_alias_lookup(cfg["gis"]["node_aliases"])
    by_id = {p.pipe_id.lower(): p for p in pipes}
    by_nodes: dict[tuple[str, str], Pipe] = {}
    for p in pipes:
        by_nodes[(p.from_mh, p.to_mh)] = p
        by_nodes[(p.to_mh, p.from_mh)] = p

    # ---------------- 맨홀점 → 이름: 좌표
    node_xy: dict[str, tuple[float, float]] = {}
    node_gl: dict[str, float] = {}
    if point_layer is not None:
        ly = point_layer
        for f in ly.features:
            xy = point_xy(f["geometry"])
            pr = _canon(f["properties"], node_lookup)
            name = _s(pr.get("name"))
            if xy and name:
                node_xy[name] = to_work(ly, [xy])[0]
                try:
                    node_gl[name] = float(pr["ground_el"])
                except (KeyError, TypeError, ValueError):
                    pass

    def orient(vs, p: Pipe):
        """시점→종점 방향으로 정렬(조서/SWMM 좌표 또는 맨홀점 기준)."""
        a = (p.from_x, p.from_y) if p.has_coords else node_xy.get(p.from_mh)
        b = (p.to_x, p.to_y) if p.has_coords else node_xy.get(p.to_mh)
        if a and b:
            fwd, rev = _d(vs[0], a) + _d(vs[-1], b), _d(vs[0], b) + _d(vs[-1], a)
            return (vs[::-1], True) if rev < fwd else (vs, True)
        if a:
            return (vs[::-1], True) if _d(vs[-1], a) < _d(vs[0], a) else (vs, True)
        if b:
            return (vs[::-1], True) if _d(vs[0], b) < _d(vs[-1], b) else (vs, True)
        return vs, False

    matched: dict[str, str] = {}
    unknown_dir: list[str] = []
    n_line = 0
    if line_layer is not None:
        ly = line_layer
        unmatched_feats = 0
        for f in ly.features:
            vs = line_vertices(f["geometry"])
            if not vs or len(vs) < 2:
                continue
            pr = _canon(f["properties"], pipe_lookup)
            pid, fm, tm = _s(pr.get("pipe_id")), _s(pr.get("from_mh")), _s(pr.get("to_mh"))
            p = by_id.get(pid.lower()) if pid else None
            how = "관번"
            if p is None and fm and tm:
                p, how = by_nodes.get((fm, tm)), "맨홀명"
            vs = to_work(ly, vs)
            if p is None:                                            # 공간 매칭
                best = None
                for q in pipes:
                    if not q.has_coords or q.pipe_id in matched:
                        continue
                    a, b = (q.from_x, q.from_y), (q.to_x, q.to_y)
                    err = min(_d(vs[0], a) + _d(vs[-1], b), _d(vs[0], b) + _d(vs[-1], a))
                    if err <= 2 * tol and (best is None or err < best[0]):
                        best = (err, q)
                p, how = (best[1] if best else None), "공간"
            if p is None:
                unmatched_feats += 1
                continue
            if p.pipe_id in matched:
                warns.append(f"[{p.pipe_id}] GIS 관로선이 2개 이상 매칭됨 - 첫 번째만 사용")
                continue
            vs, known = orient(vs, p)
            if not known:
                unknown_dir.append(p.pipe_id)
            p.vertices = [(round(x, 3), round(y, 3)) for x, y in vs]
            p.from_x, p.from_y = p.vertices[0]
            p.to_x, p.to_y = p.vertices[-1]
            matched[p.pipe_id] = how
            n_line += 1
        if unmatched_feats:
            warns.append(f"GIS 관로선 {unmatched_feats}개는 조서 관로와 매칭되지 않음(관번·맨홀명 확인)")

    n_node = 0
    for p in pipes:                                                  # 관로선이 없는 관로 → 맨홀점으로 직선 생성
        if p.pipe_id in matched:
            continue
        a, b = node_xy.get(p.from_mh), node_xy.get(p.to_mh)
        if a and b:
            p.vertices = [(round(a[0], 3), round(a[1], 3)), (round(b[0], 3), round(b[1], 3))]
            p.from_x, p.from_y, p.to_x, p.to_y = a[0], a[1], b[0], b[1]
            matched[p.pipe_id] = "맨홀점"
            n_node += 1
    for p in pipes:
        if p.from_ground_el is None and p.from_mh in node_gl:
            p.from_ground_el = node_gl[p.from_mh]
        if p.to_ground_el is None and p.to_mh in node_gl:
            p.to_ground_el = node_gl[p.to_mh]

    # ---------------- 연장 검토
    ratio = float(g["length_warn_ratio"])
    for p in pipes:
        if p.pipe_id not in matched or not p.vertices or C.is_geographic(work):
            continue
        L = _poly_len(p.vertices)
        if use_len:
            p.plan_length_m = round(L, 2)
        elif p.plan_length_m > 0 and abs(L - p.plan_length_m) / p.plan_length_m > ratio:
            warns.append(f"[{p.pipe_id}] GIS 연장 {L:.2f}m ≠ 조서 도면연장 {p.plan_length_m:.2f}m "
                         f"({(L / p.plan_length_m - 1) * 100:+.1f}%) - 결함 위치는 조서 연장 기준으로 보정됨(--gis-length로 GIS 연장 사용 가능)")
    if unknown_dir:
        warns.append(f"방향 미확인 관로 {len(unknown_dir)}개(GIS 선 방향을 시점→종점으로 간주): " + ", ".join(unknown_dir[:8]))
    miss = [p.pipe_id for p in pipes if p.pipe_id not in matched and not p.has_coords]
    if miss:
        warns.append(f"GIS 좌표를 얻지 못한 관로 {len(miss)}개(DXF 임시배치): " + ", ".join(miss[:8]) + (" ..." if len(miss) > 8 else ""))
    how_cnt: dict[str, int] = {}
    for h in matched.values():
        how_cnt[h] = how_cnt.get(h, 0) + 1
    warns.append(f"GIS 결합({work}): 조서 관로 {len(pipes)}개 중 {len(matched)}개 매칭 (" +
                 ", ".join(f"{k} {v}" for k, v in how_cnt.items()) + ")")
    return warns, work
