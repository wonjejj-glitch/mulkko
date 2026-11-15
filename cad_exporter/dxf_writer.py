"""방식 A: ezdxf 기반 .dxf 직접 생성 (평면도 결함 주석 + 종단면도 + 결함/보수 테이블)."""
from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment

from cad_exporter.annotate import defect_color, defect_note, safe_layer
from cad_exporter.geometry import frame_at, pipe_endpoints, sub_polyline, vec_angle
from common.models import (M_CIPP, M_PARTIAL_DIG, M_REPLACE, M_UPSIZE, METHODS, Pipe, PipeDecision,
                           ProjectResult)

STYLE = "GP_KOR"
L_MH = "PLAN_맨홀"
L_LABEL = "PLAN_관로제원"
L_MARK = "PLAN_결함마커"
L_NOTE = "PLAN_결함주석"
L_PATCH = "PLAN_부분보수구간"
L_DIG = "PLAN_부분굴착구간"
P_GROUND = "PROF_지반선"
P_PIPE = "PROF_관로"
P_EXC = "PROF_굴착선"
P_MH = "PROF_맨홀"
P_HATCH_PATCH = "PROF_보수구간해치"
P_HATCH_DIG = "PROF_굴착구간해치"
P_TABLE = "PROF_테이블"
P_TEXT = "PROF_문자"
P_DEFECT = "PROF_결함"


def _method_layer(m: str) -> str:
    return safe_layer(f"PLAN_관로_{m}")


def _setup(doc, cfg: dict) -> None:
    c = cfg["cad"]
    doc.styles.new(STYLE, dxfattribs={"font": c.get("font_file", "malgun.ttf")})
    for m in METHODS:
        doc.layers.new(_method_layer(m), dxfattribs={
            "color": c["method_colors"].get(m, 7),
            "linetype": c["method_linetypes"].get(m, "CONTINUOUS"),
            "lineweight": 50,
        })
    for name, color in [(L_MH, 7), (L_LABEL, 7), (L_MARK, 2), (L_NOTE, 7), (L_PATCH, 3), (L_DIG, 30),
                        (P_GROUND, 34), (P_PIPE, 7), (P_EXC, 30), (P_MH, 7), (P_HATCH_PATCH, 3),
                        (P_HATCH_DIG, 30), (P_TABLE, 8), (P_TEXT, 7), (P_DEFECT, 2)]:
        doc.layers.new(name, dxfattribs={"color": color})
    doc.layers.get(P_EXC).dxf.linetype = "DASHED"


def _text(msp, txt, pos, h, layer, rot=0.0, align=TextEntityAlignment.LEFT, color=None):
    attrs = {"layer": layer, "style": STYLE, "rotation": rot}
    if color is not None:
        attrs["color"] = color
    t = msp.add_text(txt, height=h, dxfattribs=attrs)
    t.set_placement(pos, align=align)
    return t


def _rect(msp, x0, y0, x1, y1, layer, color=None):
    attrs = {"layer": layer}
    if color is not None:
        attrs["color"] = color
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs=attrs)


def _hatch(msp, pts, layer, color, pattern: str | None = None, scale: float = 0.5):
    h = msp.add_hatch(color=color, dxfattribs={"layer": layer})
    if pattern:
        h.set_pattern_fill(pattern, scale=scale)
    h.paths.add_polyline_path(pts, is_closed=True)
    return h


