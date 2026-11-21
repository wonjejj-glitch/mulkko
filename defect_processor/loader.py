"""nPASS Report / PipeAsset 엑셀(CSV) 조서 로더.

- 시트명이 달라도 열 이름(별칭)으로 '관로제원' 시트와 '결함내역' 시트를 자동 판별한다.
- 조서 상단에 제목·결재란이 있어 헤더가 1행이 아닌 경우도 헤더 행을 자동 탐색한다.
- 열 이름 별칭은 config["column_aliases"]에서 확장할 수 있다.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

from common.manuals import manual_view
from common.manuals import normalize_id as normalize_manual
from common.models import Defect, Pipe

TRUE_WORDS = {"y", "yes", "true", "1", "o", "예", "부족", "불량", "ng", "필요"}
REVERSE_WORDS = ("역", "rev", "하류", "up")  # '역방향', 'reverse', '하류→상류'


def _norm(s: Any) -> str:
    s = str(s).strip().lower()
    s = re.sub(r"\s+", "", s)
    return s


def _norm_nounit(s: Any) -> str:
    return re.sub(r"\(.*?\)|\[.*?\]", "", _norm(s))


def _build_alias_lookup(aliases: dict[str, list[str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canon, names in aliases.items():
        for n in [canon, *names]:
            lookup.setdefault(_norm(n), canon)
            lookup.setdefault(_norm_nounit(n), canon)
    return lookup


def _map_columns(columns: list[Any], lookup: dict[str, str]) -> dict[Any, str]:
    mapping: dict[Any, str] = {}
    used: set[str] = set()
    for c in columns:
        for key in (_norm(c), _norm_nounit(c)):
            canon = lookup.get(key)
            if canon and canon not in used:
                mapping[c] = canon
                used.add(canon)
                break
    return mapping


def _find_header(raw: pd.DataFrame, lookup: dict[str, str], must: set[str], scan_rows: int = 20) -> int | None:
    """원시 시트에서 필수 열을 가장 많이 포함하는 헤더 행 번호를 찾는다."""
    best_row, best_score = None, 0
    for i in range(min(scan_rows, len(raw))):
        mapped = set(_map_columns(list(raw.iloc[i].values), lookup).values())
        score = len(mapped)
        if must <= mapped and score > best_score:
            best_row, best_score = i, score
    return best_row


def _frame_with_header(raw: pd.DataFrame, header_row: int, lookup: dict[str, str]) -> pd.DataFrame:
    df = raw.iloc[header_row + 1:].copy()
    df.columns = list(raw.iloc[header_row].values)
    df = df.rename(columns=_map_columns(list(df.columns), lookup))
    df = df.loc[:, [c for c in df.columns if isinstance(c, str)]]
    df = df.loc[:, ~df.columns.duplicated()]
    return df.dropna(how="all").reset_index(drop=True)


def read_tables(path: str | Path, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """엑셀/CSV에서 (관로제원 DataFrame, 결함내역 DataFrame)을 정규화된 열 이름으로 반환한다."""
    path = Path(path)
    pipe_lookup = _build_alias_lookup(cfg["column_aliases"]["pipes"])
    defect_lookup = _build_alias_lookup(cfg["column_aliases"]["defects"])
    pipe_must = {"pipe_id", "diameter_mm"}
    defect_must = {"pipe_id", "distance_m"}

    if path.suffix.lower() in (".csv", ".txt"):
        raise ValueError("CSV는 read_csv_pair(관로CSV, 결함CSV)를 사용하십시오.")

    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    pipes_df = defects_df = None
    for _name, raw in sheets.items():
        if raw.empty:
            continue
        if pipes_df is None:
            h = _find_header(raw, pipe_lookup, pipe_must | {"plan_length_m"})
            if h is not None:
                pipes_df = _frame_with_header(raw, h, pipe_lookup)
                continue
        if defects_df is None:
            h = _find_header(raw, defect_lookup, defect_must)
            if h is not None:
                defects_df = _frame_with_header(raw, h, defect_lookup)
    if pipes_df is None:
        raise ValueError(f"{path.name}: 관로제원 시트(관번·관경·도면연장 열)를 찾지 못했습니다.")
    if defects_df is None:
        defects_df = pd.DataFrame(columns=["pipe_id", "distance_m", "code"])
    return pipes_df, defects_df


def read_csv_pair(pipes_csv: str | Path, defects_csv: str | Path, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    pl = _build_alias_lookup(cfg["column_aliases"]["pipes"])
    dl = _build_alias_lookup(cfg["column_aliases"]["defects"])
    p = pd.read_csv(pipes_csv, dtype=object, encoding="utf-8-sig")
    d = pd.read_csv(defects_csv, dtype=object, encoding="utf-8-sig")
    return p.rename(columns=_map_columns(list(p.columns), pl)), d.rename(columns=_map_columns(list(d.columns), dl))


# ---------------------------------------------------------------- 값 변환
def _design_flow_lps(r) -> float | None:
    """설계유량을 L/s로. 실무 표기 ㎥/일(단위 없는 '설계유량'도 ㎥/일로 봄), L/s, ㎥/s 열을 모두 받는다."""
    lps = _f(r.get("design_flow_lps"))
    if lps is not None:
        return lps
    cms = _f(r.get("design_flow_cms"))
    if cms is not None:
        return cms * 1000.0
    cmd = _f(r.get("design_flow_cmd"))
    return cmd / 86.4 if cmd is not None else None


def _pipe_use(v: Any) -> str | None:
    s = str(v or "").strip()
    for key in ("합류", "우수", "오수"):
        if key in s:
            return key
    return None


def _f(v: Any, default: float | None = None) -> float | None:
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return default if (isinstance(v, float) and math.isnan(v)) else float(v)
    s = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(s) if s not in ("", "-", ".") else default
    except ValueError:
        return default


def _s(v: Any, default: str = "") -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    s = str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s or default


def _b(v: Any) -> bool:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return False
    if isinstance(v, bool):
        return v
    return _norm(v) in TRUE_WORDS


def parse_grade(v: Any, cfg: dict) -> int | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).strip().upper()
    if s in cfg["grade_alias"]:
        return int(cfg["grade_alias"][s])
    num = _f(s)
    if num is None:
        return None
    return int(max(1, min(5, round(num))))


def resolve_code(code: str, description: str, cfg: dict) -> str:
    """결함코드를 표준 코드로 정규화한다. 코드가 없거나 비표준이면 결함내용 키워드로 판별."""
    table = cfg["defect_codes"]
    c = (code or "").strip().upper()
    if c in table:
        return c
    alias = cfg.get("code_aliases", {}).get(c)
    if alias in table:
        return alias
    text = f"{code} {description}".replace(" ", "")
    for k, spec in table.items():
        if spec["name"] in text:
            return k
    for k, spec in table.items():
        if any(kw.replace(" ", "") in text for kw in spec.get("keywords", [])):
            return k
    return c or "UNK"


# ---------------------------------------------------------------- 모델 변환
def build_pipes(pipes_df: pd.DataFrame, defects_df: pd.DataFrame, cfg: dict) -> tuple[list[Pipe], list[str]]:
    warnings: list[str] = []
    optional = {"avg_depth_m": "평균심도(기본 1.5m 적용)", "cctv_length_m": "촬영연장(연장보정 없음)",
                "from_invert_el": "관저고(종단면도 가정값·경사 산정 불가)", "from_x": "좌표(DXF 임시배치)"}
    for col, effect in optional.items():
        if col not in pipes_df.columns or pipes_df[col].isna().all():
            warnings.append(f"입력 열 없음: {effect}")
    pipes: dict[str, Pipe] = {}
    default_pav = cfg["excavation"].get("default_pavement", "아스콘")

    for _, r in pipes_df.iterrows():
        pid = _s(r.get("pipe_id"))
        if not pid:
            continue
        plan = _f(r.get("plan_length_m"))
        cctv = _f(r.get("cctv_length_m"))
        if plan is None and cctv is None:
            warnings.append(f"[{pid}] 도면연장·촬영연장이 모두 없어 제외")
            continue
        plan = plan if plan is not None else cctv
        cctv = cctv if cctv is not None else plan
        dia = _f(r.get("diameter_mm"), 300.0)
        direction = _s(r.get("survey_direction"), "순방향")
        cap_ratio = _f(r.get("capacity_ratio"))
        cap_flag = _b(r.get("capacity_insufficient")) or (cap_ratio is not None and cap_ratio > 1.0)
        req = _f(r.get("required_diameter_mm"))
        if pid in pipes:
            warnings.append(f"[{pid}] 관번 중복 - 첫 번째 행만 사용")
            continue
        pipes[pid] = Pipe(
            pipe_id=pid,
            from_mh=_s(r.get("from_mh"), "-"),
            to_mh=_s(r.get("to_mh"), "-"),
            material=_s(r.get("material"), "흄관"),
            diameter_mm=int(dia),
            plan_length_m=float(plan),
            cctv_length_m=float(cctv),
            avg_depth_m=_f(r.get("avg_depth_m"), 1.5),
            pavement=_s(r.get("pavement"), default_pav),
            survey_direction="역방향" if any(w in direction.lower() for w in REVERSE_WORDS) else "순방향",
            manual=normalize_manual(r.get("manual")),
            survey_length_m=_f(r.get("survey_length_m")),
            capacity_insufficient=cap_flag,
            capacity_ratio=cap_ratio,
            required_diameter_mm=int(req) if req else None,
            design_flow_lps=_design_flow_lps(r),
            pipe_use=_pipe_use(r.get("pipe_use")),
            roughness_n=_f(r.get("roughness_n")),
            from_x=_f(r.get("from_x")), from_y=_f(r.get("from_y")),
            to_x=_f(r.get("to_x")), to_y=_f(r.get("to_y")),
            from_ground_el=_f(r.get("from_ground_el")), from_invert_el=_f(r.get("from_invert_el")),
            to_ground_el=_f(r.get("to_ground_el")), to_invert_el=_f(r.get("to_invert_el")),
        )

    bad_manual = sorted({_s(v) for v in pipes_df.get("manual", []) if _s(v) and normalize_manual(v) is None})
    if bad_manual:
        warnings.append(f"인식할 수 없는 적용매뉴얼 값 {bad_manual} - 사업 기본 매뉴얼({cfg.get('manual')}) 적용")
    for idx, r in defects_df.iterrows():
        pid = _s(r.get("pipe_id"))
        if not pid:
            continue
        if pid not in pipes:
            warnings.append(f"결함내역 {idx + 1}번째 자료: 관번 '{pid}'가 관로제원에 없어 제외")
            continue
        dist = _f(r.get("distance_m"))
        if dist is None:
            warnings.append(f"[{pid}] 결함내역 {idx + 1}번째 자료: 거리값 없음 - 제외")
            continue
        desc = _s(r.get("description"))
        mcfg = manual_view(cfg, pipes[pid].manual)          # 관로의 적용 매뉴얼 코드표로 해석
        code = resolve_code(_s(r.get("code")), desc, mcfg)
        spec = mcfg["defect_codes"].get(code)
        if spec is None:
            warnings.append(f"[{pid}] {mcfg.get('manual')} 매뉴얼 미등록 결함코드 '{code}'({desc}) - 판정 제외")
            continue
        g_struct = parse_grade(r.get("struct_grade"), cfg)
        g_oper = parse_grade(r.get("oper_grade"), cfg)
        g_any = parse_grade(r.get("grade"), cfg)
        if spec["category"] == "structural":
            grade = g_struct or g_any or g_oper
        else:
            grade = g_oper or g_any or g_struct
        pipes[pid].defects.append(Defect(
            pipe_id=pid,
            distance_raw=float(dist),
            end_distance_raw=_f(r.get("end_distance_m")),
            code=code,
            name=spec["name"],
            grade=grade or 3,
            description=desc or spec["name"],
            position_estimated="추정" in _s(r.get("note")),
        ))
    for p in pipes.values():
        p.defects.sort(key=lambda d: d.distance_raw)
    return list(pipes.values()), warnings


def load_survey(path: str | Path, cfg: dict, defects_path: str | Path | None = None) -> tuple[list[Pipe], list[str]]:
    """조서 파일을 읽어 Pipe 리스트를 만든다. CSV인 경우 defects_path 필요."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        if defects_path is None:
            raise ValueError("CSV 입력은 관로 CSV와 결함 CSV 두 개가 필요합니다.")
        p, d = read_csv_pair(path, defects_path, cfg)
    else:
        p, d = read_tables(path, cfg)
    return build_pipes(p, d, cfg)
