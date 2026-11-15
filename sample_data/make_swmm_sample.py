"""샘플 조서(make_sample.py)와 짝을 이루는 SWMM `.inp`/`.rpt` 생성.

- 조서와 같은 관번(P-001…P-012)·맨홀명(MH-01…)을 사용하므로 `--swmm` 결합을 바로 시험할 수 있다.
- P-010/P-011은 조서에 관저고·지반고가 없고 SWMM에만 있다(결합 시 채워지는지 확인용).
- .rpt는 SWMM 5.2 보고서의 Link Flow Summary / Conduit Surcharge Summary 형식을 따른다.
"""
from __future__ import annotations

import math
from pathlib import Path

from common.hydraulics import full_capacity_lps
from sample_data.make_sample import DESIGN_FLOW, PIPES, _coords

N_BY_MATERIAL = {"흄관": 0.013, "VR관": 0.011, "PE관": 0.010}
SPECIAL = {"P-010": (30.95, 30.85, 32.50, 32.30), "P-011": (31.10, 30.95, 32.80, 32.50)}   # il_f, il_t, gf, gt
DEFAULT_FLOW = 12.0


def _pipe_elevations() -> dict[str, tuple[float, float, float, float]]:
    out = {}
    for row in PIPES:
        pid, _f, _t, _mat, dia, L, _c, dep = row[:8]
        (gf, gt) = row[14]
        if gf is None:
            out[pid] = SPECIAL[pid]
        else:
            slope = 0.004 if dia <= 300 else 0.003
            il_f = round(gf - dep - 0.10, 3)
            out[pid] = (il_f, round(il_f - L * slope, 3), gf, gt)
    return out


def make_swmm_sample(out_dir: str | Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    elev = _pipe_elevations()
    coords = _coords()

    inv: dict[str, float] = {}
    gnd: dict[str, float] = {}
    xy: dict[str, tuple[float, float]] = {}
    for row in PIPES:
        pid, f, t = row[:3]
        il_f, il_t, gf, gt = elev[pid]
        inv[f] = min(inv.get(f, 1e9), il_f)
        inv[t] = min(inv.get(t, 1e9), il_t)
        gnd[f] = max(gnd.get(f, -1e9), gf)
        gnd[t] = max(gnd.get(t, -1e9), gt)
        if pid in coords:
            (x0, y0), (x1, y1) = coords[pid]
            xy[f], xy[t] = (x0, y0), (x1, y1)
    for row in PIPES:                       # 조서에 좌표가 없는 관로(P-011): 하류 맨홀 기준 남쪽으로 연장만큼 배치
        pid, f, t, L = row[0], row[1], row[2], row[5]
        if pid not in coords and f not in xy and t in xy:
            xy[f] = (xy[t][0], round(xy[t][1] - L, 3))

    outfall = "MH-10"
    lines = ["[TITLE]", ";;Project Title/Notes", "노후하수관로 정비 샘플 모델 (자동 생성)", "",
             "[OPTIONS]", "FLOW_UNITS           LPS", "INFILTRATION         HORTON", "FLOW_ROUTING         DYNWAVE",
             "LINK_OFFSETS         DEPTH", "START_DATE           01/01/2026", "END_DATE             01/01/2026", "",
             "[JUNCTIONS]", ";;Name  Elevation  MaxDepth  InitDepth  SurDepth  Aponded"]
    for n in sorted(inv):
        if n != outfall:
            lines.append(f"{n:<8}{inv[n]:>9.3f}{gnd[n] - inv[n]:>9.3f}      0      0      0")
    lines += ["", "[OUTFALLS]", ";;Name  Elevation  Type  Stage Data  Gated", f"{outfall:<8}{inv[outfall]:>9.3f}  FREE  NO", "",
              "[CONDUITS]", ";;Name  From  To  Length  Roughness  InOffset  OutOffset  InitFlow  MaxFlow"]
    for row in PIPES:
        pid, f, t, mat, _dia, L = row[:6]
        il_f, il_t, _gf, _gt = elev[pid]
        lines.append(f"{pid:<7}{f:<7}{t:<7}{L:>7.2f}  {N_BY_MATERIAL.get(mat, 0.013):.3f}  {il_f - inv[f]:.3f}  {il_t - inv[t]:.3f}  0  0")
    lines += ["", "[XSECTIONS]", ";;Link  Shape  Geom1  Geom2  Geom3  Geom4  Barrels"]
    for row in PIPES:
        lines.append(f"{row[0]:<7}CIRCULAR  {row[4] / 1000:.3f}  0  0  0  1")
    lines += ["", "[COORDINATES]", ";;Node  X-Coord  Y-Coord"]
    for n in sorted(xy):
        lines.append(f"{n:<8}{xy[n][0]:.3f}  {xy[n][1]:.3f}")
    inp = out_dir / "sample_swmm.inp"
    inp.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rpt = ["  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.0)", "  " + "-" * 62, "",
           "  *****************", "  Link Flow Summary", "  *****************", "",
           "  " + "-" * 77,
           "                                 Maximum  Time of Max    Maximum    Max/    Max/",
           "                                  |Flow|   Occurrence   |Veloc|    Full    Full",
           "  Link                 Type          LPS  days hr:min     m/sec    Flow   Depth",
           "  " + "-" * 77]
    over = []
    for row in PIPES:
        pid, _f, _t, mat, dia, L = row[:6]
        il_f, il_t, *_ = elev[pid]
        slope = (il_f - il_t) / L
        q = DESIGN_FLOW.get(pid, DEFAULT_FLOW)
        qfull = full_capacity_lps(dia, slope, N_BY_MATERIAL.get(mat, 0.013))
        ratio = q / qfull
        depth = 1.0 if ratio >= 1 else min(0.95, 0.30 + 0.6 * ratio)
        vel = q / 1000 / (math.pi * (dia / 1000) ** 2 / 4)
        rpt.append(f"  {pid:<20} CONDUIT {q:>10.2f}    0  12:00 {vel:>9.2f} {ratio:>7.2f} {depth:>7.2f}")
        if ratio >= 1:
            over.append(pid)
    rpt += ["", "  *************************", "  Conduit Surcharge Summary", "  *************************", "",
            "  " + "-" * 77,
            "                                                           Hours        Hours",
            "                          --------- Hours Full --------   Above Full   Capacity",
            "  Conduit               Both Ends  Upstream  Dnstream    Normal Flow   Limited",
            "  " + "-" * 77]
    for pid in over:
        rpt.append(f"  {pid:<20} {2.5:>9.2f} {2.5:>9.2f} {2.5:>9.2f} {3.1:>12.2f} {1.2:>9.2f}")
    rpt += ["", "  Analysis begun on:  Fri Nov 13 2026", ""]
    rpt_path = out_dir / "sample_swmm.rpt"
    rpt_path.write_text("\n".join(rpt) + "\n", encoding="utf-8")
    return inp, rpt_path


if __name__ == "__main__":
    for p in make_swmm_sample(Path(__file__).parent):
        print("생성:", p)
