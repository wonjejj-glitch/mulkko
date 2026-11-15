"""전체 처리 파이프라인: 로드 → 연장보정 → 결함병합/공법판정 → 수량산출."""
from __future__ import annotations

from pathlib import Path

from common.models import Pipe, ProjectResult
from defect_processor.calibration import calibrate_pipe
from common.hydraulics import apply_hydraulics
from defect_processor.loader import load_survey
from quantity_takeoff.takeoff import takeoff_pipe
from rehab_decision_ve.engine import decide_pipe
from common.manuals import manual_view
from rehab_decision_ve.scoring import assign_priority


def run_pipes(pipes: list[Pipe], cfg: dict, load_warnings: list[str] | None = None) -> ProjectResult:
    """관로마다 적용 매뉴얼(관로 지정 > 사업 기본값)로 결함 분류·상태평가·공법판정을 수행한다."""
    decisions = {}
    quantities = []
    for p in pipes:
        pcfg = manual_view(cfg, p.manual)
        apply_hydraulics(p, cfg)
        calibrate_pipe(p, pcfg)
        dec = decide_pipe(p, pcfg)
        items = takeoff_pipe(cfg, p, dec)
        dec.total_cost = sum(i.amount for i in items)
        decisions[p.pipe_id] = dec
        quantities.extend(items)
    assign_priority(pipes, decisions)
    warnings = list(load_warnings or [])
    for p in pipes:
        warnings.extend(f"[{p.pipe_id}] {w}" for w in p.warnings)
    return ProjectResult(pipes=pipes, decisions=decisions, quantities=quantities, warnings=warnings, config=cfg)


def run_file(path: str | Path, cfg: dict, defects_path: str | Path | None = None,
             swmm_inp: str | Path | None = None, swmm_rpt: str | Path | None = None,
             gis_lines: str | Path | None = None, gis_points: str | Path | None = None,
             gis_crs: str | None = None, gis_work_crs: str | None = None,
             gis_use_length: bool | None = None) -> ProjectResult:
    pipes, warns = load_survey(path, cfg, defects_path)
    if swmm_inp:
        from swmm_link import apply_swmm, parse_inp, parse_rpt
        model = parse_inp(swmm_inp)
        results = parse_rpt(swmm_rpt) if swmm_rpt else None
        warns = warns + apply_swmm(pipes, model, results, cfg)
    crs = None
    if gis_lines or gis_points:
        from gis_link import apply_gis
        gw, crs = apply_gis(pipes, cfg, gis_lines, gis_points, gis_crs, gis_work_crs, gis_use_length)
        warns = warns + gw
    res = run_pipes(pipes, cfg, warns)
    res.crs = crs or (cfg["gis"]["default_crs"] if any(p.has_coords for p in pipes) else None)
    return res
