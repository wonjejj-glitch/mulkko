"""조서 입력 양식(빈 엑셀) 생성 - nPASS/PipeAsset 내보내기를 이 양식으로 정리해 입력할 수 있다."""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill

REQ = PatternFill("solid", fgColor="FFF2CC")
OPT = PatternFill("solid", fgColor="E2EFDA")

PIPE_COLS = [
    ("관번", True, "관로 고유번호. 결함내역 시트의 관번과 일치해야 함"),
    ("시점맨홀", False, "상류 맨홀 번호"),
    ("종점맨홀", False, "하류 맨홀 번호"),
    ("관종", False, "흄관/VR관/PE관/PVC관/도관 등 (미입력 시 흄관)"),
    ("관경(mm)", True, "내경 mm"),
    ("도면연장(m)", True, "GIS/실측 평면연장. 결함 위치 보정의 기준"),
    ("촬영연장(m)", False, "CCTV 주행거리. 미입력 시 도면연장과 동일(보정 없음)"),
    ("평균심도(m)", False, "평균 관저 매설심도. 굴착 단면·가시설 판정에 사용"),
    ("포장종류", False, "아스콘/콘크리트/보도블럭/비포장"),
    ("조사방향", False, "순방향(시점→종점) 또는 역방향"),
    ("적용매뉴얼", False, "2015 / 2017 / 2025 (이 관로 CCTV 판독에 쓴 매뉴얼. 비우면 실행 시 기본값)"),
    ("설계유량(㎥/일)", False, "계획시간최대오수량(오수관로) 또는 계획하수량(합류). 입력 시 Manning 식으로 통수능·유속 자동 검토. L/s·㎥/s 열 이름도 인식"),
    ("관로구분", False, "오수/우수/합류. 유속 기준(KDS 61 40 00): 오수 0.6~3.0 m/s, 우수·합류 0.8~3.0 m/s. 비우면 오수"),
    ("조도계수", False, "Manning n. 미입력 시 관종별 기본값"),
    ("통수능부족", False, "SWMM 검토 결과 Y/빈칸 (입력 시 우선 적용)"),
    ("통수능비", False, "설계유량/통수능 (1 초과 시 부족)"),
    ("필요관경", False, "SWMM 검토 결과 개량관경 mm"),
    ("시점X", False, "도면 좌표(DXF/LSP 배치용, 미입력 시 임시배치)"),
    ("시점Y", False, ""), ("종점X", False, ""), ("종점Y", False, ""),
    ("시점지반고", False, "EL.m (종단면도용)"), ("시점관저고", False, "EL.m"),
    ("종점지반고", False, "EL.m"), ("종점관저고", False, "EL.m. 입력 시 경사 산정"),
]
DEFECT_COLS = [
    ("관번", True, "관로제원 시트의 관번"),
    ("거리(m)", True, "CCTV 조사 시작 기준 결함 위치"),
    ("종료거리(m)", False, "연속결함의 끝 위치"),
    ("결함코드", False, "적용매뉴얼의 코드. 2017: CL CC CM SD LD BC DF BK CX PO HL LP LS JD JF JS SG NS / DE DS DG TO RI IF, "
                     "2025: 2017과 같으나 뿌리 RT·막힘 PB, 2015: LP LF JO JF JD CL CC CM DP PH TM SD PB PX DF PF PL OP RF IP / "
                     "OT ED DE DG RI (없으면 결함내용으로 판별)"),
    ("결함내용", False, "예: 원주방향 균열, 토사퇴적 20%"),
    ("구조등급", False, "대/중/소 (2015는 A/B/C. 5/4/3도 인식)"),
    ("운영등급", False, "대/중/소 또는 A/B/C"),
]


def _sheet(ws, cols: list[tuple[str, bool, str]]) -> None:
    for j, (name, required, note) in enumerate(cols, start=1):
        c = ws.cell(row=1, column=j, value=name)
        c.font = Font(bold=True)
        c.fill = REQ if required else OPT
        if note:
            c.comment = Comment(note, "template")
        ws.column_dimensions[c.column_letter].width = max(12, len(name) * 2.2)
    ws.freeze_panes = "B2"


def make_template(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "관로제원"
    _sheet(ws, PIPE_COLS)
    _sheet(wb.create_sheet("결함내역"), DEFECT_COLS)
    guide = wb.create_sheet("작성안내")
    for line in [
        "노란색 머리글 = 필수, 녹색 = 선택 (머리글에 마우스를 올리면 설명 표시)",
        "열 이름은 nPASS/PipeAsset 내보내기 이름도 인식합니다(config/default_criteria.json 의 column_aliases).",
        "시트 이름은 자유이며, 열 이름으로 관로제원/결함내역 시트를 자동 판별합니다.",
        "제목·결재란이 위에 있어도 머리글 행을 자동 탐색합니다(상단 20행 이내).",
        "결함 위치는 CCTV 조사 시작점 기준이며, 도면연장/촬영연장 비율로 자동 보정됩니다.",
    ]:
        guide.append([line])
    guide.column_dimensions["A"].width = 100
    wb.save(path)
    return path
