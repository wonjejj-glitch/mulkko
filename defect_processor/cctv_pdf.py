"""CCTV 조사보고서 PDF(하수관거 현황 조사 보고서)에서 관로별 결함 위치(m)를 추출한다.

보고서 양식(실제 지자체 노후하수관로 정밀조사 2016, 조사업체 등 조사 소프트웨어 출력):
  - 머리글 표(텍스트): 관종·규격·배수방식·연장·총주행거리·미주행거리, 구조적/운영적 등급(최대/평균), 미주행 사유
  - 사진 캡션(텍스트): '이음부단차(소) [00:01:43]' - 결함명(등급) [영상시각]
  - 왼쪽 관로 도식의 위치 라벨(이미지): '0.50m' 등 → 텍스트가 아니라 작은 비트맵이라 글자 인식 필요
  - 바닥글(텍스트): '보고서번호:20365 / 관로번호:20365 - (1)'

위치 라벨은 고정 비트맵 글꼴이므로, 값을 이미 아는 시작(0.00m)·끝(총연장) 라벨로 글자 모양을 학습한 뒤
나머지 라벨을 판독한다(외부 OCR 불필요). 라벨(위→아래)과 캡션(읽기 순서)을 순서대로 짝지어 결함 위치를 얻는다.
라벨 거리는 CCTV 진입 맨홀 기준 주행거리이다(역방향 촬영은 진입 맨홀이 끝맨홀).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

CAPTION = re.compile(r"^(?P<name>.+?)\((?P<grade>[대중소ABC])\)\s*\[(?P<t>\d\d:\d\d:\d\d)\]$")
ANY_CAPTION = re.compile(r"^(?P<name>.+?)\s*\[(?P<t>\d\d:\d\d:\d\d)\]$")
FOOTER = re.compile(r"관로번호\s*:\s*(?P<pid>[^\s/]+)\s*-\s*\((?P<page>\d+)\)")
METERS = re.compile(r"^-?\d+(?:\.\d+)?m$")
GRADE5 = {"대": 5, "A": 5, "중": 4, "B": 4, "소": 3, "C": 3}


# ------------------------------------------------------------------ 비트맵 글꼴 판독
def _mask(pix) -> np.ndarray:
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n >= 3:
        r, b = a[:, :, 0].astype(int), a[:, :, 2].astype(int)
        return (b > 90) & (b - r > 40)                 # 파란 글자
    return a[:, :, 0] > 128


def _glyph_masks(mask: np.ndarray) -> list[np.ndarray]:
    cols = mask.any(axis=0)
    out, j, w = [], 0, mask.shape[1]
    while j < w:
        if not cols[j]:
            j += 1
            continue
        k = j
        while k < w and cols[k]:
            k += 1
        out.append(mask[:, j:k])
        j = k
    return out


def _key(g: np.ndarray) -> bytes:
    rows = np.flatnonzero(g.any(axis=1))
    if len(rows):
        g = g[rows[0]:rows[-1] + 1]               # 위아래 여백 제거(이미지 높이가 달라도 같은 글자는 같은 키)
    return bytes([g.shape[1], g.shape[0]]) + np.packbits(g).tobytes()


def _norm(g: np.ndarray, h: int = 20, w: int = 14) -> np.ndarray:
    """글자 비트맵을 위아래 여백 제거 후 고정 격자로 축소(라벨 문자열 길이에 따라 글자 크기가 달라지는 양식 대응)."""
    rows = np.flatnonzero(g.any(axis=1))
    if len(rows):
        g = g[rows[0]:rows[-1] + 1]
    yi = np.minimum((np.arange(h) + 0.5) * g.shape[0] / h, g.shape[0] - 1).astype(int)
    xi = np.minimum((np.arange(w) + 0.5) * g.shape[1] / w, g.shape[1] - 1).astype(int)
    return g[np.ix_(yi, xi)]


def _glyphs(mask: np.ndarray) -> list[bytes]:
    return [_key(g) for g in _glyph_masks(mask)]


def _is_minus(g: np.ndarray) -> bool:
    """가로로 납작한 막대(높이 ≤ 3px, 폭 ≥ 5px) → 음수 기호(역방향 촬영 구간 거리)."""
    rows = np.flatnonzero(g.any(axis=1))
    return len(rows) > 0 and rows[-1] - rows[0] + 1 <= 3 and g.shape[1] >= 5


def _nearest_distance_high(ocr, g) -> bool:
    near, d = ocr._nearest(g)
    return near is None or d >= 0.22


def _save_ocr(ocr, path):
    import pickle
    with open(path, "wb") as f:
        pickle.dump({"table": ocr.table, "protos": ocr.protos}, f)


def _load_ocr(ocr, path):
    import pickle
    try:
        with open(path, "rb") as f:
            d = pickle.load(f)
        ocr.table.update(d["table"])
        ocr.protos.extend(d["protos"])
    except Exception:
        pass


class GlyphOCR:
    """값을 아는 라벨로 글자 비트맵을 학습하고, 같은 글꼴의 라벨을 판독한다."""

    def __init__(self) -> None:
        self.table: dict[bytes, str] = {}
        self.protos: list[tuple[np.ndarray, float, str]] = []      # (정규화 비트맵, 가로세로비, 글자)
        self.conflicts = 0

    def _nearest(self, g: np.ndarray) -> tuple[str | None, float]:
        n = _norm(g)
        r = g.shape[1] / max(g.shape[0], 1)
        best, bd = None, 1e9
        for pn, pr, ch in self.protos:
            if abs(pr - r) > 0.25 + 0.3 * r:
                continue
            d = float((pn != n).mean())
            if d < bd:
                best, bd = ch, d
        return best, bd

    def learn(self, mask: np.ndarray, text: str) -> bool:
        gs = _glyphs(mask)
        if len(gs) != len(text):
            return False
        for g, ch in zip(gs, text):
            if self.table.get(g, ch) != ch:
                self.conflicts += 1
                continue
            self.table[g] = ch
        for m, c in zip(_glyph_masks(mask), text):
            near, d = self._nearest(m)
            if near != c or d > 0.05:
                self.protos.append((_norm(m), m.shape[1] / max(m.shape[0], 1), c))
        return True

    def read(self, mask: np.ndarray) -> str:
        out = []
        for i, g in enumerate(_glyph_masks(mask)):
            k = _key(g)
            ch = self.table.get(k)
            if ch is None and i == 0 and _is_minus(g):
                ch = self.table[k] = "-"
            if ch is None and self.protos and i == 0 and len(self.table) > 40 and _nearest_distance_high(self, g):
                ch = "-"            # 글자 크기가 문자열 길이에 맞춰 늘어난 양식: 첫 글자가 숫자가 아니면 음수 기호
            if ch is None and self.protos:
                near, d = self._nearest(g)
                if near is not None and d < 0.22:
                    ch = near
            if ch is None and i == 0 and _is_minus(g):
                ch = self.table[k] = "-"
            out.append(ch or "?")
        return "".join(out)


# ------------------------------------------------------------------ 페이지 해석
@dataclass
class PageData:
    pipe_id: str
    page_no: int
    header: dict
    captions: list[tuple[float, float, str, str | None]]     # (y, x, 이름, 등급) - 읽기 순서
    labels: list[tuple[float, object]]                         # (y, pixmap) - 위치 라벨(시작·끝 제외 전)
    start_label: object | None = None
    end_label: object | None = None


def _lines(page) -> list[tuple[float, float, str]]:
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"]).strip()
            if t:
                out.append((l["bbox"][1], l["bbox"][0], t))
    return out


def parse_page(doc, page) -> PageData | None:
    import fitz

    lines = _lines(page)
    flat = [t for _y, _x, t in sorted(lines)]
    foot = next((m for t in flat for m in [FOOTER.search(t)] if m), None)
    pid_override = None
    if not foot:
        # 2023 실제 사업 양식: 바닥글 없음 → 상·하류 맨홀번호 + 등급으로 관로를 식별(연속 페이지 병합)
        toks0 = [t.strip() for t in page.get_text().splitlines() if t.strip()]
        try:
            iu, idn = toks0.index("상류맨홀"), toks0.index("하류맨홀")
            sg = next(t for t in toks0 if t.startswith("하수관로(암거)구조적상태등급"))
            og = next(t for t in toks0 if t.startswith("하수관로(암거)운영적상태등급"))
        except (ValueError, StopIteration):
            return None
        pid_override = f"{toks0[iu + 1]}-{toks0[idn + 1]}"
        flat = toks0                      # 이 양식은 읽기 순서 그대로가 정확(y정렬 시 표 칸이 뒤섞임)
        foot = re.match(r"(?P<pid>.*)(?P<page>)", "")
    header: dict = {}
    # 머리글: '관로번호 관종 규격 배수방식 연장 총주행거리 미주행거리' 다음 값들(관로번호는 이미지일 수 있음)
    toks = [t for t in flat]
    if "총주행거리미주행거리" in toks:
        toks[toks.index("총주행거리미주행거리")] = "미주행거리"
    if "미주행거리" in toks:
        i = toks.index("미주행거리") + 1
        vals = toks[i:i + 7]
        mvals = [v for v in vals if METERS.match(v)]
        if len(mvals) >= 3:
            header["length"], header["run"], header["unrun"] = (float(v[:-1]) for v in mvals[:3])
            first_m = vals.index(mvals[0])
            pre = vals[:first_m]
            if len(pre) >= 3:
                header["material"], header["diameter"], header["kind"] = pre[-3], pre[-2], pre[-1]
    for t in flat:
        m = re.search(r"구조적등급\s*최대값\s*:\s*(\S+)\s*/\s*평균값\s*:\s*(\S+)", t)
        if m:
            header["struct_max"], header["struct_avg"] = m.group(1), m.group(2)
        m = re.search(r"운영적등급\s*최대값\s*:\s*(\S+)\s*/\s*평균값\s*:\s*(\S+)", t)
        if m:
            header["oper_max"], header["oper_avg"] = m.group(1), m.group(2)
    if pid_override:
        for t in flat:
            m = re.match(r"하수관로\(암거\)(구조적|운영적)상태등급\s*([\d.]+)", t)
            if m:
                header["struct_avg" if m.group(1) == "구조적" else "oper_avg"] = m.group(2)
        header["pair_key"] = pid_override + "|" + header.get("struct_avg", "") + "|" + header.get("oper_avg", "")
    caps = []
    for y, x, t in lines:
        m = ANY_CAPTION.match(t)
        if m and y > 200:
            c = CAPTION.match(t)
            nm = re.sub(r"^\((?:관로|상태|맨홀)\)", "", (c.group("name") if c else m.group("name")).strip())
            caps.append((y, x, nm, c.group("grade") if c else None))
    caps.sort(key=lambda c: (round(c[0] / 20), c[1]))       # 행(같은 높이) → 열 순서
    # 위치 라벨 이미지: 도식 왼쪽 열(폭 15~45pt, 높이 4~10pt)
    infos = page.get_image_info(xrefs=True)
    wide = [i for i in infos if 125 <= i["bbox"][0] <= 175 and (i["bbox"][2] - i["bbox"][0]) > 50
            and (i["bbox"][3] - i["bbox"][1]) < 12]                       # '시작맨홀: …' '끝맨홀: …'
    labels, start, end = [], None, None
    for i in infos:
        x0, y0, x1, y1 = i["bbox"]
        if not (80 <= x0 <= 130 and 15 <= x1 - x0 <= 45 and 4 <= y1 - y0 <= 25 and y0 > 200):
            continue
        comp = next((j for j in infos if abs(j["bbox"][0] - x1) < 1.5 and abs(j["bbox"][1] - y0) < 2
                     and (j["bbox"][2] - j["bbox"][0]) < 12 and (j["bbox"][3] - j["bbox"][1]) > 10), None)
        if comp is not None:      # 회현형: 숫자 그림 + 단위('m') 그림이 둘로 나뉜 라벨 → 합쳐서 렌더링
            r = fitz.Rect(x0, y0, comp["bbox"][2], y1)
            pix = page.get_pixmap(clip=r, dpi=int(24 * 72 / max(y1 - y0, 1) * 1.0 + 60))
            paired = []
            labels.append((y0, pix, False))
            continue
        pix = fitz.Pixmap(doc, i["xref"])
        if pix.colorspace is None:           # 마스크형 이미지 → 화면 렌더링으로 대체
            pix = page.get_pixmap(clip=fitz.Rect(i["bbox"]), dpi=int(72 * i["width"] / max(x1 - x0, 1)))
        elif pix.alpha or pix.n > 3:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        paired = [w for w in wide if abs(w["bbox"][1] - y0) < 4]
        if paired and start is None and (not labels or y0 < min(l[0] for l in labels)):
            start = (y0, pix)
        labels.append((y0, pix, bool(paired)))
    labels.sort(key=lambda l: l[0])
    if labels and labels[0][2]:
        start = (labels[0][0], labels[0][1])
    if len(labels) >= 2 and labels[-1][2]:
        end = (labels[-1][0], labels[-1][1])
    mid = [(y, p) for y, p, paired in labels if not paired]
    return PageData(pid_override or foot.group("pid"), int(foot.group("page") or 0), header, caps, mid,
                    start[1] if start else None, end[1] if end else None)


# ------------------------------------------------------------------ 보고서 추출
@dataclass
class PipeReport:
    pipe_id: str
    source: str
    header: dict = field(default_factory=dict)
    defects: list[tuple[float, str, int]] = field(default_factory=list)      # (위치m, 결함명, 등급5/4/3)
    events: list[tuple[float, str]] = field(default_factory=list)            # 비결함 캡션(조사시작 등)
    warnings: list[str] = field(default_factory=list)
    pages: int = 0


def extract_pdf(path: str | Path, ocr: GlyphOCR | None = None, max_pages: int | None = None,
                is_defect=None) -> tuple[list[PipeReport], GlyphOCR, dict]:
    """PDF 한 권에서 관로별 결함 위치를 추출한다. is_defect(name) → bool (기본: 결함코드 매핑 가능 여부)."""
    import fitz

    from defect_processor.count_converter import map_label

    is_defect = is_defect or (lambda n: map_label(n) is not None)
    ocr = ocr or GlyphOCR()
    doc = fitz.open(str(path))
    n = len(doc) if max_pages is None else min(max_pages, len(doc))
    pages = [p for p in (parse_page(doc, doc[i]) for i in range(n)) if p is not None]
    # 1차: 시작(0.00m)·끝(총연장) 라벨로 글꼴 학습
    for pd_ in pages:
        if pd_.start_label is not None:
            ocr.learn(_mask(pd_.start_label), "0.00m")
        if pd_.end_label is not None and "length" in pd_.header:
            ocr.learn(_mask(pd_.end_label), f"{pd_.header['length']:.2f}m")
    reports: dict[str, PipeReport] = {}
    stats = {"pages": len(pages), "labels": 0, "unread": 0, "mismatch_pages": 0}
    for pd_ in pages:
        rep = reports.setdefault(pd_.pipe_id, PipeReport(pd_.pipe_id, Path(str(path)).name))
        rep.pages += 1
        rep.header.update({k: v for k, v in pd_.header.items() if k not in rep.header})
        values = []
        for _y, pix in pd_.labels:
            txt = ocr.read(_mask(pix))
            stats["labels"] += 1
            try:
                values.append(float(txt.rstrip("m")))
            except ValueError:
                values.append(None)
                stats["unread"] += 1
        caps = pd_.captions
        L = pd_.header.get("length") or rep.header.get("length")
        # 조사중단 등으로 끝맨홀 표시가 빠지면 총연장 라벨이 남는다 → 제거
        if len(values) == len(caps) + 1 and L and values[-1] is not None and abs(values[-1] - L) < 0.011:
            values = values[:-1]
        elif len(values) == len(caps) + 1 and values[0] == 0.0:
            values = values[1:]
        # 음수 거리 = 역방향 촬영 구간(끝맨홀 기준) → 시점 기준 거리로 환산
        values = [(round(L + v, 2) if (v is not None and v < 0 and L) else v) for v in values]
        if len(values) != len(caps):
            stats["mismatch_pages"] += 1
            rep.warnings.append(f"p{pd_.page_no}: 위치라벨 {len(values)}개 ≠ 사진캡션 {len(caps)}개 - 해당 페이지 결함 위치 미확정")
            for _y, _x, name, grade in caps:
                if grade and is_defect(name):
                    rep.defects.append((float("nan"), name, GRADE5[grade]))
            continue
        for v, (_y, _x, name, grade) in zip(values, caps):
            pos = float("nan") if v is None else v
            if grade and is_defect(name):
                rep.defects.append((pos, name, GRADE5[grade]))
            else:
                rep.events.append((pos, name))
    return list(reports.values()), ocr, stats


def write_survey(reports: list[PipeReport], path: str | Path, manual: str = "2015") -> Path:
    """표준 조서(관로제원·결함내역, 실제 위치)로 저장."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    from defect_processor.count_converter import MATERIAL_MAP, OPERATIONAL_STATE, TO_MANUAL, map_label

    trans = TO_MANUAL.get(manual, {})
    wb = Workbook()
    ws = wb.active
    ws.title = "관로제원"
    ws.append(["관번", "관종", "관경(mm)", "도면연장(m)", "촬영연장(m)", "주행거리(m)", "배수방식", "적용매뉴얼",
               "조사기관 구조상태(최대)", "조사기관 구조상태(평균)", "조사기관 운영상태(최대)", "조사기관 운영상태(평균)",
               "출처", "추출경고"])
    ws2 = wb.create_sheet("결함내역")
    ws2.append(["관번", "거리(m)", "결함코드", "결함내용", "구조등급", "운영등급", "비고"])
    for r in reports:
        h = r.header
        L = h.get("length")
        if not L:
            continue
        run = h.get("run") or L
        dia = re.sub(r"[^0-9]", "", str(h.get("diameter", ""))) or None
        ws.append([r.pipe_id, MATERIAL_MAP.get(str(h.get("material", "")).replace("(HP)", ""), h.get("material")),
                   int(dia) if dia else None, L, L, run, h.get("kind"), manual,
                   h.get("struct_max"), h.get("struct_avg"), h.get("oper_max"), h.get("oper_avg"),
                   r.source, " / ".join(r.warnings)[:250]])
        for pos, name, g in r.defects:
            code = map_label(name)
            if code is None:
                continue
            struct = code not in OPERATIONAL_STATE
            note = "CCTV 보고서 위치" if pos == pos else "위치 미확정"
            ws2.append([r.pipe_id, pos if pos == pos else None, trans.get(code, code), name,
                        g if struct else None, None if struct else g, note])
    for w in (ws, ws2):
        for c in w[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="DDEBF7")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
