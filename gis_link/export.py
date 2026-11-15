"""결과를 GIS로 내보내기: GeoJSON(WGS84, 웹지도·geojson.io·OpenGISpipe용)과 Shapefile(작업 좌표계, QGIS/ArcGIS용)."""
from __future__ import annotations

import json
from pathlib import Path

from cad_exporter.annotate import defect_action
from cad_exporter.geometry import along
from common.models import ProjectResult
from gis_link import crs as C


def _collect(res: ProjectResult):
    """(관로 [(pipe, dec, 정점)], 결함 점 [dict], 맨홀 {이름: (x, y, 지반고)}) — 작업 좌표계 기준."""
    pipes, defects, mh = [], [], {}
    skipped = 0
    for p in res.pipes:
        if not p.has_coords:
            skipped += 1
            continue
        vs = [tuple(v[:2]) for v in p.vertices] if p.vertices and len(p.vertices) >= 2 else [(p.from_x, p.from_y), (p.to_x, p.to_y)]
        dec = res.decisions[p.pipe_id]
        pipes.append((p, dec, vs))
        for name, pt, gl in ((p.from_mh, vs[0], p.from_ground_el), (p.to_mh, vs[-1], p.to_ground_el)):
            if name and name != "-" and name not in mh:
                mh[name] = (pt[0], pt[1], gl)
        for d in p.defects:
            x, y = along(vs, d.distance_cal, p.plan_length_m)
            defects.append({"x": x, "y": y, "pipe": p.pipe_id, "dist": d.distance_cal, "code": d.code, "name": d.name,
                            "grade": d.level, "score": d.score, "kind": "구조" if d.state == "structural" else "운영",
                            "repair": d.is_repair_target, "blocker": d.is_blocker, "action": defect_action(dec, d)})
    return pipes, defects, mh, skipped


def _work_crs(res: ProjectResult) -> str:
    return C.normalize(res.crs) or C.normalize(res.config["gis"]["default_crs"])


def export_geojson(res: ProjectResult, path: str | Path) -> tuple[Path, list[str]]:
    """관로(LineString)·결함(Point)·맨홀(Point)을 한 파일의 FeatureCollection(WGS84)으로 저장."""
    cfg = res.config
    work = _work_crs(res)
    pipes, defects, mh, skipped = _collect(res)
    colors = cfg["gis"]["method_colors_hex"]
    warns = [f"좌표 없는 관로 {skipped}개는 GIS 출력에서 제외"] if skipped else []

    def ll(pts):
        out = C.transform_points(pts, work, "EPSG:4326")
        return [[round(x, 7), round(y, 7)] for x, y in out]

    feats = []
    for p, dec, vs in pipes:
        feats.append({
            "type": "Feature", "geometry": {"type": "LineString", "coordinates": ll(vs)},
            "properties": {
                "레이어": "관로", "관번": p.pipe_id, "시점맨홀": p.from_mh, "종점맨홀": p.to_mh, "관종": p.material,
                "관경_mm": p.diameter_mm, "연장_m": round(p.plan_length_m, 2), "정비공법": dec.method, "세부내용": dec.detail,
                "선정사유": dec.reason, "구조적상태점수": dec.condition_score, "최대본등급": int(dec.condition_grade),
                "운영적상태점수": dec.oper_score, "정비우선순위": dec.priority,
                "개량관경_mm": dec.new_diameter_mm, "공사비_원": round(dec.total_cost),
                "통수능부족": bool(p.capacity_insufficient),
                "stroke": colors.get(dec.method, "#555555"), "stroke-width": 4 if dec.method != "개량제외" else 2,
            }})
    for d in defects:
        feats.append({
            "type": "Feature", "geometry": {"type": "Point", "coordinates": ll([(d["x"], d["y"])])[0]},
            "properties": {
                "레이어": "결함", "관번": d["pipe"], "보정위치_m": d["dist"], "결함코드": d["code"], "결함명": d["name"],
                "등급": d["grade"], "결함점수": d["score"], "구분": d["kind"], "보수대상": d["repair"], "비굴착불가": d["blocker"], "적용공법": d["action"],
                "marker-color": "#d62728" if d["blocker"] else ("#f2c200" if d["repair"] else "#9e9e9e"), "marker-size": "small",
            }})
    for name, (x, y, gl) in mh.items():
        feats.append({
            "type": "Feature", "geometry": {"type": "Point", "coordinates": ll([(x, y)])[0]},
            "properties": {"레이어": "맨홀", "맨홀명": name, "지반고": gl, "marker-color": "#444444", "marker-size": "small"}})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "name": "노후하수관로_정비", "features": feats},
                               ensure_ascii=False), encoding="utf-8")
    return path, warns


