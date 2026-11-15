"""테스트용 샘플 조서(nPASS Report 형식 모사) 생성.

관로 12개에 각 판정 분기(부분보수·병합·불량률 전환·경제성 전환·부분굴착·굴착교체·증경·
준설/세정·개량제외·역방향 촬영·연장오차·좌표 누락)를 하나 이상 포함한다.
좌표는 EPSG:5186(중부원점) 범위의 가상 좌표이다.
"""
from __future__ import annotations

import math
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

# (관번, 시점, 종점, 관종, 관경, 도면연장, 촬영연장, 평균심도, 포장, 조사방향, 통수능부족, 통수능비, 필요관경, 방위각°, 지반고(시,종))
PIPES = [
    ("P-001", "MH-01", "MH-02", "흄관", 300, 45.0, 44.2, 1.6, "아스콘", "순방향", "", None, None, -10, (32.40, 32.10)),
    ("P-002", "MH-02", "MH-03", "흄관", 300, 40.0, 40.0, 1.8, "아스콘", "순방향", "", None, None, -15, (32.10, 31.85)),
    ("P-003", "MH-03", "MH-04", "흄관", 350, 44.0, 44.0, 2.0, "아스콘", "순방향", "", None, None, -5, (31.85, 31.60)),
    ("P-004", "MH-04", "MH-05", "흄관", 400, 52.0, 55.4, 2.2, "콘크리트", "역방향", "", None, None, -60, (31.60, 31.20)),
    ("P-005", "MH-05", "MH-06", "흄관", 300, 38.0, 38.0, 1.9, "아스콘", "순방향", "", None, None, -80, (31.20, 30.95)),
    ("P-006", "MH-06", "MH-07", "VR관", 250, 35.0, 35.3, 1.5, "보도블럭", "순방향", "", None, None, -95, (30.95, 30.80)),
    ("P-007", "MH-07", "MH-08", "흄관", 400, 60.0, 60.5, 2.8, "아스콘", "순방향", "", None, None, -100, (30.80, 30.40)),
    ("P-008", "MH-08", "MH-09", "흄관", 600, 55.0, 55.0, 4.3, "아스콘", "순방향", "", None, None, -120, (30.40, 30.10)),
    ("P-009", "MH-11", "MH-04", "PE관", 300, 42.0, 42.0, 1.4, "아스콘", "순방향", "", None, None, 80, (32.30, 31.60)),
    ("P-010", "MH-12", "MH-11", "흄관", 250, 30.0, 30.4, 1.3, "비포장", "순방향", "", None, None, 90, (None, None)),
    ("P-011", "MH-13", "MH-12", "흄관", 450, 50.0, 50.0, 1.7, "아스콘", "순방향", "", None, None, None, (None, None)),
    ("P-012", "MH-09", "MH-10", "흄관", 300, 36.0, 35.0, 1.7, "아스콘", "순방향", "", None, None, -30, (30.10, 29.90)),
]

# 설계유량(L/s): P-007은 Manning 만관 통수능(약 114 L/s)을 초과 → 자동 통수능 부족 판정(증경 D450)
DESIGN_FLOW = {"P-001": 18.0, "P-002": 22.0, "P-003": 40.0, "P-004": 55.0, "P-007": 150.0, "P-008": 260.0}

# (관번, 거리, 종료거리, 결함코드, 결함내용, 구조등급, 운영등급)
DEFECTS = [
    # P-001 : 근접결함 병합 1건 + 단독 1건 → 비굴착 부분보수, 운영결함 준설
    # 결함코드·등급은 환경부 표준매뉴얼(2017) 체계(대/중/소). 보수대상 = 결함점수 20점 이상.
    ("P-001", 12.2, None, "CC", "원주방향 균열(철근노출)", "대", None),       # 40점
    ("P-001", 12.8, None, "JD", "이음부 단차(관경 10% 미만)", "소", None),   # 20점(보수대상, 필수굴착은 중 이상)
    ("P-001", 30.5, None, "CL", "길이방향 균열(철근노출)", "대", None),       # 40점
    ("P-001", 38.0, None, "DS", "토사퇴적 관경 25%", None, "대"),             # 45점(운영)
    # P-002 : 불량률 50% → 비굴착 전체보수
    *[("P-002", d, None, "JF", "이음부 손상(공동·철근부식)", "대", None) for d in
      (1.2, 6.1, 11.3, 16.4, 21.2, 26.0, 31.5, 36.4)],                       # 33점 × 8
    # P-003 : 불량률 22.2%(<25%)이나 부분보수비가 전체보수의 81.4% → 경제성 전체보수
    ("P-003", 5.1, None, "CL", "길이방향 균열", "대", None),
    ("P-003", 7.4, None, "JF", "이음부 손상", "대", None),
    ("P-003", 15.1, None, "CC", "원주방향 균열", "대", None),
    ("P-003", 17.3, None, "IF", "침입수(표면을 따라 흐름)", "중", None),       # 28점(운영적 상태, 지수 부분보수)
    ("P-003", 25.2, None, "SD", "표면손상(골재노출)", "중", None),             # 20점
    ("P-003", 27.3, None, "CC", "원주방향 균열", "대", None),
    ("P-003", 33.0, 34.2, "CL", "연속 길이방향 균열", "대", None),
    # P-004 : 역방향 촬영 + 연장오차 6.5% + 파손(대) 1개소 → 부분굴착 + 비굴착 부분보수
    ("P-004", 20.0, None, "BK", "파손(단면적 20% 초과 손실)", "대", None),    # 90점
    ("P-004", 21.0, None, "CL", "길이방향 균열", "중", None),
    ("P-004", 40.0, None, "JF", "이음부 손상", "대", None),
    # P-005 : 필수굴착 결함 3개소 > 허용 2개소 → 굴착교체(동경)
    ("P-005", 5.0, None, "BK", "파손", "대", None),
    ("P-005", 15.0, None, "SG", "침하(정체수위 관경 30%)", "중", None),
    ("P-005", 28.0, None, "", "관 파손(뒷채움재 목격)", "중", None),          # 코드 누락 → 결함내용으로 판별
    # P-006 : 역경사 → 굴착교체(동경)
    ("P-006", 10.0, 18.0, "NS", "역경사 구간", "소", None),
    # P-007 : 통수능 부족 → 증경
    ("P-007", 22.0, None, "CL", "길이방향 균열", "대", None),
    # P-008 : 대구경 심층(H=4.3m) 필수굴착 결함 2개소 → 부분굴착(강재토류판)
    ("P-008", 30.0, None, "BK", "파손", "대", None),
    ("P-008", 45.0, None, "DF", "변형(관경 10%)", "중", None),
    # P-009 : 운영결함 + 돌출관 → 준설/세정
    ("P-009", 10.0, None, "RI", "뿌리침입(관경 15% 감소)", None, "중"),       # 33점
    ("P-009", 22.0, None, "DS", "토사퇴적 30%", None, "대"),
    ("P-009", 30.0, None, "LP", "연결관 돌출 관경 30% 초과", "대", None),      # 25점
    # P-010 : 경미결함만 → 개량제외
    ("P-010", 12.0, None, "CL", "길이방향 미세 균열", "소", None),            # 5점
    ("P-010", 20.0, None, "JF", "이음부 작은 균열", "소", None),              # 7점
    # P-012 : 3개 결함 연쇄 병합(2.5m 1개소) + 단독 1개소
    ("P-012", 8.0, None, "CL", "길이방향 균열", "대", None),
    ("P-012", 8.9, None, "JF", "이음부 손상", "대", None),
    ("P-012", 9.8, None, "CC", "원주방향 균열", "대", None),
    ("P-012", 25.0, None, "CL", "길이방향 균열", "대", None),
    # 검증용 오류 행 (경고 출력 확인)
    ("P-012", 30.0, None, "XX", "기타 미분류", "중", None),
    ("P-999", 5.0, None, "CL", "균열", "대", None),
]


