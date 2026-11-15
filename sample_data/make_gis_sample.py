"""샘플 GIS 레이어 생성 - 조서(make_sample.py)와 같은 관번·맨홀명 사용.

  sample_gis_lines.geojson   관로선(WGS84, 일부 관로는 꺾인 선형, P-003은 역방향 digitizing)
  sample_gis_lines.shp/.prj  관로선(EPSG:5186, 한글 속성은 CP949)
  sample_gis_manholes.shp    맨홀점(EPSG:5186, 맨홀번호·지반고)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import shapefile

from gis_link import crs as C
from sample_data.make_sample import PIPES, _coords
from sample_data.make_swmm_sample import _pipe_elevations

CURVED = {"P-001": 3.0, "P-004": -3.0}      # 중간 정점을 관로 직각 방향으로 밀어 꺾인 선형 생성(m)
REVERSED = {"P-003"}                        # 흐름 반대로 그려진 선


def _node_xy() -> tuple[dict, dict]:
    coords = _coords()
    xy: dict[str, tuple[float, float]] = {}
    for pid, ((x0, y0), (x1, y1)) in coords.items():
        row = next(r for r in PIPES if r[0] == pid)
        xy[row[1]], xy[row[2]] = (x0, y0), (x1, y1)
    for row in PIPES:                       # P-011(조서에 좌표 없음): MH-12 남쪽으로 연장만큼
        pid, f, t, L = row[0], row[1], row[2], row[5]
        if pid not in coords and f not in xy and t in xy:
            xy[f] = (xy[t][0], round(xy[t][1] - L, 3))
    return coords, xy


def _lines_5186() -> list[tuple[str, list[tuple[float, float]]]]:
    _coords_, xy = _node_xy()
    out = []
    for row in PIPES:
        pid, f, t = row[:3]
        (x0, y0), (x1, y1) = xy[f], xy[t]
        vs = [(x0, y0), (x1, y1)]
        if pid in CURVED:
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            dx, dy = x1 - x0, y1 - y0
            n = math.hypot(dx, dy)
            off = CURVED[pid]
            vs = [(x0, y0), (mx - dy / n * off, my + dx / n * off), (x1, y1)]
        if pid in REVERSED:
            vs = vs[::-1]
        out.append((pid, [(round(x, 3), round(y, 3)) for x, y in vs]))
    return out


def make_gis_sample(out_dir: str | Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = _lines_5186()
    row_by = {r[0]: r for r in PIPES}
    prj = C.prj_text("EPSG:5186")
    written: list[Path] = []

    # GeoJSON (WGS84)
    feats = []
    for pid, vs in lines:
        ll = C.transform_points(vs, "EPSG:5186", "EPSG:4326")
        r = row_by[pid]
        feats.append({"type": "Feature", "properties": {"관번": pid, "관종": r[3], "관경": r[4]},
                      "geometry": {"type": "LineString", "coordinates": [[round(x, 7), round(y, 7)] for x, y in ll]}})
    gj = out_dir / "sample_gis_lines.geojson"
    gj.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")
    written.append(gj)

    # Shapefile: 관로선 (5186)
    base = out_dir / "sample_gis_lines"
    w = shapefile.Writer(str(base), shapeType=shapefile.POLYLINE, encoding="cp949")
    w.field("관번", "C", 12)
    w.field("관종", "C", 12)
    w.field("관경", "N", 6)
    for pid, vs in lines:
        w.line([[list(v) for v in vs]])
        w.record(pid, row_by[pid][3], row_by[pid][4])
    w.close()
    base.with_suffix(".prj").write_text(prj, encoding="ascii", errors="ignore")
    base.with_suffix(".cpg").write_text("CP949", encoding="ascii")
    written.append(base.with_suffix(".shp"))

    # Shapefile: 맨홀점 (5186)
    _c, xy = _node_xy()
    elev = _pipe_elevations()
    gl: dict[str, float] = {}
    for row in PIPES:
        _il_f, _il_t, gf, gt = elev[row[0]]
        gl[row[1]] = max(gl.get(row[1], -1e9), gf)
        gl[row[2]] = max(gl.get(row[2], -1e9), gt)
    base = out_dir / "sample_gis_manholes"
    w = shapefile.Writer(str(base), shapeType=shapefile.POINT, encoding="cp949")
    w.field("맨홀번호", "C", 12)
    w.field("지반고", "N", 9, 3)
    for name, (x, y) in sorted(xy.items()):
        w.point(x, y)
        w.record(name, gl[name])
    w.close()
    base.with_suffix(".prj").write_text(prj, encoding="ascii", errors="ignore")
    base.with_suffix(".cpg").write_text("CP949", encoding="ascii")
    written.append(base.with_suffix(".shp"))
    return written


if __name__ == "__main__":
    for p in make_gis_sample(Path(__file__).parent):
        print("생성:", p)
