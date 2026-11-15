"""실제 설계내역서(STmate 엑셀 내보내기)에서 단가·원가계산 요율을 뽑아 프로그램 단가표로 환산한다.

    python main.py import-prices 도급.xlsx --gwangeup 관급.xlsx --out config/prices/실제 사업_2026.json [--set-default]

- 도급 내역서 '설계내역서' 시트: 공종(1.1 토공 …) 아래 항목별 단가(노무비/재료비/경비)를 읽는다.
- 관급 내역서: 관·아스콘·골재 등 관급자재 단가(VAT 포함)를 읽는다.
- '총괄설계내역서' 시트: 간접노무비·4대보험·일반관리비·이윤 등 요율(“x 16.6%”)을 읽는다.
- 프로그램 단가 항목(관경별 전체보수 m당, 부분보수 개소당, 터파기 m3 …)으로 환산하고, 항목마다 환산 근거를 남긴다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

STD_DIAMETERS = [200, 250, 300, 350, 400, 450, 500, 600, 700, 800, 900, 1000, 1100, 1200]


@dataclass
class Item:
    path: tuple[str, ...]          # 공종 경로 (예: ("1. 굴착공사", "1.1 토공"))
    name: str
    spec: str
    qty: float
    unit: str
    price: float                   # 합계 단가
    lab: float
    mat: float
    exp: float

    @property
    def amount(self) -> float:
        return self.qty * self.price

    def diameter(self) -> int | None:
        m = re.search(r"[φΦø]\s*(\d{3,4})", f"{self.spec} {self.name}")
        return int(m.group(1)) if m else None


@dataclass
class Section:
    path: tuple[str, ...]
    amount: float
    lab: float
    mat: float
    exp: float


@dataclass
class Book:
    items: list[Item] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    rates: dict[str, float] = field(default_factory=dict)
    title: str = ""

    def find(self, name: str, spec: str = "", section: str = "") -> list[Item]:
        n = name.replace(" ", "")
        return [i for i in self.items if n in i.name.replace(" ", "") and spec in (i.spec or "")
                and (not section or any(section in p for p in i.path))]

    def section(self, key: str) -> Section | None:
        k = key.replace(" ", "")
        return next((s for s in self.sections if k in s.path[-1].replace(" ", "")), None)


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _level(code: str) -> int:
    c = code.strip()
    if c in ("□",) or not c:
        return 0
    if re.fullmatch(r"\d+\.", c) or re.fullmatch(r"[ⅠⅡⅢⅣⅤ]+\.", c):
        return 1
    if re.fullmatch(r"\d+\.\d+", c):
        return 2
    if re.fullmatch(r"\(\d+\)", c):
        return 3
    return 4


def read_stmate(path: str | Path) -> Book:
    """STmate 엑셀 내보내기(설계내역서·총괄설계내역서 시트)를 읽는다."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if "설계내역서" not in wb.sheetnames:
        raise ValueError(f"{Path(path).name}: '설계내역서' 시트가 없습니다(STmate 내보내기 형식이 아님).")
    book = Book()
    stack: list[tuple[int, str]] = []
    for r in wb["설계내역서"].iter_rows(min_row=1, values_only=True):
        r = list(r[:13]) + [None] * max(0, 13 - len(r))
        code, name, spec, qty, unit = r[0], r[1], r[2], r[3], r[4]
        head = next((c for c in r if isinstance(c, str) and c.strip().startswith("공사명")), None)
        if head:
            book.title = head.split(":", 1)[-1].strip()
            continue
        if name in (None, "") or str(name).strip() in ("명      칭", "단   가"):
            continue
        name = re.sub(r"\s*\*$", "", str(name)).strip()
        if unit in (None, "", 0) and code not in (None, ""):
            lv = _level(str(code))
            label = f"{str(code).strip()} {re.sub(r'\s+', '', name)}".strip()
            stack = [s for s in stack if s[0] < lv] + [(lv, label)]
            book.sections.append(Section(tuple(s[1] for s in stack), _num(r[6]), _num(r[8]), _num(r[10]), _num(r[12])))
            continue
        if unit in (None, "", 0):
            continue
        book.items.append(Item(tuple(s[1] for s in stack), name, str(spec or "").strip(), _num(qty), str(unit).strip(),
                               _num(r[5]), _num(r[7]), _num(r[9]), _num(r[11])))
    if "총괄설계내역서" in wb.sheetnames:
        book.rates = _read_rates(wb["총괄설계내역서"])
    return book