def _coords():
    """관로 연결 순서대로 방위각·도면연장을 적용하여 맨홀 좌표 생성."""
    mh = {"MH-01": (198500.0, 551200.0), "MH-11": None}
    out = {}
    # 본선(MH-01 → MH-10)
    for row in PIPES:
        pid, f, t, *_rest = row
        L, az = row[5], row[13]
        if az is None or pid in ("P-009", "P-010"):
            continue
        x0, y0 = mh[f]
        x1, y1 = x0 + L * math.cos(math.radians(az)), y0 + L * math.sin(math.radians(az))
        mh[t] = (round(x1, 3), round(y1, 3))
        out[pid] = (mh[f], mh[t])
    # 지선: MH-12 → MH-11 → MH-04 (하류 기준 역산)
    x4, y4 = mh["MH-04"]
    L9 = PIPES[8][5]
    mh["MH-11"] = (round(x4 - L9 * math.cos(math.radians(80)), 3), round(y4 - L9 * math.sin(math.radians(80)), 3))
    out["P-009"] = (mh["MH-11"], mh["MH-04"])
    L10 = PIPES[9][5]
    x11, y11 = mh["MH-11"]
    mh["MH-12"] = (round(x11 - L10 * math.cos(math.radians(90)), 3), round(y11 - L10 * math.sin(math.radians(90)), 3))
    out["P-010"] = (mh["MH-12"], mh["MH-11"])
    return out


def make_sample(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    coords = _coords()
    wb = Workbook()
    ws = wb.active
    ws.title = "관로제원"
    ws.append(["하수관로 정밀조사 결과 조서 (nPASS Report 형식 예시)"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["조사기간: 2026.09 ~ 2026.10", "", "", "좌표계: EPSG:5186"])
    ws.append([])
    ws.append(["관번", "시점맨홀", "종점맨홀", "관종", "관경(mm)", "도면연장(m)", "촬영연장(m)", "평균심도(m)", "포장종류",
               "조사방향", "통수능부족", "통수능비", "필요관경", "시점X", "시점Y", "종점X", "종점Y",
               "시점지반고", "시점관저고", "종점지반고", "종점관저고", "설계유량(L/s)"])
    for row in PIPES:
        pid, f, t, mat, dia, L, cctv, dep, pav, dirc, cap, ratio, req, _az, (gf, gt) = row
        (x0, y0), (x1, y1) = coords.get(pid, ((None, None), (None, None)))
        if gf is not None:
            slope = 0.004 if dia <= 300 else 0.003
            il_f = round(gf - dep - 0.10, 3)
            il_t = round(il_f - L * slope, 3)
        else:
            il_f = il_t = None
        flow = DESIGN_FLOW.get(pid)
        ws.append([pid, f, t, mat, dia, L, cctv, dep, pav, dirc, cap, ratio, req, x0, y0, x1, y1, gf, il_f, gt, il_t, flow])

    ws2 = wb.create_sheet("결함내역")
    ws2.append(["관번", "거리(m)", "종료거리(m)", "결함코드", "결함내용", "구조등급", "운영등급"])
    for r in DEFECTS:
        ws2.append(list(r))
    for w in (ws, ws2):
        for col in w.columns:
            w.column_dimensions[col[0].column_letter].width = 13
    wb.save(path)
    return path


if __name__ == "__main__":
    out = make_sample(Path(__file__).with_name("sample_survey.xlsx"))
    print(f"샘플 조서 생성: {out}")