# ============================================================ 평면도
def _draw_plan(msp, pipe: Pipe, dec: PipeDecision, ends, cfg: dict) -> None:
    c = cfg["cad"]
    th = float(c["plan_text_height"])
    r = float(c["marker_radius"])
    p0, p1, temp = ends
    L = pipe.plan_length_m
    mlay = _method_layer(dec.method)
    # GIS 선형(다중 정점)이 있으면 그대로 사용, 없으면 시·종점 직선
    vs = [tuple(v[:2]) for v in pipe.vertices] if (pipe.vertices and len(pipe.vertices) >= 2 and not temp) else [p0, p1]

    msp.add_lwpolyline(vs, dxfattribs={"layer": mlay})
    # 전체 공법 구간 강조(굵은 폴리선)
    if dec.method in (M_CIPP, M_REPLACE, M_UPSIZE):
        msp.add_lwpolyline(vs, dxfattribs={"layer": mlay, "const_width": r * 1.2})
    for cl in dec.patch_clusters:
        msp.add_lwpolyline(sub_polyline(vs, cl.repair_start_m, cl.repair_end_m, L),
                           dxfattribs={"layer": L_PATCH, "const_width": r * 1.4})
    for cl in dec.dig_clusters if dec.method == M_PARTIAL_DIG else []:
        msp.add_lwpolyline(sub_polyline(vs, cl.repair_start_m, cl.repair_end_m, L),
                           dxfattribs={"layer": L_DIG, "const_width": r * 2.0})

    # 맨홀
    for pt, name in ((p0, pipe.from_mh), (p1, pipe.to_mh)):
        msp.add_circle(pt, r * 1.4, dxfattribs={"layer": L_MH})
        _text(msp, name, (pt[0] + r * 1.6, pt[1] + r * 1.6), th, L_MH)

    # 관로 제원 라벨 (관로 중앙, 관로 방향 회전, 우측 법선 방향으로 띄움)
    mid, u_mid, nrm = frame_at(vs, L / 2, L)
    ang = vec_angle(u_mid)
    off = th * 1.8
    lbl_pos = (mid[0] - nrm[0] * off, mid[1] - nrm[1] * off)
    tag = " (좌표없음-임시배치)" if temp else ""
    _text(msp, f"{pipe.pipe_id} {pipe.material} D{pipe.diameter_mm} L={L:.1f}m{tag}", lbl_pos, th, L_LABEL,
          rot=ang, align=TextEntityAlignment.TOP_CENTER)
    _text(msp, f"[{dec.method}] {dec.detail}", (lbl_pos[0] - nrm[0] * th * 1.6, lbl_pos[1] - nrm[1] * th * 1.6),
          th * 0.9, mlay, rot=ang, align=TextEntityAlignment.TOP_CENTER)

    # 결함 마커 + 지시선(LEADER) 주석 — 관로 좌측(법선 방향)으로 계단식 배치하여 겹침 방지
    for i, d in enumerate(pipe.defects):
        pt, u, nrm = frame_at(vs, d.distance_cal, L)
        ang = vec_angle(u)
        col = defect_color(d)
        msp.add_circle(pt, r, dxfattribs={"layer": L_MARK, "color": col})
        msp.add_line((pt[0] - r * 0.7, pt[1] - r * 0.7), (pt[0] + r * 0.7, pt[1] + r * 0.7),
                     dxfattribs={"layer": L_MARK, "color": col})
        if d.end_distance_cal is not None:
            msp.add_lwpolyline(sub_polyline(vs, d.start_cal, d.end_cal, L),
                               dxfattribs={"layer": L_MARK, "color": col, "const_width": r * 0.5})
        dist = th * (3.0 + (i % 4) * 2.2)
        elbow = (pt[0] + nrm[0] * dist, pt[1] + nrm[1] * dist)
        rd = u if u[0] >= -1e-9 else (-u[0], -u[1])  # 글자 읽는 방향
        tail = (elbow[0] + rd[0] * th * 1.5, elbow[1] + rd[1] * th * 1.5)
        msp.add_leader([pt, elbow, tail], dimstyle="EZDXF",
                       override={"dimasz": th * 0.6, "dimclrd": col}, dxfattribs={"layer": L_NOTE})
        _text(msp, defect_note(dec, d), (tail[0] + rd[0] * th * 0.3, tail[1] + rd[1] * th * 0.3), th * 0.85,
              L_NOTE, rot=ang, align=TextEntityAlignment.MIDDLE_LEFT, color=col)


# ============================================================ 종단면도
ROW_DEFS = [("측점(m)", 4.0), ("지반고(EL.m)", 2.5), ("관저고(EL.m)", 2.5), ("심도(m)", 2.5),
            ("결함(코드·등급)", 4.0), ("보수구간·공법", 3.0)]
LABEL_W = 16.0


