"""EPA SWMM 5.x `.inp` 모델과 `.rpt` 결과 파서 (원형관 중심, 단위는 SI로 환산).

지원: [OPTIONS](FLOW_UNITS, LINK_OFFSETS), [JUNCTIONS], [OUTFALLS], [STORAGE], [DIVIDERS],
      [CONDUITS], [XSECTIONS](CIRCULAR), [COORDINATES]
.rpt: Link Flow Summary(최대유량·유속·Max/Full Flow·Max/Full Depth), Conduit Surcharge Summary(만관 시간)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

FT = 0.3048
US_UNITS = {"CFS", "GPM", "MGD"}
FLOW_TO_LPS = {"CMS": 1000.0, "LPS": 1.0, "MLD": 1e6 / 86400, "CFS": 28.3168466, "GPM": 0.0630902, "MGD": 43.8126364}


@dataclass
class SwmmNode:
    name: str
    kind: str                      # JUNCTION | OUTFALL | STORAGE | DIVIDER
    invert: float                  # m
    max_depth: float = 0.0         # m
    x: float | None = None
    y: float | None = None

    @property
    def ground(self) -> float | None:
        return self.invert + self.max_depth if self.max_depth > 0 else None


@dataclass
class SwmmConduit:
    name: str
    from_node: str
    to_node: str
    length: float                  # m
    n: float
    in_offset: float = 0.0         # m
    out_offset: float = 0.0        # m
    shape: str = "CIRCULAR"
    diameter: float = 0.0          # m (Geom1)
    barrels: int = 1


@dataclass
class SwmmModel:
    path: str = ""
    flow_units: str = "CMS"
    metric: bool = True
    link_offsets: str = "DEPTH"
    routing: str = ""                               # FLOW_ROUTING (STEADY/KINWAVE/DYNWAVE)
    dwf_lps: dict[str, float] = field(default_factory=dict)      # [DWF] 절점별 평균 오수량 (L/s)
    nodes: dict[str, SwmmNode] = field(default_factory=dict)
    conduits: dict[str, SwmmConduit] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def inverts(self, c: SwmmConduit) -> tuple[float, float]:
        """관로 시·종점 관저고(EL.m)."""
        a, b = self.nodes.get(c.from_node), self.nodes.get(c.to_node)
        if self.link_offsets == "ELEVATION":
            return c.in_offset, c.out_offset
        return (a.invert if a else 0.0) + c.in_offset, (b.invert if b else 0.0) + c.out_offset


@dataclass
class LinkResult:
    name: str
    max_flow_lps: float
    max_velocity: float
    flow_ratio: float              # Max/Full Flow
    depth_ratio: float             # Max/Full Depth
    surcharge_hours: float | None = None   # Hours Above Full Normal Flow


def _read(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _num(tok: str, default: float = 0.0) -> float:
    if tok in ("*", ""):
        return default
    try:
        return float(tok)
    except ValueError:
        return default


def parse_inp(path: str | Path) -> SwmmModel:
    model = SwmmModel(path=str(path))
    sections: dict[str, list[list[str]]] = {}
    cur = None
    for line in _read(path).splitlines():
        s = line.split(";", 1)[0].strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1].upper()
            sections.setdefault(cur, [])
            continue
        if cur:
            sections[cur].append(s.split())

    for tok in sections.get("OPTIONS", []):
        if len(tok) >= 2 and tok[0].upper() == "FLOW_UNITS":
            model.flow_units = tok[1].upper()
        elif len(tok) >= 2 and tok[0].upper() == "LINK_OFFSETS":
            model.link_offsets = tok[1].upper()
        elif len(tok) >= 2 and tok[0].upper() == "FLOW_ROUTING":
            model.routing = tok[1].upper()
    model.metric = model.flow_units not in US_UNITS
    k = 1.0 if model.metric else FT

    for kind, sec in (("JUNCTION", "JUNCTIONS"), ("OUTFALL", "OUTFALLS"), ("STORAGE", "STORAGE"), ("DIVIDER", "DIVIDERS")):
        for t in sections.get(sec, []):
            if len(t) < 2:
                continue
            md = _num(t[2]) * k if kind in ("JUNCTION", "STORAGE") and len(t) > 2 else 0.0
            model.nodes[t[0]] = SwmmNode(t[0], kind, _num(t[1]) * k, md)
    zero_xy = 0
    for t in sections.get("COORDINATES", []):
        if len(t) >= 3 and t[0] in model.nodes:
            x, y = _num(t[1]), _num(t[2])
            if x == 0.0 and y == 0.0:          # 좌표 미입력(PipeTools 등이 0,0으로 채움) → 좌표 없음으로 취급
                zero_xy += 1
                continue
            model.nodes[t[0]].x, model.nodes[t[0]].y = x, y
    if zero_xy:
        model.warnings.append(f"절점 {zero_xy}개의 좌표가 (0,0)이라 좌표 없음으로 처리(GIS 연동 필요)")
    flow_k = FLOW_TO_LPS.get(model.flow_units, 1000.0)
    for t in sections.get("DWF", []):
        if len(t) >= 3 and t[1].upper() == "FLOW" and t[0] in model.nodes:
            model.dwf_lps[t[0]] = model.dwf_lps.get(t[0], 0.0) + _num(t[2]) * flow_k

    for t in sections.get("CONDUITS", []):
        if len(t) < 5:
            continue
        off = lambda i: _num(t[i]) * k if len(t) > i else 0.0   # noqa: E731
        model.conduits[t[0]] = SwmmConduit(t[0], t[1], t[2], _num(t[3]) * k, _num(t[4]), off(5), off(6))
    for t in sections.get("XSECTIONS", []):
        if len(t) < 3 or t[0] not in model.conduits:
            continue
        c = model.conduits[t[0]]
        c.shape = t[1].upper()
        c.diameter = _num(t[2]) * k
        c.barrels = int(_num(t[6], 1)) if len(t) > 6 else 1
        if c.shape != "CIRCULAR":
            model.warnings.append(f"[{t[0]}] 비원형 단면({c.shape}) - Geom1을 관경으로 간주하므로 통수능 검토 정확도 낮음")
    for name, c in model.conduits.items():
        if c.diameter <= 0:
            model.warnings.append(f"[{name}] XSECTIONS 단면 정보 없음")
        for nd in (c.from_node, c.to_node):
            if nd not in model.nodes:
                model.warnings.append(f"[{name}] 절점 '{nd}' 정의 없음")
    model.warnings.extend(check_model(model))
    return model


def check_model(model: SwmmModel) -> list[str]:
    """SWMM 5.x 엔진이 거부하거나 무시하는 입력을 사전 점검한다(엔진 실행 없이)."""
    msgs: list[str] = []
    rigid = model.routing in ("STEADY", "KINWAVE")
    for name, c in model.conduits.items():
        if model.link_offsets != "ELEVATION" and (c.in_offset < 0 or c.out_offset < 0):
            msgs.append(f"[{name}] 음(-)오프셋은 SWMM 5.x에서 무시됨(WARNING 03) - 경사가 달라져 모의 결과가 바뀝니다")
        if c.from_node in model.nodes and c.to_node in model.nodes and c.length > 0:
            inv_a, inv_b = model.inverts(c)
            if c.in_offset < 0:
                inv_a = model.nodes[c.from_node].invert
            if c.out_offset < 0:
                inv_b = model.nodes[c.to_node].invert
            if inv_a < inv_b:
                sev = "SWMM 5.x ERROR 115로 실행 불가" if rigid else "역경사"
                msgs.append(f"[{name}] 역경사 {(inv_b - inv_a) / c.length * 1000:.1f}‰ ({model.routing or '라우팅 미지정'}: {sev}) - 압송관이면 중력관 모델에서 분리 필요")
    return msgs


def steady_flows_lps(model: SwmmModel) -> dict[str, float]:
    """[DWF] 평균 오수량을 하류로 누적한 관로별 유량(L/s). 정상류(STEADY) 모델의 정확한 설계유량이며,
    .rpt의 유량은 소수 3자리(CMS 기준)로 반올림되어 소유량 관로에서 오차가 큼. 가지(Tree) 망 가정."""
    inflow = {n: model.dwf_lps.get(n, 0.0) for n in model.nodes}
    out_links: dict[str, list[SwmmConduit]] = {}
    indeg = {n: 0 for n in model.nodes}
    for c in model.conduits.values():
        if c.from_node in model.nodes and c.to_node in model.nodes:
            out_links.setdefault(c.from_node, []).append(c)
            indeg[c.to_node] += 1
    flows: dict[str, float] = {}
    queue = [n for n, d in indeg.items() if d == 0]
    while queue:
        n = queue.pop()
        for c in out_links.get(n, []):
            flows[c.name] = inflow[n]
            inflow[c.to_node] += inflow[n]
            indeg[c.to_node] -= 1
            if indeg[c.to_node] == 0:
                queue.append(c.to_node)
    return flows


_ROW = re.compile(r"^\s*(\S+)\s+(CONDUIT|PUMP|ORIFICE|WEIR|OUTLET)\s+([-\d.eE+]+)\s+(\d+)\s+(\d+:\d+)\s+"
                  r"([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)")
_SUR = re.compile(r"^\s*(\S+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$")


def parse_rpt(path: str | Path) -> dict[str, LinkResult]:
    """SWMM 결과 보고서에서 관로별 최대유량·통수능비·만관시간을 읽는다."""
    lines = _read(path).splitlines()
    results: dict[str, LinkResult] = {}
    unit = "CMS"
    in_flow = in_sur = False
    got = False
    sur: dict[str, float] = {}
    for line in lines:
        low = line.strip().lower()
        if low == "link flow summary":
            in_flow, in_sur, got = True, False, False
            continue
        if low == "conduit surcharge summary":
            in_flow, in_sur, got = False, True, False
            continue
        if in_flow:
            m = re.match(r"^\s*Link\s+Type\s+(\w+)", line)
            if m:
                unit = m.group(1).upper()
                continue
            m = _ROW.match(line)
            if m:
                got = True
                q = float(m.group(3)) * FLOW_TO_LPS.get(unit, 1000.0)
                v = float(m.group(6)) * (1.0 if unit not in ("CFS", "GPM", "MGD") else FT)
                results[m.group(1)] = LinkResult(m.group(1), q, v, float(m.group(7)), float(m.group(8)))
            elif got and line.strip() and not set(line.strip()) <= {"-", "*"}:
                in_flow = False
        elif in_sur:
            m = _SUR.match(line)
            if m:
                got = True
                sur[m.group(1)] = float(m.group(5))
            elif got and line.strip() and not set(line.strip()) <= {"-", "*"}:
                in_sur = False
    for name, h in sur.items():
        if name in results:
            results[name].surcharge_hours = h
    return results