RATE_KEYS = [  # (총괄 비목 이름 일부, 프로그램 요율 키)
    ("간접노무비", "indirect_labor"), ("산재보험", "industrial_accident"), ("고용보험", "employment"),
    ("건강보험", "health"), ("노인장기요양", "care"), ("연금보험", "pension"), ("퇴직공제", "retirement"),
    ("산업안전보건관리비", "safety"), ("기타경비", "other_expense"), ("하도급대금", "subcontract_guarantee"),
    ("환경보전비", "environment"), ("건설기계대여금", "equipment_guarantee"), ("석면피해", "asbestos"),
    ("임금채권", "wage_claim"), ("일반관리비", "general_admin"), ("이윤", "profit"), ("부가가치세", "vat"),
]


def _read_rates(ws) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in ws.iter_rows(values_only=True):
        cells = [str(c) for c in row if c not in (None, "")]
        if not cells:
            continue
        text = " ".join(cells)
        name = re.sub(r"^\s*\d+\.\s*", "", cells[0]).replace(" ", "")   # 비목 이름(첫 칸)으로만 판별
        key = next((k for kw, k in RATE_KEYS if name.startswith(kw)), None)
        if not key or key in out:
            continue
        m = re.findall(r"[x×]\s*([\d.]+)\s*%", text)
        if not m:
            continue
        rate = float(m[0]) / 100
        if key == "safety" and "1.2배" in text:      # 관급자재 제외 적용: 요율 × 1.2
            rate *= 1.2
        out[key] = round(rate, 6)
    return out


# ---------------------------------------------------------------- 관경별 보간
def by_diameter(points: dict[int, float], floor_ratio: float = 0.6) -> dict[str, int]:
    """관경별 실적 단가를 표준 관경표로 보간·외삽(범위 밖은 양 끝 두 점의 기울기, 하한 = 최소 실적의 floor_ratio)."""
    ds = sorted(points)
    if not ds:
        return {}
    out = {}
    for d in STD_DIAMETERS:
        if d in points:
            v = points[d]
        elif len(ds) == 1:
            v = points[ds[0]]
        else:
            if d < ds[0]:
                a, b = ds[0], ds[1]
            elif d > ds[-1]:
                a, b = ds[-2], ds[-1]
            else:
                a = max(x for x in ds if x < d)
                b = min(x for x in ds if x > d)
            v = points[a] + (points[b] - points[a]) * (d - a) / (b - a)
            v = max(v, points[ds[0]] * floor_ratio)
        out[str(d)] = int(round(v, -1))
    return out


def _dia_map(items: list[Item], per: float = 1.0, add: dict[int, float] | None = None) -> dict[int, float]:
    pts: dict[int, float] = {}
    for it in items:
        d = it.diameter()
        if d and d not in pts:
            pts[d] = it.price / per + (add or {}).get(d, 0.0)
    return pts


def _w_avg(items: list[Item]) -> float:
    q = sum(i.qty for i in items)
    return sum(i.qty * i.price for i in items) / q if q else 0.0


