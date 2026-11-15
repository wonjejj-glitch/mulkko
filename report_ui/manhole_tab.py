"""대시보드 '맨홀' 탭: 맨홀 결함 조서 → 상태등급(2017 표준매뉴얼) · 보수공법 · 공법별 수량·공사비.

입력은 두 가지를 받는다.
  1) 간단 양식: 한 행 = 결함 1건 (맨홀번호, 결함명, 등급[대/중/소], 맨홀깊이(m), 블록이음부수) - 결함 없는 맨홀은 결함명 빈칸
  2) PC의 manhole-pdf 결과 엑셀(2_맨홀조서 + 3_맨홀결함 시트)
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from rehab_decision_ve.manhole import decide_repair, grade_manhole, repair_rules, state_name, tables

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_data" / "sample_manholes.xlsx"
TEMPLATE_COLS = ["맨홀번호", "결함명", "등급", "맨홀깊이(m)", "블록이음부수"]


def _num(v) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", str(v if v is not None else ""))
    return float(m.group()) if m else None


def read_manholes(src) -> list[dict]:
    """엑셀 → [{번호, 깊이, 이음부, 결함:[(이름, 등급)]}] (입력 순서 유지)."""
    xl = pd.ExcelFile(src)
    out: dict[str, dict] = {}
    if "3_맨홀결함" in xl.sheet_names and "2_맨홀조서" in xl.sheet_names:      # manhole-pdf 결과
        m = xl.parse("2_맨홀조서")
        d = xl.parse("3_맨홀결함")
        for _, r in m.iterrows():
            k = str(r["기록번호"])
            out[k] = {"번호": str(r.get("상류맨홀", k)), "깊이": _num(r.get("맨홀깊이")),
                      "이음부": int(r.get("블록이음부수") or 0), "결함": []}
        for _, r in d.iterrows():
            k = str(r["기록번호"])
            if k in out and isinstance(r.get("결함명"), str):
                out[k]["결함"].append((r["결함명"].strip(), str(r.get("등급", "중")).strip()))
        return list(out.values())
    df = xl.parse(xl.sheet_names[0])
    miss = [c for c in ("맨홀번호", "결함명", "등급") if c not in df.columns]
    if miss:
        raise ValueError(f"열이 없습니다: {', '.join(miss)} (양식: {', '.join(TEMPLATE_COLS)})")
    for _, r in df.iterrows():
        no = str(r["맨홀번호"]).strip()
        if not no or no == "nan":
            continue
        e = out.setdefault(no, {"번호": no, "깊이": None, "이음부": 0, "결함": []})
        if e["깊이"] is None and "맨홀깊이(m)" in df.columns:
            e["깊이"] = _num(r["맨홀깊이(m)"])
        if "블록이음부수" in df.columns and _num(r["블록이음부수"]):
            e["이음부"] = max(e["이음부"], int(_num(r["블록이음부수"])))
        if isinstance(r["결함명"], str) and r["결함명"].strip():
            e["결함"].append((r["결함명"].strip(), str(r["등급"]).strip() or "중"))
    return list(out.values())


def evaluate(mhs: list[dict], prices: dict[str, float]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, works = [], []
    for m in mhs:
        g = grade_manhole(m["결함"], ["블록이음부존재(중)"] * m["이음부"])
        ws = decide_repair(m["결함"], g["struct_grade"], g["oper_grade"], m["깊이"])
        for w in ws:
            if w["공법"] in prices and not w["공법"] == "맨홀재설치":
                w["단가"] = prices[w["공법"]]
                w["금액"] = int(round(w["수량"] * w["단가"]))
            works.append({"맨홀번호": m["번호"], **w})
        rows.append({"맨홀번호": m["번호"], "깊이(m)": m["깊이"], "결함수": len(m["결함"]),
                     "구조적 상태등급": g["struct_grade"], "운영적 상태등급": g["oper_grade"], "상태": state_name(g["struct_grade"]),
                     "보수대상": "Y" if g["repair"] else "", "보수공법": ", ".join(w["공법"] for w in ws),
                     "공사비(원)": sum(w["금액"] for w in ws), "미등록 결함": ", ".join(sorted(set(g["unknown"])))})
    return pd.DataFrame(rows), pd.DataFrame(works)


def _template_bytes() -> bytes:
    bio = io.BytesIO()
    ex = pd.DataFrame([["MH-001", "인버트결함", "대", 1.8, 0], ["MH-001", "맨홀뚜껑", "중", 1.8, 0],
                       ["MH-002", "", "", 2.4, 1]], columns=TEMPLATE_COLS)
    names = pd.DataFrame([(n, "구조", "/".join(v["scores"])) for n, v in tables()["structural"].items()] +
                         [(n, "운영", "/".join(v["scores"])) for n, v in tables()["operational"].items()],
                         columns=["결함명(이 이름 그대로)", "구분", "등급"])
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        ex.to_excel(w, sheet_name="맨홀결함", index=False)
        names.to_excel(w, sheet_name="결함명 목록", index=False)
    return bio.getvalue()


def render(project: str) -> None:
    st.subheader("맨홀 상태평가 · 보수공법 · 수량")
    st.caption("2017 하수관로 표준매뉴얼 맨홀 점수표로 상태등급을 매기고(블록 이음부 단위구간 평균), 설계 관행에 맞춘 규칙으로 보수공법·수량·공사비를 산출합니다.")
    c1, c2 = st.columns([3, 1])
    up = c1.file_uploader("맨홀 결함 조서 (.xlsx) — 간단 양식 또는 PC의 manhole-pdf 결과", type=["xlsx"], key="mh_up")
    c2.download_button("📄 입력 양식 받기", _template_bytes(), file_name="맨홀결함_입력양식.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    src = up if up is not None else (SAMPLE if SAMPLE.exists() else None)
    if src is None:
        st.info("맨홀 결함 조서를 올리면 결과가 표시됩니다.")
        return
    if up is None:
        st.caption("샘플 맨홀 자료 사용 중 (sample_data/sample_manholes.xlsx)")
    try:
        mhs = read_manholes(src)
    except Exception as e:  # noqa: BLE001
        st.error(f"맨홀 조서를 읽지 못했습니다: {e}")
        return
    rules = repair_rules()["works"]
    with st.expander("💰 맨홀 보수 단가 편집", expanded=False):
        pdf = pd.DataFrame([{"공법": k, "단위": v["unit"], "단가(원)": v.get("price", v.get("price_by_depth", [[0, 0]])[0][1]),
                             "단가 근거": v.get("price_basis", "")} for k, v in rules.items() if k != "맨홀재설치"])
        pdf = st.data_editor(pdf, disabled=["공법", "단위", "단가 근거"], hide_index=True, use_container_width=True, key="mh_price")
        st.caption("맨홀재설치는 깊이 구간별 단가(" + ", ".join(f"H≤{h}m {p:,.0f}원" for h, p in rules["맨홀재설치"]["price_by_depth"]) + ")")
    prices = dict(zip(pdf["공법"], pdf["단가(원)"].astype(float)))
    res, works = evaluate(mhs, prices)
    k = st.columns(4)
    k[0].metric("맨홀 수", f"{len(res):,}")
    k[1].metric("보수대상(구조 4↑·운영 5)", f"{(res['보수대상'] == 'Y').sum():,}")
    k[2].metric("보수공법 적용 맨홀", f"{(res['보수공법'] != '').sum():,}")
    k[3].metric("맨홀 보수 공사비", f"{res['공사비(원)'].sum() / 1e8:,.2f}억원" if res['공사비(원)'].sum() >= 1e8 else f"{res['공사비(원)'].sum():,.0f}원")
    st.markdown("**공법별 수량·금액**")
    if len(works):
        sm = works.groupby(["공법", "단위"], as_index=False, sort=False).agg(개소=("맨홀번호", "count"), 수량=("수량", "sum"), 금액=("금액", "sum"))
        sm["단가 근거"] = [rules.get(x, {}).get("price_basis", "") for x in sm["공법"]]
        st.dataframe(sm, hide_index=True, use_container_width=True,
                     column_config={"금액": st.column_config.NumberColumn(format="%,d")})
    else:
        sm = pd.DataFrame()
        st.success("보수공법이 필요한 맨홀이 없습니다.")
    a, b = st.columns(2)
    a.markdown("**구조적 상태등급 분포**")
    a.bar_chart(res["구조적 상태등급"].round().astype(int).value_counts().sort_index())
    b.markdown("**운영적 상태등급 분포**")
    b.bar_chart(res["운영적 상태등급"].round().astype(int).value_counts().sort_index())
    st.markdown("**맨홀별 결과**")
    only = st.checkbox("보수공법이 있는 맨홀만", value=True, key="mh_only")
    view = res[res["보수공법"] != ""] if only else res
    st.dataframe(view, hide_index=True, use_container_width=True, height=360)
    unk = sorted({x for s in res["미등록 결함"] for x in s.split(", ") if x})
    if unk:
        st.warning("점수표에 없는 결함명(판정 제외): " + ", ".join(unk) + " — '입력 양식 받기'의 결함명 목록과 같은 이름으로 적어 주세요.")
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        sm.to_excel(w, sheet_name="1_공법별수량", index=False)
        res.to_excel(w, sheet_name="2_맨홀별결과", index=False)
        works.to_excel(w, sheet_name="3_맨홀별공법", index=False)
    st.download_button("📗 맨홀 보수 조서 (Excel)", bio.getvalue(), file_name=f"{project}_맨홀보수조서.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
