"""조사·상태평가 매뉴얼(2015 / 2017 / 2025) 선택과 적용.

매뉴얼마다 결함코드 의미가 다르므로(예: DE = 2015 토사퇴적 / 2017·2025 내피생성, PB = 2015 관파손 / 2025 막힘)
코드표·점수·상태등급 방식·정비판단 규칙을 섞지 않고 통째로 교체한다.
관로마다 다른 매뉴얼을 쓰는 혼용 사업(예: 노후하수 실제 사업 2015+2017)은 조서 '적용매뉴얼' 열로 지정한다.
"""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path

MANUAL_DIR = Path(__file__).resolve().parent.parent / "config" / "manuals"
REPLACED = ("defect_codes", "code_aliases", "condition_grading", "rules", "infiltration_bonus", "flexible_variants",
            "grade_labels")


def available() -> list[str]:
    return sorted(p.stem for p in MANUAL_DIR.glob("*.json"))


def normalize_id(value) -> str | None:
    """'2017', '2017년 매뉴얼', '표준매뉴얼(2025)', 2015.0 → '2017'/'2025'/'2015'. 인식 불가면 None."""
    if value is None:
        return None
    m = re.search(r"(20\d{2})", str(value))
    if m and m.group(1) in available():
        return m.group(1)
    return None


@lru_cache(maxsize=8)
def load_manual(manual_id: str) -> dict:
    path = MANUAL_DIR / f"{manual_id}.json"
    if not path.exists():
        raise ValueError(f"매뉴얼 '{manual_id}' 없음 (사용 가능: {', '.join(available())})")
    return json.loads(path.read_text(encoding="utf-8"))


def apply_manual(cfg: dict, manual_id: str | None = None) -> dict:
    """cfg에 매뉴얼 기준을 적용한 사본. 매뉴얼 소유 항목은 덮어쓰고 criteria는 해당 키만 갱신."""
    mid = normalize_id(manual_id) or normalize_id(cfg.get("manual")) or "2017"
    man = load_manual(mid)
    out = copy.copy(cfg)          # 얕은 복사: 큰 단가표 등은 공유
    for k in REPLACED:
        out[k] = man.get(k)
    out["criteria"] = {**cfg["criteria"], **man.get("criteria", {})}
    out["manual"] = mid
    out["manual_title"] = man["title"]
    return out


_VIEW_CACHE: dict[tuple[int, str], dict] = {}


def manual_view(cfg: dict, manual_id: str | None) -> dict:
    """관로별 매뉴얼 적용 설정(같은 cfg·매뉴얼 조합은 재사용)."""
    mid = normalize_id(manual_id) or cfg.get("manual") or "2017"
    if mid == cfg.get("manual") and "defect_codes" in cfg:
        return cfg
    key = (id(cfg), mid)
    if key not in _VIEW_CACHE:
        if len(_VIEW_CACHE) > 64:
            _VIEW_CACHE.clear()
        _VIEW_CACHE[key] = apply_manual(cfg, mid)
    return _VIEW_CACHE[key]


def is_flexible(cfg: dict, material: str) -> bool:
    m = (material or "").upper().replace(" ", "")
    return any(k.upper() in m for k in cfg.get("flexible_materials", []))


def grade_label(cfg: dict, level: str) -> str:
    return (cfg.get("grade_labels") or {}).get(level, level)