def export_shapefile(res: ProjectResult, out_dir: str | Path, stem: str = "sewer_rehab") -> tuple[list[Path], list[str]]:
    """`{stem}_pipes/_defects/_manholes.shp`를 작업 좌표계로 저장(.prj, CP949 .cpg 포함)."""
    import shapefile

    work = _work_crs(res)
    pipes, defects, mh, skipped = _collect(res)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    warns = [f"좌표 없는 관로 {skipped}개는 GIS 출력에서 제외"] if skipped else []
    prj = C.prj_text(work)
    written: list[Path] = []

    def clip(s, n=110):
        return (s or "")[:n].rstrip()           # 끝 공백은 DBF 패딩과 구분되지 않아 제거

    def finish(w, base: Path):
        w.close()
        base.with_suffix(".prj").write_text(prj, encoding="ascii", errors="ignore")
        base.with_suffix(".cpg").write_text("CP949", encoding="ascii")
        written.append(base.with_suffix(".shp"))

    base = out_dir / f"{stem}_pipes"
    w = shapefile.Writer(str(base), shapeType=shapefile.POLYLINE, encoding="cp949")
    for name, t, size, dec_ in [("PIPE_ID", "C", 20, 0), ("FROM_MH", "C", 20, 0), ("TO_MH", "C", 20, 0), ("MATERIAL", "C", 16, 0),
                                ("DIA_MM", "N", 6, 0), ("LEN_M", "N", 10, 2), ("METHOD", "C", 24, 0), ("DETAIL", "C", 80, 0),
                                ("REASON", "C", 120, 0), ("GRADE", "C", 2, 0), ("STATE", "N", 5, 2), ("PRIORITY", "N", 4, 0), ("NEW_DIA", "N", 6, 0),
                                ("COST", "N", 14, 0), ("CAP_SHORT", "C", 1, 0)]:
        w.field(name, t, size, dec_)
    for p, dec, vs in pipes:
        w.line([[list(v) for v in vs]])
        w.record(p.pipe_id, p.from_mh, p.to_mh, p.material, p.diameter_mm, round(p.plan_length_m, 2), dec.method,
                 clip(dec.detail, 38), clip(dec.reason, 56), dec.condition_grade, dec.condition_score, dec.priority, dec.new_diameter_mm or 0,
                 round(dec.total_cost), "Y" if p.capacity_insufficient else "")
    finish(w, base)

    base = out_dir / f"{stem}_defects"
    w = shapefile.Writer(str(base), shapeType=shapefile.POINT, encoding="cp949")
    for name, t, size, dec_ in [("PIPE_ID", "C", 20, 0), ("DIST_M", "N", 8, 2), ("CODE", "C", 6, 0), ("NAME", "C", 20, 0),
                                ("GRADE", "C", 2, 0), ("SCORE", "N", 4, 0), ("KIND", "C", 6, 0), ("ACTION", "C", 40, 0),
                                ("BLOCKER", "C", 1, 0)]:
        w.field(name, t, size, dec_)
    for d in defects:
        w.point(d["x"], d["y"])
        w.record(d["pipe"], d["dist"], d["code"], d["name"], d["grade"], d["score"], d["kind"], clip(d["action"], 18),
                 "Y" if d["blocker"] else "")
    finish(w, base)

    base = out_dir / f"{stem}_manholes"
    w = shapefile.Writer(str(base), shapeType=shapefile.POINT, encoding="cp949")
    w.field("NAME", "C", 20)
    w.field("GROUND_EL", "N", 9, 3)
    for name, (x, y, gl) in mh.items():
        w.point(x, y)
        w.record(name, gl if gl is not None else 0)
    finish(w, base)
    return written, warns
