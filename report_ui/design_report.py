"""설계보고서 초안(.docx) 자동 작성: '노후하수관로 정비계획' 장의 본문·표를 판정 결과로 채운다.

설계자가 그대로 붙여 다듬을 수 있도록 실시설계 보고서의 통상 목차를 따른다.
  1. 과업 개요  2. 조사 결과  3. 정비 판정 기준  4. 정비공법 선정 결과  5. 관로별 선정 사유
  6. 수량 및 개략공사비  7. 맨홀 정비계획(맨홀 결과가 있을 때)  8. 검토 의견 및 유의사항
"""
from __future__ import annotations

from collections import Counter
from datetime import date

import pandas as pd

from common.models import ProjectResult
from report_ui import tables as T
from report_ui.docx_mini import Doc

MANUAL_NAME = {"2015": "하수관로 정밀조사 매뉴얼(2015, 환경부)", "2017": "하수관로·맨홀 조사 및 상태등급 판단기준 표준매뉴얼(2017.06, 환경부)",
               "2025": "하수관로 조사 및 상태평가 표준매뉴얼(2025, 환경부)"}


def _won(v: float) -> str:
    return f"{v / 1e8:,.2f}억원" if abs(v) >= 1e8 else f"{v / 1e4:,.0f}만원"


def _rows(df: pd.DataFrame) -> list[list]:
    out = []
    for r in df.itertuples(index=False):
        out.append([None if (isinstance(v, float) and v != v) else (int(v) if hasattr(v, "dtype") and str(v.dtype).startswith("int") else v)
                    for v in r])
    return out