def _profile_size(pipe: Pipe, cfg: dict) -> tuple[float, float]:
    V = float(cfg["cad"]["profile_vertical_exaggeration"])
    fg, fi, tg, ti, _ = pipe.elevations()
    datum = math.floor(min(fi, ti) - 1.0)
    graph_h = (max(fg, tg) - datum) * V + 8
    table_h = sum(h for _, h in ROW_DEFS)
    return LABEL_W + pipe.plan_length_m + 6, graph_h + table_h + 6


def _draw_profile(msp, pipe: Pipe, dec: PipeDecision, origin: tuple[float, float], cfg: dict) -> None:
    c = cfg["cad"]
    V = float(c["profile_vertical_exaggeration"])
    th = float(c["profile_text_height"])
    found = float(cfg["excavation"]["foundation_thickness_m"])
    x0, y0 = origin
    L = pipe.plan_length_m
    D = pipe.diameter_mm / 1000.0
    fg, fi, tg, ti, assumed = pipe.elevations()
    datum = math.floor(min(fi, ti) - 1.0)
    table_h = sum(h for _, h in ROW_DEFS)
    yg = y0 + table_h + 2.0
    gx = x0 + LABEL_W

    def X(s: float) -> float:
        return gx + s

    def Y(el: float) -> float:
        return yg + (el - datum) * V

    def inv(s: float) -> float:
        return fi + (ti - fi) * (s / L if L else 0)

    def grd(s: float) -> float:
        return fg + (tg - fg) * (s / L if L else 0)

    mcol = c["method_colors"].get(dec.method, 7)
    # 제목
    title = (f"종단면도  {pipe.pipe_id}  ({pipe.from_mh} → {pipe.to_mh})  {pipe.material} D{pipe.diameter_mm}  "
             f"L={L:.1f}m  [{dec.method}]  (수직 {V:g}배 과장)")
    if assumed:
        title += "  ※지반고 가정값"
    _ymax = Y(max(fg, tg)) + 3
    _text(msp, title, (x0, _ymax + 1.0), th * 1.3, P_TEXT)
    _text(msp, f"DATUM EL.{datum:.1f}", (x0, yg), th, P_TEXT)

    # 보수 구간 해치 (관 단면 띠)
    def band(s0, s1, bottom_off=0.0, top_el_fn=None):
        pts = [(X(s0), Y(inv(s0)) - bottom_off), (X(s1), Y(inv(s1)) - bottom_off)]
        if top_el_fn is None:
            pts += [(X(s1), Y(inv(s1) + D)), (X(s0), Y(inv(s0) + D))]
        else:
            pts += [(X(s1), Y(top_el_fn(s1))), (X(s0), Y(top_el_fn(s0)))]
        return pts

    if dec.method in (M_CIPP, M_REPLACE, M_UPSIZE):
        if dec.method == M_CIPP:
            _hatch(msp, band(0, L), P_HATCH_PATCH, mcol)
        else:
            _hatch(msp, band(0, L, bottom_off=found * V, top_el_fn=grd), P_HATCH_DIG, mcol, "ANSI31", 1.0)
    for cl in dec.dig_clusters if dec.method == M_PARTIAL_DIG else []:
        _hatch(msp, band(cl.repair_start_m, cl.repair_end_m, bottom_off=found * V, top_el_fn=grd),
               P_HATCH_DIG, c["method_colors"].get(M_PARTIAL_DIG, 30), "ANSI31", 1.0)
    if dec.method == M_PARTIAL_DIG and dec.sub_method == M_CIPP:
        _hatch(msp, band(0, L), P_HATCH_PATCH, c["method_colors"].get(M_CIPP, 5))
    for cl in dec.patch_clusters:
        _hatch(msp, band(cl.repair_start_m, cl.repair_end_m), P_HATCH_PATCH, 3)

    # 지반선, 관저선, 관정선, 굴착선
    msp.add_line((X(0), Y(fg)), (X(L), Y(tg)), dxfattribs={"layer": P_GROUND})
    msp.add_line((X(0), Y(fi)), (X(L), Y(ti)), dxfattribs={"layer": P_PIPE, "color": mcol})
    msp.add_line((X(0), Y(fi + D)), (X(L), Y(ti + D)), dxfattribs={"layer": P_PIPE, "color": mcol})
    if dec.method in (M_REPLACE, M_UPSIZE):
        msp.add_line((X(0), Y(fi - found)), (X(L), Y(ti - found)), dxfattribs={"layer": P_EXC})
        if dec.method == M_UPSIZE and dec.new_diameter_mm:
            Dn = dec.new_diameter_mm / 1000.0
            msp.add_line((X(0), Y(fi + Dn)), (X(L), Y(ti + Dn)), dxfattribs={"layer": P_EXC, "color": 1})
            _text(msp, f"신설 D{dec.new_diameter_mm}", (X(L / 2), Y(inv(L / 2) + Dn) + th), th, P_TEXT,
                  align=TextEntityAlignment.BOTTOM_CENTER, color=1)
    gs = abs(fi - ti) / L * 1000 if L else 0
    slope_txt = f"I={'+' if ti > fi else '-'}{gs:.1f}‰  {pipe.material} D{pipe.diameter_mm}  L={L:.2f}m"
    _text(msp, slope_txt, (X(L / 2), Y(inv(L / 2) + D) + th * 0.8), th, P_TEXT, align=TextEntityAlignment.BOTTOM_CENTER)

    # 맨홀
    for s, g, i_, name in ((0.0, fg, fi, pipe.from_mh), (L, tg, ti, pipe.to_mh)):
        _rect(msp, X(s) - 0.6, Y(i_ - 0.3), X(s) + 0.6, Y(g), P_MH)
        _text(msp, name, (X(s), Y(g) + th * 0.6), th, P_MH, align=TextEntityAlignment.BOTTOM_CENTER)

    # 결함 위치 표시(관정 위 수직 지시선)
    for i, d in enumerate(pipe.defects):
        s = d.distance_cal
        col = defect_color(d)
        top = Y(inv(s) + D)
        msp.add_line((X(s), Y(inv(s))), (X(s), top + 2.0 + (i % 3) * 1.2), dxfattribs={"layer": P_DEFECT, "color": col})
        msp.add_circle((X(s), top), 0.35, dxfattribs={"layer": P_DEFECT, "color": col})
        _text(msp, f"{d.code}{d.level}", (X(s) + 0.2, top + 2.0 + (i % 3) * 1.2), th * 0.85, P_DEFECT,
              align=TextEntityAlignment.BOTTOM_LEFT, color=col)

    # ---------------- 하단 테이블
    total_w = LABEL_W + L
    y = y0 + table_h
    rows_y = {}
    for name, h in ROW_DEFS:
        msp.add_line((x0, y), (x0 + total_w, y), dxfattribs={"layer": P_TABLE})
        rows_y[name] = (y - h, y)
        _text(msp, name, (x0 + LABEL_W / 2, y - h / 2), th, P_TABLE, align=TextEntityAlignment.MIDDLE_CENTER, color=7)
        y -= h
    msp.add_line((x0, y0), (x0 + total_w, y0), dxfattribs={"layer": P_TABLE})
    for xx in (x0, gx, gx + L):
        msp.add_line((xx, y0), (xx, y0 + table_h), dxfattribs={"layer": P_TABLE})

    def cell(row: str, s: float, txt: str, rot: float = 0.0, color=None):
        yb, yt = rows_y[row]
        _text(msp, txt, (X(s), (yb + yt) / 2), th * 0.85, P_TEXT, rot=rot,
              align=TextEntityAlignment.MIDDLE_CENTER, color=color)

    for s in (0.0, L):
        cell("측점(m)", s, f"{s:.2f}", rot=90)
        cell("지반고(EL.m)", s, f"{grd(s):.2f}")
        cell("관저고(EL.m)", s, f"{inv(s):.2f}")
        cell("심도(m)", s, f"{grd(s) - inv(s):.2f}")
    for d in pipe.defects:
        s = d.distance_cal
        if 0.5 < s < L - 0.5:
            cell("측점(m)", s, f"{s:.2f}", rot=90, color=defect_color(d))
        cell("결함(코드·등급)", s, f"{d.code}-{d.level}", rot=90, color=defect_color(d))

    yb, yt = rows_y["보수구간·공법"]
    ym = (yb + yt) / 2

    def span(s0: float, s1: float, txt: str, col: int):
        msp.add_line((X(s0), ym - 0.6), (X(s1), ym - 0.6), dxfattribs={"layer": P_TABLE, "color": col})
        for s in (s0, s1):
            msp.add_line((X(s), yb + 0.3), (X(s), yt - 0.3), dxfattribs={"layer": P_TABLE, "color": col})
        _text(msp, txt, (X((s0 + s1) / 2), ym + 0.2), th * 0.75, P_TEXT, align=TextEntityAlignment.MIDDLE_CENTER, color=col)

    if dec.method in (M_CIPP, M_REPLACE, M_UPSIZE):
        span(0, L, f"{dec.method} L={L:.1f}m", mcol)
    else:
        if dec.method == M_PARTIAL_DIG and dec.sub_method == M_CIPP:
            span(0, L, f"비굴착 전체보수(굴착구간 제외)", c["method_colors"].get(M_CIPP, 5))
        for cl in dec.dig_clusters:
            span(cl.repair_start_m, cl.repair_end_m, f"부분굴착#{cl.cluster_no} {cl.length_m:.1f}m", 30)
        for cl in dec.patch_clusters:
            span(cl.repair_start_m, cl.repair_end_m, f"#{cl.cluster_no} {cl.unit_size_m:.1f}m" + ("×" + str(cl.n_sites) if cl.n_sites > 1 else ""), 3)
        if not dec.dig_clusters and not dec.patch_clusters:
            span(0, L, dec.method, mcol)


