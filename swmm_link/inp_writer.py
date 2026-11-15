"""정비 후 SWMM `.inp` 생성 - 정비공법을 반영(증경·신관 교체·CIPP 라이닝)하여 재해석용 모델을 만든다."""
from __future__ import annotations

from pathlib import Path

from common.models import ProjectResult
from common.rehab_hydraulics import post_rehab_properties
from swmm_link.inp_parser import FT, _read, parse_inp
from swmm_link.merge import match_conduit


def write_rehab_inp(src_inp: str | Path, res: ProjectResult, out_path: str | Path) -> tuple[Path, list[str]]:
    """원본 `.inp`의 [CONDUITS] 조도계수와 [XSECTIONS] 관경만 정비 후 값으로 바꾼다.

    반환: (출력 경로, 변경 내역 목록). 변경 대상이 아닌 줄은 그대로 보존한다.
    """
    model = parse_inp(src_inp)
    k = 1.0 if model.metric else 1.0 / FT
    changes: dict[str, tuple[float, float]] = {}
    log: list[str] = []
    for p in res.pipes:
        c = match_conduit(p, model)
        if c is None:
            continue
        d1, n1, changed = post_rehab_properties(p, res.decisions[p.pipe_id], res.config)
        if changed:
            changes[c.name] = (d1 / 1000.0, n1)
            log.append(f"{c.name}: {res.decisions[p.pipe_id].method}  D{p.diameter_mm}→D{d1:.0f}, n {c.n:.3f}→{n1:.4f}")

    out_lines = [";; 정비 후 모델 - 노후하수관로 정비 설계 자동화 도구가 생성 (원본: " + Path(src_inp).name + ")"]
    section = ""
    for raw in _read(src_inp).splitlines():
        body = raw.split(";", 1)[0]
        stripped = body.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].upper()
            out_lines.append(raw)
            continue
        toks = body.split()
        if toks and section == "CONDUITS" and toks[0] in changes and len(toks) >= 5:
            toks[4] = f"{changes[toks[0]][1]:.4f}"
            out_lines.append(" ".join(toks))
        elif toks and section == "XSECTIONS" and toks[0] in changes and len(toks) >= 3:
            toks[2] = f"{changes[toks[0]][0] * k:.4f}"
            out_lines.append(" ".join(toks))
        else:
            out_lines.append(raw)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return out, log