def build_design_report(res: ProjectResult, project: str = "노후하수관로 정비사업", manhole_result: pd.DataFrame | None = None,
                        manhole_works: pd.DataFrame | None = None, total_cost: float | None = None) -> bytes:
    d = Doc()
    pipes = res.pipes
    decs = res.decisions
    n = len(pipes)
    L = sum(p.plan_length_m for p in pipes)
    manuals = Counter((decs[p.pipe_id].manual if p.pipe_id in decs else p.manual or "2017") for p in pipes)
    summary = T.summary_by_method(res)
    body = summary[summary["정비공법"] != "합계"]
    tot = summary[summary["정비공법"] == "합계"].iloc[0]
    rehab = body[body["정비공법"] != "개량제외"]
    rehab_len = float(rehab["연장(m)"].sum())
    defects = T.defect_detail(res)

    # ------------------------------------------------------------- 표지
    d.p(project, "Title", align="center")
    d.p("노후하수관로 정비계획 — 설계보고서 초안", "Subtitle", align="center")
    d.p(f"작성일 {date.today():%Y. %m. %d.}  ·  물꼬(노후하수관로 정비 통합 설계 플랫폼) 자동 작성", "Small", align="center")
    d.small("※ 본 문서는 정밀조사 조서와 판정 결과로 자동 작성한 초안입니다. 수치·문장은 설계자가 검토 후 사업 여건에 맞게 수정하십시오.")
    d.page_break()

    # ------------------------------------------------------------- 1. 과업 개요
    d.h1("1. 과업 개요")
    d.p(f"본 설계는 {project} 대상 하수관로 {n:,}개 구간, 총연장 {L:,.1f}m에 대하여 CCTV 정밀조사 결과를 근거로 "
        f"결함의 위치·규모·상태등급을 평가하고, 굴착·비굴착 공법의 경제성과 시공성을 비교하여 구간별 최적 정비공법을 선정하였다.")
    d.table(["구분", "내용"], [
        ["대상 관로", f"{n:,}개 구간 · {L:,.1f} m"],
        ["관경 범위", f"D{min(p.diameter_mm for p in pipes)} ~ D{max(p.diameter_mm for p in pipes)} mm" if pipes else "-"],
        ["적용 매뉴얼", " / ".join(f"{MANUAL_NAME.get(m, m)} {c}구간" for m, c in sorted(manuals.items()))],
        ["정비 대상", f"{len(rehab) and int(rehab['관로수'].sum()):,}개 구간 · {rehab_len:,.1f} m ({rehab_len / L * 100 if L else 0:.1f}%)"],
        ["개략공사비", _won(total_cost if total_cost is not None else float(tot["공사비(원)"]))
         + (" (원가계산서 기준)" if total_cost is not None else " (직접공사비 기준)")],
    ], [1, 3])

    # ------------------------------------------------------------- 2. 조사 결과
    d.h1("2. 조사 결과")
    d.h2("2.1 관경별 연장")
    by_dia = T.length_by_diameter(res)
    d.table(list(by_dia.columns), _rows(by_dia), font_size=15)
    d.h2("2.2 결함 현황")
    if len(defects):
        g = defects.groupby(["구분", "결함명"]).agg(개소=("결함명", "size"), 대=("등급", lambda s: (s == "대").sum()),
                                                  중=("등급", lambda s: (s == "중").sum()), 소=("등급", lambda s: (s == "소").sum()),
                                                  보수대상=("보수대상", lambda s: (s == "Y").sum())).reset_index()
        g = g.sort_values(["구분", "개소"], ascending=[True, False])
        n_s = int((defects["구분"] == "구조").sum())
        n_o = int((defects["구분"] == "운영").sum())
        d.p(f"정밀조사 결과 결함은 총 {len(defects):,}개소로, 구조적 결함 {n_s:,}개소, 운영적 결함 {n_o:,}개소이며, "
            f"이 중 보수대상 결함은 {int((defects['보수대상'] == 'Y').sum()):,}개소이다. 주요 결함은 "
            + ", ".join(f"{r.결함명} {r.개소}개소" for r in g.head(4).itertuples()) + "이다.")
        d.table(["구분", "결함명", "개소", "대", "중", "소", "보수대상"], _rows(g), [1, 2.4, 1, 0.8, 0.8, 0.8, 1.2])
    else:
        d.p("조서에 결함 자료가 없다.")
    d.h2("2.3 상태평가")
    cs = Counter()
    for p in pipes:
        dc = decs.get(p.pipe_id)
        if dc:
            cs[int(round(dc.condition_score))] += 1
    d.p("매뉴얼 기준 구조적 상태점수(본 등급 평균)를 반올림한 등급별 관로 수는 다음과 같다.")
    d.table(["구조적 상태(반올림 등급)", "1 (매우좋음)", "2 (좋음)", "3 (보통)", "4 (나쁨)", "5 (매우나쁨)"],
            [["관로 수"] + [cs.get(k, 0) for k in range(1, 6)]], [2, 1, 1, 1, 1, 1])

    # ------------------------------------------------------------- 3. 판정 기준
    d.h1("3. 정비 판정 기준")
    d.p("정비공법은 결함의 종류·등급·분포에 따라 다음 순서로 판정하였다.")
    d.bullets([
        "결함 위치 보정: 촬영연장과 도면연장의 비로 결함 위치를 보정하고, 역방향 촬영은 시점 기준으로 환산",
        "근접결함 병합: 이격거리 이내 결함은 한 구간으로 묶고 양측 여유를 더해 부분보수 표준규격 적용",
        "부분보수 vs 전체보수: 불량률이 기준 이상이거나 부분보수비가 전체보수비의 손익분기 비율 이상이면 전체보수",
        "비굴착 불가 결함(파손·변형 대 등)과 통수능 부족 구간은 부분굴착 또는 굴착교체(증경) 검토",
        "운영결함은 준설·세정으로 해소하고, 결함이 없거나 경미한 구간은 개량제외",
    ])
    crit = T.criteria_table(res)
    d.table(list(crit.columns), _rows(crit), [2, 3])

    # ------------------------------------------------------------- 4. 공법 선정 결과
    d.h1("4. 정비공법 선정 결과")
    top = rehab.sort_values("연장(m)", ascending=False)
    d.p(f"판정 결과 정비 대상은 {int(rehab['관로수'].sum()) if len(rehab) else 0:,}개 구간 {rehab_len:,.1f}m이며, "
        + (", ".join(f"{r['정비공법']} {r['연장(m)']:,.1f}m({int(r['관로수'])}구간)" for _, r in top.iterrows()) if len(top) else "없음")
        + f" 이다. 부분보수는 근접결함 병합 후 {int(tot['부분보수(개소)']):,}개소를 계상하였다.")
    d.table(list(summary.columns), _rows(summary), [2, 0.8, 1, 1, 1, 1.6, 0.9, 1.2], font_size=15)

    # ------------------------------------------------------------- 5. 관로별 선정 사유
    d.h1("5. 관로별 정비공법 및 선정 사유")
    det = T.pipe_detail(res)
    rows5 = [[r["관번"], f"D{r['관경(mm)']}", r["도면연장(m)"], r["정비공법"], r["선정사유"]]
             for _, r in det.iterrows() if r["정비공법"] != "개량제외"]
    if rows5:
        d.table(["관번", "관경", "연장(m)", "정비공법", "선정 사유"], rows5, [0.9, 0.7, 0.8, 1.3, 6.3], font_size=14)
    else:
        d.p("정비 대상 관로가 없다.")

    # ------------------------------------------------------------- 6. 수량 및 공사비
    d.h1("6. 수량 및 개략공사비")
    qs, _ = T.quantity_tables(res)
    d.p("공종별 주요 수량과 직접공사비는 다음과 같다. 상세 수량은 수량산출서(Excel)를 참조한다.")
    if len(qs):
        d.table(list(qs.columns), _rows(qs), [1.2, 1.6, 2.6, 0.6, 1, 1.2, 1.4], font_size=14)
    src = (res.config.get("price_source") or {}).get("title")
    d.p(f"직접공사비 합계 {_won(float(tot['공사비(원)']))}"
        + (f", 원가계산(간접노무비·경비·일반관리비·이윤 등 포함) 총공사비 {_won(total_cost)}" if total_cost is not None else "")
        + f"이다. 단가·요율은 {src + ' 설계내역서' if src else '프로그램 예시 단가'}를 적용하였다.")

    # ------------------------------------------------------------- 7. 맨홀
    if manhole_result is not None and len(manhole_result):
        d.h1("7. 맨홀 정비계획")
        mr = manhole_result
        d.p(f"맨홀 {len(mr):,}개소를 2017 표준매뉴얼 맨홀 점수표로 평가하였다(블록 이음부 단위구간 평균). "
            f"보수대상(구조 4등급 이상 또는 운영 5등급)은 {(mr['보수대상'] == 'Y').sum():,}개소, "
            f"보수공법을 적용한 맨홀은 {(mr['보수공법'] != '').sum():,}개소이며, 맨홀 보수비는 {_won(float(mr['공사비(원)'].sum()))}이다.")
        if manhole_works is not None and len(manhole_works):
            sm = manhole_works.groupby(["공법", "단위"], as_index=False, sort=False).agg(개소=("맨홀번호", "count"),
                                                                                    수량=("수량", "sum"), 금액=("금액", "sum"))
            d.table(["공법", "단위", "개소", "수량", "금액(원)"], _rows(sm), [2, 0.7, 0.8, 1, 1.4])
        sec8 = "8"
    else:
        sec8 = "7"

    # ------------------------------------------------------------- 8. 검토 의견
    d.h1(f"{sec8}. 검토 의견 및 유의사항")
    notes = [
        "결함 위치는 CCTV 촬영거리를 도면연장으로 보정한 값이므로, 연장오차가 큰 구간은 시공 전 재확인이 필요하다.",
        "부분보수 개소는 근접결함 병합 결과이며, 시공 시 결함 상태 변화(진행성 균열·침입수)를 확인하여 조정한다.",
        "비굴착 보수 전 준설·세정과 시공 전·후 CCTV 촬영을 계상하였다.",
    ]
    d.bullets(notes)
    if res.warnings:
        d.h2("자동 검토 경고")
        d.bullets(res.warnings[:40] + ([f"외 {len(res.warnings) - 40}건"] if len(res.warnings) > 40 else []))
    return d.to_bytes(f"{project} 설계보고서 초안")