# ---------------------------------------------------------------- 환산
def derive(dogeup: Book, gwangeup: Book | None = None) -> dict:
    """설계내역서 → 프로그램 단가표(unit_prices)·원가계산(cost_statement) 덮어쓰기 JSON."""
    src: dict[str, str] = {}
    up: dict = {}
    g = gwangeup or Book()

    def one(name, spec="", section=""):
        r = dogeup.find(name, spec, section)
        return r[0] if r else None

    # 1) 비굴착 전체보수(m) · 부분보수(개소)
    full = _dia_map(dogeup.find("비굴착전체보수"))
    part = _dia_map(dogeup.find("비굴착부분보수"))
    if full:
        up["cipp_full_per_m"] = by_diameter(full)
        src["cipp_full_per_m"] = f"'비굴착전체보수' m당 단가 φ{min(full)}~{max(full)} 실적, 그 외 관경은 보간·외삽"
    if part:
        up["partial_repair_per_site"] = by_diameter(part)
        src["partial_repair_per_site"] = f"'비굴착부분보수' 개소당 단가 φ{min(part)}~{max(part)} 실적(1개소 = 기준 규격)"

    # 2) 물돌리기(구간) = 지수플러그 1개소 + 물푸기 8시간
    plugs = _dia_map(dogeup.find("지수플러그", section="비굴착")) or _dia_map(dogeup.find("지수플러그"))
    pump = one("물푸기", section="비굴착")
    pump_8h = pump.price * 8 if pump and pump.unit.lower().startswith("hr") else 0.0
    if plugs:
        up["bypass_per_span"] = by_diameter({d: v + pump_8h for d, v in plugs.items()})
        src["bypass_per_span"] = (f"'지수플러그' 개소당 단가 + 물푸기 {pump.price:,.0f}원/hr × 8hr" if pump_8h
                                  else "'지수플러그' 개소당 단가")

    # 3) 준설·세정: 단가(원/㎥) × 관 단면적 × 준설률(설계 준설량 ÷ 전체보수 구간 관단면 체적)
    dred = dogeup.find("준설및세정", "B-TYPE") or dogeup.find("준설및세정")
    if dred:
        unit_m3 = dred[0].price
        nd = [i for i in dred if any("비굴착" in p for p in i.path)]
        vol = sum(i.qty for i in nd)
        full_items = dogeup.find("비굴착전체보수")
        area_len = sum(3.141592653589793 * (d / 1000) ** 2 / 4 * i.qty for i in full_items if (d := i.diameter()))
        ratio = round(vol / area_len, 3) if vol and area_len else 0.25
        pts = {d: unit_m3 * 3.141592653589793 * (d / 1000) ** 2 / 4 * ratio for d in STD_DIAMETERS}
        up["dredging_per_m"] = {str(d): int(round(v, -1)) for d, v in pts.items()}
        up["cleaning_per_m"] = {str(d): 0 for d in STD_DIAMETERS}
        src["dredging_per_m"] = (f"'준설 및 세정' {unit_m3:,.0f}원/㎥ × 관단면적 × 준설률 {ratio} "
                                 f"(설계 준설량 {vol:,.1f}㎥ ÷ 전체보수 구간 관단면 체적 {area_len:,.1f}㎥)")
        src["cleaning_per_m"] = "설계는 '준설 및 세정' 통합 단가 → 세정은 준설에 포함(0)"

    # 4) 돌출관 절단
    cut = dogeup.find("연결관절단공", "미만") or dogeup.find("연결관절단공")
    if cut:
        up["lateral_cut_per_site"] = int(round(cut[0].price, -1))
        src["lateral_cut_per_site"] = f"'연결관절단공 {cut[0].spec}' 개소당 단가"

    # 5) 토공
    exc = dogeup.find("터파기:토사")
    haul_out = one("토사운반(현장→중간적치장")
    if exc:
        v = _w_avg(exc) + (haul_out.price if haul_out else 0)
        up["excavation_per_m3"] = int(round(v, -1))
        src["excavation_per_m3"] = ("터파기(토사·용수) 수량가중 평균" + (f" + 토사운반(현장→적치장) {haul_out.price:,.0f}" if haul_out else ""))
    back = one("되메우기:토사")
    haul_in = one("토사운반(중간적치장→현장")
    if back:
        ratio = haul_in.qty / back.qty if haul_in and back.qty else 0
        up["backfill_per_m3"] = int(round(back.price + (haul_in.price * ratio if haul_in else 0), -1))
        src["backfill_per_m3"] = ("되메우기" + (f" + 적치장→현장 운반 {haul_in.price:,.0f} × {ratio:.2f}(운반량/되메우기량)" if haul_in else ""))
    sand = one("관부사")
    sand_mat = (g.find("순환모래") or dogeup.find("순환모래") or dogeup.find("모래"))
    sand_haul = one("모래운반")
    if sand:
        v = sand.price + (sand_mat[0].price if sand_mat else 0) + (sand_haul.price if sand_haul else 0)
        up["sand_foundation_per_m3"] = int(round(v, -1))
        src["sand_foundation_per_m3"] = "관부사(포설) + 모래 자재 + 모래운반"
    soil = one("사토:토사")
    if soil:
        up["surplus_soil_per_m3"] = int(round(soil.price, -1))
        src["surplus_soil_per_m3"] = f"'{soil.name}' 단가"

    # 6) 가시설: 간이흙막이(원/m) ÷ 양측 벽체면적(2H), 시트파일 = 공종 금액 ÷ 벽체면적
    simple = dogeup.find("조립식간이흙막이공")
    if simple:
        num = den = 0.0
        for it in simple:
            m = re.search(r"H\s*([\d.]+)", it.spec)
            if m:
                num += it.amount
                den += it.qty * 2 * float(m.group(1))
        if den:
            up["simple_shoring_per_m2"] = int(round(num / den, -1))
            src["simple_shoring_per_m2"] = "조립식 간이흙막이(원/m) 수량가중, 양측 벽체면적(2×H) 기준 환산"
    sp = dogeup.section("SHEET-PILE공")
    piles = dogeup.find("SHEETPILE건입및뽑기")
    if sp and piles:
        wall = 0.0
        for it in piles:
            m = re.search(r"H\s*=\s*([\d.]+)", it.spec)
            if m:
                length = float(m.group(1))
                trench = it.qty * 0.4 / 2                      # 폭 400 시트파일, 양측
                wall += trench * 2 * max(length - 2.8, 1.0)    # 근입 2.8m 제외 = 굴착깊이
        if wall:
            up["steel_sheathing_per_m2"] = int(round(sp.amount / wall, -1))
            src["steel_sheathing_per_m2"] = (f"SHEET-PILE 공종 금액 {sp.amount:,.0f}원 ÷ 양측 굴착벽면적 {wall:,.0f}㎡ "
                                             "(시트파일 폭 0.4m, 근입 2.8m 가정)")

    # 7) 포장 (원/㎡): 깨기 = 절단 + (깨기+운반+상차) × 두께, 복구 = 포설·다짐 + 보조기층 + 덧씌우기 + 관급 자재
    pav_break, pav_rest = {}, {}
    asc_cut, asc_brk = one("아스팔트포장절단"), one("아스팔트포장깨기")
    asc_haul, load = one("폐아스콘운반"), one("상차:폐자재")
    base_lay = dogeup.find("기층아스콘포설및다짐")
    if asc_cut and asc_brk and base_lay:
        area = base_lay[0].qty
        t = asc_brk.qty / area
        pav_break["아스콘"] = asc_cut.price * asc_cut.qty / area + (asc_brk.price + (asc_haul.price if asc_haul else 0)
                                                                    + (load.price if load else 0)) * t
        sub = one("보조기층포설및다짐", section="아스팔트")
        overlay = one("절삭후아스팔트덧씌우기")
        coat = sum(i.price for i in dogeup.find("코팅", section="아스팔트"))
        mats = sum(i.amount for i in g.find("아스콘")) + sum(i.amount for i in g.find("보조기층재")) \
            + sum(i.amount for i in g.find("순환골재"))
        haul_m = sum(i.amount for i in dogeup.find("보조기층재운반") + dogeup.find("순환골재운반"))
        pav_rest["아스콘"] = (sum(i.price for i in base_lay) + coat + (sub.amount / area if sub else 0)
                            + (overlay.amount / area if overlay else 0) + (mats + haul_m) / area)
        src["pavement_아스콘"] = (f"포장 깨기/복구 면적 {area:,.0f}㎡ 기준: 절단·깨기·운반·상차(두께 {t:.2f}m), "
                                f"기층 포설·다짐·코팅·보조기층·절삭덧씌우기 + 관급 아스콘·골재 자재비/면적")
    con_cut, con_brk = one("콘크리트포장절단"), one("콘크리트포장깨기")
    con_lay = one("콘크리트표층인력포설")
    if con_cut and con_brk and con_lay and con_lay.qty:
        a = con_lay.qty
        haul = one("폐콘크리트운반")
        pav_break["콘크리트"] = con_cut.price * con_cut.qty / a + (con_brk.price + (haul.price if haul else 0)
                                                               + (load.price if load else 0)) * con_brk.qty / a
        remi = g.find("레미콘")
        pav_rest["콘크리트"] = con_lay.price + (remi[0].price * 0.2 if remi else 0)
        src["pavement_콘크리트"] = "콘크리트포장 절단·깨기·운반 / 표층 인력포설 + 레미콘(T=0.2m)"
    blk_rm, blk_lay = one("보도블록철거"), one("소형고압블록포장")
    if blk_rm and blk_lay:
        a = blk_lay.qty
        haul = [i for i in dogeup.find("폐콘크리트운반") if any("보도" in p for p in i.path)]
        pav_break["보도블럭"] = blk_rm.price + (haul[0].amount / a if haul else 0)
        blk_mat = sum(i.amount for i in g.find("소형고압블럭"))
        pav_rest["보도블럭"] = blk_lay.price + blk_mat / a
        src["pavement_보도블럭"] = "보도블록 철거·운반 / 소형고압블록포장 + 관급 블럭 자재비/면적"
    if pav_break:
        up["pavement_break_per_m2"] = {k: int(round(v, -1)) for k, v in pav_break.items()} | {"비포장": 0}
        up["pavement_restore_per_m2"] = {k: int(round(v, -1)) for k, v in pav_rest.items()} | {"비포장": 0}

    # 8) 관 부설(자재포함, m당): 관급 관 자재(본) + 이음관 + 부설·접합(개소) ÷ 본 길이
    pipe_mat = _dia_map(g.find("유리섬유복합관(하수)"))
    joint = _dia_map(g.find("유리섬유복합이음관"))
    lay = _dia_map([i for i in dogeup.find("유리섬유복합관(GRP관)/소켓식") if "이형" not in i.name])
    if pipe_mat and lay:
        L = 6.0
        pts = {d: (pipe_mat[d] + joint.get(d, 0) + lay.get(d, 0)) / L for d in pipe_mat if d in lay}
        up["pipe_install_per_m"] = by_diameter(pts)
        src["pipe_install_per_m"] = "유리섬유복합관(GRP) 6m: 관급 관 자재 + 이음관 + 관부설·접합(개소) ÷ 6m"
    hume_brk = one("철근콘크리트깨기")
    if hume_brk:
        haul = one("폐콘크리트운반")
        per_m3 = hume_brk.price + (haul.price if haul else 0) + (load.price if load else 0)
        pts = {d: per_m3 * 3.141592653589793 / 4 * ((d / 1000 * 1.18) ** 2 - (d / 1000) ** 2) for d in STD_DIAMETERS}
        up["pipe_removal_per_m"] = {str(d): int(round(v, -1)) for d, v in pts.items()}
        src["pipe_removal_per_m"] = f"흄관 철거 = 철근콘크리트 깨기·운반·상차 {per_m3:,.0f}원/㎥ × 관벽 체적(외경 1.18D)"

    # 9) 공종별 재료비/노무비/경비 비율
    split = {}
    for grp, key in (("토공", "토공"), ("관부설공", "관부설공"), ("가시설공", "가시설공"), ("포장공", "포장공"),
                     ("비굴착보수공", "비굴착보수공"), ("부대공", "부대공")):
        s = dogeup.section(key)
        tot = (s.lab + s.mat + s.exp) if s else 0
        if tot:
            split[grp] = {"material": round(s.mat / tot, 3), "labor": round(s.lab / tot, 3), "expense": round(s.exp / tot, 3)}
    if "관부설공" in split and pipe_mat:      # 관급 관 자재가 들어가므로 재료비 비중을 자재 포함으로 재산정
        s = dogeup.section("관부설공")
        gm = sum(i.amount for i in g.find("유리섬유복합관")) + sum(i.amount for i in g.find("흄관"))
        tot = s.lab + s.mat + s.exp + gm
        split["관부설공"] = {"material": round((s.mat + gm) / tot, 3), "labor": round(s.lab / tot, 3),
                          "expense": round(s.exp / tot, 3)}

    out = {"price_source": {"title": dogeup.title, "gwangeup": g.title or None, "basis": src,
                            "note": "설계내역서 실적 단가를 프로그램 수량 단위로 환산한 값. CCTV 촬영 단가는 내역서에 없어 기존값 유지."},
           "unit_prices": up}
    cs = {}
    if dogeup.rates:
        cs["rates_override"] = dogeup.rates
    if split:
        cs["cost_split"] = split
    if cs:
        out["cost_statement"] = cs
    return out


