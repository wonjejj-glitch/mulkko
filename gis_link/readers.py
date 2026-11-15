"""GIS 레이어 읽기: GeoJSON, Shapefile(.shp), 압축(.zip, 안에 .shp 또는 .geojson)."""
from __future__ import annotations

import json
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gis_link import crs as C


@dataclass
class GisLayer:
    path: str
    features: list[dict[str, Any]] = field(default_factory=list)   # {"geometry": {...}|None, "properties": {...}}
    crs: str | None = None
    crs_source: str = ""        # 좌표계 판별 근거(메시지용)


def _first_xy(geom: dict | None):
    """지오메트리의 첫 좌표(좌표계 추정용)."""
    c = (geom or {}).get("coordinates")
    while isinstance(c, (list, tuple)) and c and isinstance(c[0], (list, tuple)):
        c = c[0]
    return c if isinstance(c, (list, tuple)) and len(c) >= 2 else None


def _guess_geographic(features: list[dict]) -> bool:
    for f in features[:20]:
        xy = _first_xy(f.get("geometry"))
        if xy and not (-180 <= xy[0] <= 180 and -90 <= xy[1] <= 90):
            return False
    return bool(features)


def _read_geojson(path: Path) -> GisLayer:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            obj = json.loads(raw.decode(enc))
            break
        except (UnicodeDecodeError, json.JSONDecodeError):
            obj = None
    if obj is None:
        raise ValueError(f"{path.name}: GeoJSON을 읽을 수 없습니다.")
    feats = obj["features"] if obj.get("type") == "FeatureCollection" else [obj] if obj.get("type") == "Feature" else []
    feats = [{"geometry": f.get("geometry"), "properties": dict(f.get("properties") or {})} for f in feats]
    layer = GisLayer(str(path), feats)
    name = ((obj.get("crs") or {}).get("properties") or {}).get("name")
    if name:
        layer.crs, layer.crs_source = C.normalize(name), "GeoJSON crs 항목"
    elif _guess_geographic(feats):
        layer.crs, layer.crs_source = "EPSG:4326", "좌표 범위로 위경도 추정(GeoJSON 표준)"
    return layer


def _read_shp(path: Path) -> GisLayer:
    import shapefile      # pyshp

    cpg = path.with_suffix(".cpg")
    enc = "cp949"
    if cpg.exists():
        t = cpg.read_text(errors="ignore").strip().lower()
        enc = {"utf-8": "utf-8", "utf8": "utf-8", "65001": "utf-8", "949": "cp949", "cp949": "cp949"}.get(t, t or "cp949")
    reader = shapefile.Reader(str(path), encoding=enc, encodingErrors="replace")
    names = [f[0] for f in reader.fields[1:]]
    feats = []
    for sr in reader.iterShapeRecords():
        geom = sr.shape.__geo_interface__ if sr.shape.shapeType else None
        feats.append({"geometry": geom, "properties": dict(zip(names, list(sr.record)))})
    layer = GisLayer(str(path), feats)
    prj = path.with_suffix(".prj")
    if prj.exists():
        layer.crs = C.crs_from_prj(prj.read_text(errors="ignore"))
        layer.crs_source = ".prj" if layer.crs else ".prj 해석 실패"
    return layer


def read_layer(path: str | Path) -> GisLayer:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="gis_"))
        with zipfile.ZipFile(p) as z:
            for m in z.namelist():
                target = (tmp / m).resolve()
                if tmp.resolve() not in target.parents and target != tmp.resolve():
                    raise ValueError("압축 파일에 허용되지 않는 경로가 있습니다.")
            z.extractall(tmp)
        found = sorted(tmp.rglob("*.shp")) or sorted(tmp.rglob("*.geojson")) + sorted(tmp.rglob("*.json"))
        if not found:
            raise ValueError(f"{p.name}: 압축 안에서 .shp/.geojson을 찾지 못했습니다.")
        return read_layer(found[0])
    if suffix in (".geojson", ".json"):
        return _read_geojson(p)
    if suffix == ".shp":
        return _read_shp(p)
    raise ValueError(f"지원하지 않는 GIS 형식: {p.suffix} (.geojson/.json/.shp/.zip)")


def line_vertices(geom: dict | None) -> list[tuple[float, float]] | None:
    """LineString/MultiLineString(연속 이어붙임)의 정점 목록. 선형이 아니면 None."""
    if not geom:
        return None
    t, c = geom.get("type"), geom.get("coordinates")
    if t == "LineString":
        return [(p[0], p[1]) for p in c]
    if t == "MultiLineString":
        out: list[tuple[float, float]] = []
        for part in c:
            for p in part:
                if not out or (p[0], p[1]) != out[-1]:
                    out.append((p[0], p[1]))
        return out
    return None


def point_xy(geom: dict | None) -> tuple[float, float] | None:
    if geom and geom.get("type") == "Point":
        c = geom["coordinates"]
        return (c[0], c[1])
    return None
