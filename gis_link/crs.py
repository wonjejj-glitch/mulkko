"""좌표계 변환 유틸 (pyproj). pyproj가 없으면 변환이 필요한 경우에만 안내 메시지와 함께 실패한다."""
from __future__ import annotations

import re
from functools import lru_cache


def _pyproj():
    try:
        import pyproj
        return pyproj
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("좌표계 변환에는 pyproj가 필요합니다: pip install pyproj") from e


def normalize(crs: str | int | None) -> str | None:
    """'EPSG:5186', '5186', 'epsg::5186', 'urn:ogc:def:crs:EPSG::5186' → 'EPSG:5186'."""
    if crs is None:
        return None
    s = str(crs).strip()
    m = re.search(r"(?:EPSG)[:\s]*:*\s*(\d{4,6})", s, re.I)
    if m:
        return f"EPSG:{m.group(1)}"
    if s.isdigit():
        return f"EPSG:{s}"
    if s.upper() in ("CRS84", "OGC:CRS84") or s.endswith("CRS84"):
        return "EPSG:4326"
    return s


@lru_cache(maxsize=32)
def _transformer(src: str, dst: str):
    pp = _pyproj()
    return pp.Transformer.from_crs(src, dst, always_xy=True)


def is_geographic(crs: str) -> bool:
    return bool(_pyproj().CRS.from_user_input(crs).is_geographic)


def same(a: str | None, b: str | None) -> bool:
    return normalize(a) == normalize(b)


def transform_points(pts: list[tuple[float, float]], src: str, dst: str) -> list[tuple[float, float]]:
    if same(src, dst):
        return [(float(x), float(y)) for x, y in pts]
    t = _transformer(normalize(src), normalize(dst))
    return [tuple(t.transform(x, y)) for x, y in pts]


def crs_from_prj(wkt: str) -> str | None:
    """.prj WKT에서 EPSG 코드를 추정한다(Korea 2000 계열 이름 포함). 실패하면 None."""
    if not wkt.strip():
        return None
    try:
        crs = _pyproj().CRS.from_wkt(wkt)
    except Exception:
        return None
    epsg = crs.to_epsg(min_confidence=70)
    return f"EPSG:{epsg}" if epsg else None


def prj_text(crs: str) -> str:
    """Shapefile .prj 내용(ESRI WKT)."""
    return _pyproj().CRS.from_user_input(crs).to_wkt(version="WKT1_ESRI")
