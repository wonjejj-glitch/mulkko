"""VE 민감도 분석: 판정기준 변화에 따른 공법 구성·총공사비 변화."""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from common.config import with_criteria
from common.models import METHODS, Pipe
from common.pipeline import run_pipes


def _run(pipes: list[Pipe], cfg: dict):
    return run_pipes(copy.deepcopy(pipes), cfg)


def sweep(pipes: list[Pipe], cfg: dict, param: str, values) -> pd.DataFrame:
    """criteria[param]를 values로 바꿔가며 총공사비·공법별 연장을 계산."""
    rows = []
    for v in values:
        res = _run(pipes, with_criteria(cfg, **{param: float(v)}))
        row = {param: float(v), "총공사비(억원)": round(res.total_cost / 1e8, 3)}
        for m in METHODS:
            row[f"{m}(m)"] = round(sum(p.plan_length_m for p in res.pipes if res.decisions[p.pipe_id].method == m), 1)
        rows.append(row)
    return pd.DataFrame(rows)


def default_sweeps(pipes: list[Pipe], cfg: dict) -> dict[str, pd.DataFrame]:
    return {
        "breakeven_ratio": sweep(pipes, cfg, "breakeven_ratio", np.round(np.arange(0.40, 1.01, 0.05), 2)),
        "defect_rate_threshold_pct": sweep(pipes, cfg, "defect_rate_threshold_pct", np.arange(10, 55, 5)),
        "merge_gap_m": sweep(pipes, cfg, "merge_gap_m", np.round(np.arange(0.0, 3.01, 0.3), 1)),
    }