# ============================================================ 범례
def _draw_legend(msp, origin, cfg: dict) -> None:
    th = float(cfg["cad"]["plan_text_height"])
    x, y = origin
    _text(msp, "범례 (정비공법)", (x, y), th * 1.3, L_LABEL)
    for i, m in enumerate(METHODS):
        yy = y - (i + 1) * th * 2.2
        msp.add_lwpolyline([(x, yy), (x + th * 6, yy)], dxfattribs={"layer": _method_layer(m), "const_width": th * 0.5})
        _text(msp, m, (x + th * 7, yy), th, L_LABEL, align=TextEntityAlignment.MIDDLE_LEFT)
    yy = y - (len(METHODS) + 2) * th * 2.2
    for j, (lbl, col) in enumerate([("시공불가 결함", 1), ("보수대상 결함", 2), ("운영결함(준설)", 4), ("경미결함", 8)]):
        msp.add_circle((x + th, yy - j * th * 2.0), th * 0.5, dxfattribs={"layer": L_MARK, "color": col})
        _text(msp, lbl, (x + th * 7, yy - j * th * 2.0), th, L_LABEL, align=TextEntityAlignment.MIDDLE_LEFT)


def export_dxf(result: ProjectResult, path: str | Path) -> Path:
    cfg = result.config
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 6  # meters
    _setup(doc, cfg)
    msp = doc.modelspace()

    ends = pipe_endpoints(result.pipes)
    for p in result.pipes:
        _draw_plan(msp, p, result.decisions[p.pipe_id], ends[p.pipe_id], cfg)

    xs = [pt[0] for e in ends.values() for pt in e[:2]] or [0.0]
    ys = [pt[1] for e in ends.values() for pt in e[:2]] or [0.0]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    _draw_legend(msp, (max_x + 30, max_y), cfg)

    # 종단면도는 평면도 하단에 격자 배치
    per_row = int(cfg["cad"]["profiles_per_row"])
    gap = float(cfg["cad"]["profile_gap"])
    th = float(cfg["cad"]["plan_text_height"])
    _text(msp, "■ 관로별 종단면도 및 결함·보수구간 테이블", (min_x, min_y - 40), th * 2, P_TEXT)
    cursor_y = min_y - 50
    for r0 in range(0, len(result.pipes), per_row):
        row = result.pipes[r0:r0 + per_row]
        sizes = [_profile_size(p, cfg) for p in row]
        row_h = max(h for _, h in sizes)
        x = min_x
        for p, (w, _h) in zip(row, sizes):
            _draw_profile(msp, p, result.decisions[p.pipe_id], (x, cursor_y - row_h), cfg)
            x += w + gap
        cursor_y -= row_h + gap

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path