def apply_rates(cfg_cost: dict, overrides: dict[str, float]) -> list[dict]:
    """cost_statement.rates 목록에 요율 덮어쓰기(없는 키는 추가)."""
    names = {"subcontract_guarantee": ("하도급대금지급보증수수료", "직접공사비"),
             "equipment_guarantee": ("건설기계대여금지급보증서수수료", "직접공사비"),
             "asbestos": ("석면피해구제분담금", "노무비계"), "wage_claim": ("임금채권부담금", "노무비계")}
    rates = [dict(r) for r in cfg_cost.get("rates", [])]
    keys = {r["key"] for r in rates}
    for r in rates:
        if r["key"] in overrides:
            r["rate"] = overrides[r["key"]]
    for k, v in overrides.items():
        if k not in keys and k in names:
            rates.append({"key": k, "name": names[k][0], "rate": v, "base": names[k][1]})
    return rates


def build_price_file(dogeup_path: str | Path, gwangeup_path: str | Path | None, out_path: str | Path,
                     base_cfg: dict) -> tuple[Path, dict]:
    d = read_stmate(dogeup_path)
    g = read_stmate(gwangeup_path) if gwangeup_path else None
    res = derive(d, g)
    cs = res.get("cost_statement", {})
    if "rates_override" in cs:
        cs["rates"] = apply_rates(base_cfg["cost_statement"], cs.pop("rates_override"))
        cs["note"] = f"원가계산 요율·공종별 비목 비율: {d.title} 총괄설계내역서·설계내역서 실적"
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return out, res


def set_default(default_path: str | Path, price: dict) -> None:
    """default_criteria.json 의 unit_prices·cost_statement 를 환산 단가로 교체(다른 항목은 유지)."""
    p = Path(default_path)
    cfg = json.loads(p.read_text(encoding="utf-8"))
    for k, v in price.get("unit_prices", {}).items():
        cfg["unit_prices"][k] = v
    for k, v in price.get("cost_statement", {}).items():
        if isinstance(v, dict) and isinstance(cfg["cost_statement"].get(k), dict):
            cfg["cost_statement"][k].update(v)      # cost_split 등은 기존 항목(default 포함) 유지하며 덮어쓰기
        else:
            cfg["cost_statement"][k] = v
    cfg["price_source"] = price.get("price_source")
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
