"""판정기준·단가 설정(JSON) 로더 및 관경별 단가 조회 유틸리티."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "default_criteria.json"
# 개략 검토용 예시 단가·요율(실적 단가 도입 이전 기본값). 판정 로직 회귀 테스트가 단가 변화와 무관하도록 고정 사용.
EXAMPLE_PRICES_PATH = ROOT_DIR / "config" / "prices" / "예시단가.json"


def load_config(path: str | Path | None = None, manual: str | None = None) -> dict[str, Any]:
    """설정 파일을 읽고 조사·상태평가 매뉴얼(2015/2017/2025)을 적용한다.

    path 미지정 시 기본 판정기준. manual 미지정 시 설정의 "manual"(기본 2017).
    """
    from common.manuals import apply_manual

    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    if path and Path(path).resolve() != DEFAULT_CONFIG_PATH:
        # 사용자 설정은 기본값 위에 덮어쓴다(누락 키는 기본값 유지)
        with open(path, encoding="utf-8") as f:
            cfg = deep_merge(cfg, json.load(f))
    return apply_manual(cfg, manual)


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def with_criteria(cfg: dict, **overrides: Any) -> dict:
    """criteria 항목만 바꾼 설정 사본을 반환(민감도 분석용)."""
    new = copy.deepcopy(cfg)
    for k, v in overrides.items():
        if v is not None:
            new["criteria"][k] = v
    return new


def price_by_diameter(table: dict[str, float] | float, diameter_mm: float) -> float:
    """관경별 단가표에서 해당 관경 이상인 가장 작은 규격의 단가를 반환한다.

    표 최대 규격을 초과하면 최대 규격 단가를 관경 비례로 외삽한다.
    """
    if isinstance(table, (int, float)):
        return float(table)
    sizes = sorted((int(k), float(v)) for k, v in table.items())
    for size, price in sizes:
        if diameter_mm <= size:
            return price
    max_size, max_price = sizes[-1]
    return max_price * (diameter_mm / max_size)


def upsized_diameter(cfg: dict, diameter_mm: int, steps: int | None = None) -> int:
    """표준관경 목록에서 steps 단계 큰 관경을 반환한다."""
    steps = cfg["criteria"].get("upsize_steps", 1) if steps is None else steps
    std = sorted(cfg["standard_diameters_mm"])
    larger = [d for d in std if d > diameter_mm]
    if not larger:
        return diameter_mm + 100 * steps
    return larger[min(steps, len(larger)) - 1]


def pipe_unit_length(cfg: dict, material: str) -> float:
    table = cfg["pipe_unit_length_m"]
    m = (material or "").replace(" ", "")
    for key, val in table.items():
        if key != "default" and key in m:
            return float(val)
    return float(table.get("default", 2.5))
