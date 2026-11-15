"""설계엔지니어용 VE 시뮬레이터 대시보드 (Streamlit).

실행:  streamlit run report_ui/app.py
"""
from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from cad_exporter import build_lisp, export_dxf  # noqa: E402
from common.config import load_config, with_criteria  # noqa: E402
from common.models import METHODS  # noqa: E402
from common.pipeline import run_pipes  # noqa: E402
from defect_processor.loader import build_pipes, read_tables  # noqa: E402
from report_ui import tables as T  # noqa: E402
from gis_link import apply_gis, export_geojson  # noqa: E402
from swmm_link import apply_swmm, parse_inp, parse_rpt  # noqa: E402
from report_ui.cost_statement import export_cost_statement  # noqa: E402
from report_ui.excel_report import build_workbook  # noqa: E402
from report_ui.sensitivity import sweep  # noqa: E402

SAMPLE = ROOT / "sample_data" / "sample_survey.xlsx"
PRICE_KEYS = {
    "cipp_full_per_m": "비굴착 전체보수(원/m)",
    "partial_repair_per_site": "부분보수 1.0m(원/개소)",
    "dredging_per_m": "준설(원/m)",
    "cleaning_per_m": "세정(원/m)",
    "bypass_per_span": "물돌리기(원/구간)",
    "pipe_install_per_m": "관부설(원/m)",
}
METHOD_COLORS = ["#d62728", "#c51bcb", "#ff7f0e", "#1f4fd6", "#2ca02c", "#17becf", "#9e9e9e"]

st.set_page_config(page_title="물꼬 · 노후하수관로 정비 통합 설계 플랫폼", layout="wide", page_icon="🛠️")


def _app_password() -> str:
    """배포 시 Streamlit secrets(APP_PASSWORD)에 설정한 접속 비밀번호. 로컬 실행(secrets 없음)은 빈 문자열."""
    try:
        return str(st.secrets.get("APP_PASSWORD", "") or "")
    except Exception:  # secrets.toml 없음
        return ""


def _gate() -> None:
    import hmac
    pw = _app_password()
    if not pw or st.session_state.get("_auth_ok"):
        return
    st.title("물꼬 — 노후하수관로 정비 통합 설계 플랫폼")
    typed = st.text_input("접속 비밀번호", type="password")
    if typed and hmac.compare_digest(typed.encode(), pw.encode()):
        st.session_state["_auth_ok"] = True
        st.rerun()
    if typed:
        st.error("비밀번호가 맞지 않습니다.")
    st.stop()


_gate()


@st.cache_data(show_spinner=False)
def _load(file_bytes: bytes, name: str):
    cfg = load_config()
    suffix = Path(name).suffix or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(file_bytes)
        tmp = f.name
    p_df, d_df = read_tables(tmp, cfg)
    Path(tmp).unlink(missing_ok=True)
    return p_df, d_df


def _price_frame(cfg: dict) -> pd.DataFrame:
    up = cfg["unit_prices"]
    dias = sorted(int(k) for k in up["cipp_full_per_m"])
    return pd.DataFrame({"관경(mm)": dias, **{lbl: [up[k][str(d)] for d in dias] for k, lbl in PRICE_KEYS.items()}})


def _apply_prices(cfg: dict, df: pd.DataFrame) -> dict:
    cfg = copy.deepcopy(cfg)
    for k, lbl in PRICE_KEYS.items():
        cfg["unit_prices"][k] = {str(int(r["관경(mm)"])): float(r[lbl]) for _, r in df.iterrows()}
    return cfg


def _won(x: float) -> str:
    return f"{x / 1e8:,.2f}억원" if abs(x) >= 1e8 else f"{x / 1e4:,.0f}만원"


# ------------------------------------------------------------------ 사이드바
base_cfg = load_config()
st.sidebar.header("📂 조사 데이터")
up_file = st.sidebar.file_uploader("nPASS / PipeAsset 조서 (.xlsx)", type=["xlsx", "xls"])
if up_file is not None:
    data_bytes, data_name = up_file.getvalue(), up_file.name
elif SAMPLE.exists():
    data_bytes, data_name = SAMPLE.read_bytes(), SAMPLE.name
    st.sidebar.caption("샘플 데이터 사용 중 (sample_data/sample_survey.xlsx)")
else:
    st.error("샘플 데이터가 없습니다. `python main.py sample`을 먼저 실행하거나 파일을 업로드하세요.")
    st.stop()
with st.sidebar.expander("🌊 SWMM 연동 (선택)"):
    inp_file = st.file_uploader("SWMM 모델 (.inp)", type=["inp"])
    rpt_file = st.file_uploader("SWMM 결과 (.rpt)", type=["rpt"])
swmm_model, swmm_results = None, None
if inp_file is not None:
    with tempfile.TemporaryDirectory() as _td:
        (Path(_td) / "m.inp").write_bytes(inp_file.getvalue())
        swmm_model = parse_inp(Path(_td) / "m.inp")
        if rpt_file is not None:
            (Path(_td) / "m.rpt").write_bytes(rpt_file.getvalue())
            swmm_results = parse_rpt(Path(_td) / "m.rpt")
gis_state: dict = {"lines": None, "points": None, "crs": None, "source_crs": None, "use_length": False}
with st.sidebar.expander("🗺️ GIS 연동 (선택)"):
    st.caption("관로선(.geojson/.zip[shp])과 맨홀점(.zip[shp]/.geojson)을 올리면 좌표·선형이 결합됩니다.")
    gl_file = st.file_uploader("GIS 관로선", type=["geojson", "json", "zip"], key="gis_lines")
    gp_file = st.file_uploader("GIS 맨홀점", type=["geojson", "json", "zip"], key="gis_points")
    gis_state["source_crs"] = st.text_input("입력 좌표계(비워두면 자동)", "", placeholder="EPSG:5186") or None
    gis_state["use_length"] = st.checkbox("GIS 선형 길이를 도면연장으로 사용", value=False)
    _gdir = Path(tempfile.mkdtemp(prefix="gis_up_"))
    for key, f in (("lines", gl_file), ("points", gp_file)):
        if f is not None:
            (_gdir / f.name).write_bytes(f.getvalue())
            gis_state[key] = _gdir / f.name
project = st.sidebar.text_input("사업명", "노후하수관로 정비사업")

st.sidebar.header("📘 조사·평가 매뉴얼")
manual_sel = st.sidebar.radio(
    "기본 적용 매뉴얼", ["2017", "2025", "2015"], horizontal=True,
    help="조서에 '적용매뉴얼' 열이 있으면 관로별로 그 값이 우선합니다(2015·2017 혼용 사업 등).")
base_cfg = load_config(manual=manual_sel)
st.sidebar.caption(base_cfg["manual_title"])

st.sidebar.header("⚙️ 판정기준 (VE 파라미터)")
c0 = base_cfg["criteria"]
merge_gap = st.sidebar.slider("부분보수 병합 이격거리 (m)", 0.0, 3.0, float(c0["merge_gap_m"]), 0.1)
rate_th = st.sidebar.slider("전체보수 전환 불량률 기준 (%)", 5.0, 60.0, float(c0["defect_rate_threshold_pct"]), 1.0)
breakeven = st.sidebar.slider("부분/전체보수 손익분기 비율 (%)", 40, 100, int(float(c0["breakeven_ratio"]) * 100), 1) / 100
max_dig = st.sidebar.slider("부분굴착 허용 개소수", 0, 5, int(c0["max_partial_excavations"]))
rate_method = st.sidebar.radio("불량률 산정", ["joint_count", "length"],
                               format_func=lambda x: "관 본수 기준" if x == "joint_count" else "보수연장 기준",
                               index=0 if c0["defect_rate_method"] == "joint_count" else 1, horizontal=True)
overhead = st.sidebar.slider("제경비율 (%)", 0, 80, int(float(c0.get("overhead_ratio", 0)) * 100), 1) / 100

with st.sidebar.expander("💰 관경별 단가 테이블 편집"):
    _src = (base_cfg.get("price_source") or {}).get("title")
    st.caption(f"기본 단가 출처: 「{_src}」 설계내역서 실적 환산" if _src else "기본 단가: 개략 검토용 예시값")
    price_df = st.data_editor(_price_frame(base_cfg), hide_index=True, width="stretch", key="prices")

cfg = _apply_prices(base_cfg, price_df)
cfg = with_criteria(cfg, merge_gap_m=merge_gap, defect_rate_threshold_pct=rate_th, breakeven_ratio=breakeven,
                    max_partial_excavations=max_dig, defect_rate_method=rate_method, overhead_ratio=overhead)

# ------------------------------------------------------------------ 계산
try:
    p_df, d_df = _load(data_bytes, data_name)
except Exception as e:  # 사용자 파일 오류를 화면에 표시
    st.error(f"조서 파일을 읽지 못했습니다: {e}")
    st.stop()
def _build(c: dict):
    """조서 관로 생성 + (선택) SWMM 모델·결과 결합."""
    ps, warns = build_pipes(p_df, d_df, c)
    if swmm_model is not None:
        warns = warns + apply_swmm(ps, swmm_model, swmm_results, c)
    if gis_state["lines"] or gis_state["points"]:
        gw, gis_state["crs"] = apply_gis(ps, c, gis_state["lines"], gis_state["points"], gis_state["source_crs"], None,
                                         True if gis_state["use_length"] else None)
        warns = warns + gw
    return ps, warns


pipes, load_warn = _build(cfg)
res = run_pipes(pipes, cfg, load_warn)
res.crs = gis_state["crs"] or (cfg["gis"]["default_crs"] if any(p.has_coords for p in pipes) else None)
base_pipes, _ = _build(base_cfg)
base_res = run_pipes(base_pipes, base_cfg)

# ------------------------------------------------------------------ KPI
st.title("🛠️ 물꼬 — 노후하수관로 정비 통합 설계 플랫폼")
st.caption("정밀조사 판독부터 공법 판정·수량·공사비·도면까지 — CCTV 결함 병합 · 굴착/비굴착 VE 판정 · 수량/개략공사비 · CAD 주석 자동 생성")


def _len(r, ms):
    return sum(p.plan_length_m for p in r.pipes if r.decisions[p.pipe_id].method in ms)


k = st.columns(5)
_dcost = res.total_cost - base_res.total_cost
k[0].metric("총 개략공사비", _won(res.total_cost),
            (_won(_dcost) + " (기본기준 대비)") if abs(_dcost) >= 1 else None, delta_color="inverse")
k[1].metric("비굴착 전체보수", f"{_len(res, ['비굴착 전체보수']):,.1f} m",
            f"{_len(res, ['비굴착 전체보수']) - _len(base_res, ['비굴착 전체보수']):+,.1f} m")
n_patch = sum(d.n_patch_sites for d in res.decisions.values())
k[2].metric("부분보수", f"{n_patch} 개소", f"{n_patch - sum(d.n_patch_sites for d in base_res.decisions.values()):+d}")
k[3].metric("굴착교체", f"{_len(res, ['굴착교체(증경)', '굴착교체(동경)']):,.1f} m")
k[4].metric("검토 경고", f"{len(res.warnings)} 건")

tabs = st.tabs(["📊 총괄", "📋 관로별 조서", "🔎 관로 상세", "🧩 병합내역", "📐 수량·공사비", "📈 민감도", "⚠️ 경고", "⬇️ 출력", "🕳️ 맨홀"])

with tabs[0]:
    summary = T.summary_by_method(res)
    c1, c2 = st.columns([3, 2])
    c1.dataframe(summary.style.format({"공사비(원)": "{:,.0f}", "m당 평균(원/m)": "{:,.0f}", "연장(m)": "{:,.1f}"}),
                 hide_index=True, width="stretch")
    chart_df = summary[summary["정비공법"] != "합계"]
    c2.altair_chart(
        alt.Chart(chart_df).mark_arc(innerRadius=35).encode(
            theta="연장(m):Q",
            color=alt.Color("정비공법:N", scale=alt.Scale(domain=METHODS, range=METHOD_COLORS),
                            legend=alt.Legend(orient="bottom", columns=2)),
            tooltip=["정비공법", "관로수", "연장(m)", alt.Tooltip("공사비(원):Q", format=",")],
        ).properties(title="공법별 연장 구성", height=380), width="stretch")
    st.subheader("관경별·공법별 연장(m)")
    st.dataframe(T.length_by_diameter(res), hide_index=True, width="stretch")
    cap_df = T.capacity_comparison(res)
    if not cap_df.empty:
        with st.expander("🌊 정비 전·후 통수능 비교 (Manning 개략)"):
            st.dataframe(cap_df, hide_index=True, width="stretch")

with tabs[1]:
    detail = T.pipe_detail(res)
    sel = st.multiselect("공법 필터", METHODS, default=[])
    view = detail[detail["정비공법"].isin(sel)] if sel else detail
    st.dataframe(view, hide_index=True, width="stretch", height=520,
                 column_config={"선정사유": st.column_config.TextColumn(width="large")})

with tabs[2]:
    pid = st.selectbox("관번", [p.pipe_id for p in res.pipes])
    p = next(x for x in res.pipes if x.pipe_id == pid)
    dec = res.decisions[pid]
    st.markdown(f"**{p.pipe_id}** ({p.from_mh} → {p.to_mh}) · {p.material} D{p.diameter_mm} · "
                f"도면 {p.plan_length_m:.1f}m / 촬영 {p.cctv_length_m:.1f}m (보정계수 {p.scale_factor:.3f}) · "
                f"심도 {p.avg_depth_m:.2f}m")
    st.info(f"**{dec.method}** — {dec.detail}\n\n{dec.reason}")
    segs = [{"구분": c.kind, "시작": c.repair_start_m, "끝": c.repair_end_m, "라벨": f"{c.kind}#{c.cluster_no}"}
            for c in (dec.patch_clusters or dec.evaluated_clusters) + dec.dig_clusters]
    dfd = T.defect_detail(res)
    dfd = dfd[dfd["관번"] == pid]
    base = alt.Chart(pd.DataFrame({"x": [0, p.plan_length_m]})).mark_rule(strokeWidth=6, color="#888").encode(
        x=alt.X("x:Q", title="도면상 위치(m)", scale=alt.Scale(domain=[0, p.plan_length_m])))
    layers = [base]
    if segs:
        layers.append(alt.Chart(pd.DataFrame(segs)).mark_rect(opacity=0.35, height=40).encode(
            x="시작:Q", x2="끝:Q", color=alt.Color("구분:N", scale=alt.Scale(domain=["부분보수", "부분굴착"],
                                                                          range=["#2ca02c", "#ff7f0e"])),
            tooltip=["라벨", "시작", "끝"]))
    if not dfd.empty:
        layers.append(alt.Chart(dfd).mark_point(size=160, filled=True).encode(
            x="보정위치(m):Q", shape="구분:N",
            color=alt.Color("비굴착불가:N", scale=alt.Scale(domain=["Y", ""], range=["#d62728", "#1f4fd6"]),
                            legend=alt.Legend(title="시공불가")),
            tooltip=["결함명", "결함코드", "등급", "보정위치(m)", "조서거리(m)", "적용공법"]))
    st.altair_chart(alt.layer(*layers).properties(height=140), width="stretch")
    st.dataframe(dfd, hide_index=True, width="stretch")

with tabs[3]:
    merge = T.merge_detail(res)
    saved = pd.to_numeric(merge.get("병합 절감액(원)"), errors="coerce").fillna(0).sum() if not merge.empty else 0
    st.metric("근접결함 병합에 의한 부분보수 절감액", _won(saved))
    st.dataframe(merge, hide_index=True, width="stretch", height=480)

with tabs[4]:
    q_sum, q_detail = T.quantity_tables(res)
    st.subheader("공종별 수량 및 개략공사비")
    st.dataframe(q_sum.style.format({"금액(원)": "{:,.0f}", "평균단가(원)": "{:,.0f}", "수량": "{:,.2f}"}),
                 hide_index=True, width="stretch")
    with st.expander("관로별 수량 상세"):
        st.dataframe(q_detail, hide_index=True, width="stretch")

with tabs[5]:
    st.caption("현재 사이드바 기준을 바탕으로 한 가지 파라미터만 변화시켰을 때의 결과")
    param = st.selectbox("분석 파라미터", ["breakeven_ratio", "defect_rate_threshold_pct", "merge_gap_m"],
                         format_func={"breakeven_ratio": "손익분기 비율", "defect_rate_threshold_pct": "불량률 기준(%)",
                                      "merge_gap_m": "병합 이격거리(m)"}.get)
    rng = {"breakeven_ratio": [round(0.4 + 0.05 * i, 2) for i in range(13)],
           "defect_rate_threshold_pct": list(range(10, 55, 5)),
           "merge_gap_m": [round(0.3 * i, 1) for i in range(11)]}[param]
    sdf = sweep(_build(cfg)[0], cfg, param, rng)
    long = sdf.melt(id_vars=[param, "총공사비(억원)"], var_name="공법", value_name="연장(m)")
    long["공법"] = long["공법"].str.replace("(m)", "", regex=False)
    cA, cB = st.columns(2)
    cA.altair_chart(alt.Chart(sdf).mark_line(point=True).encode(
        x=alt.X(f"{param}:Q"), y=alt.Y("총공사비(억원):Q", scale=alt.Scale(zero=False)),
        tooltip=list(sdf.columns[:2])).properties(title="총공사비 변화", height=320), width="stretch")
    cB.altair_chart(alt.Chart(long).mark_area().encode(
        x=f"{param}:Q", y="연장(m):Q",
        color=alt.Color("공법:N", scale=alt.Scale(domain=METHODS, range=METHOD_COLORS))
    ).properties(title="공법별 연장 구성 변화", height=320), width="stretch")
    st.dataframe(sdf, hide_index=True, width="stretch")

with tabs[6]:
    if res.warnings:
        for w in res.warnings:
            st.warning(w)
    else:
        st.success("검토 경고 없음")

with tabs[7]:
    from report_ui.quantity_book import build_quantity_book
    st.download_button("📒 실무 양식 수량산출서 (Excel, 수식 연동)",
                       build_quantity_book(res, project, st.session_state.get("mh_works")),
                       file_name=f"{project}_수량산출서.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       help="수량총괄표·비굴착/굴착 집계표·단면 산출근거·관로별 산출서(+맨홀 탭 결과가 있으면 맨홀보수 집계표)")
    from report_ui.cost_statement import total_cost as _total_cost
    from report_ui.design_report import build_design_report
    st.download_button("📝 설계보고서 초안 (Word)",
                       build_design_report(res, project, st.session_state.get("mh_result"), st.session_state.get("mh_works"),
                                           _total_cost(res)),
                       file_name=f"{project}_설계보고서초안.docx",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       help="과업 개요·조사 결과·판정 기준·공법 선정 결과·관로별 선정 사유·수량/공사비·(맨홀)·검토 의견")
    st.caption("맨홀 탭에서 맨홀 자료를 불러오면 수량산출서와 보고서 초안에 맨홀 정비계획이 함께 들어갑니다.")
    st.download_button("📗 실시설계 총괄조서 (Excel)", build_workbook(res, project),
                       file_name=f"{project}_정비총괄조서.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with tempfile.TemporaryDirectory() as td:
        cost_path = export_cost_statement(res, Path(td) / "cost.xlsx", project)
        st.download_button("🧾 공사비 내역서·원가계산서 (Excel, 수식 연동)", cost_path.read_bytes(),
                           file_name=f"{project}_공사비내역서.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if any(p.has_coords for p in res.pipes):
            gj_path, gj_warn = export_geojson(res, Path(td) / "gis.geojson")
            st.download_button("🗺️ GIS 결과 (GeoJSON, WGS84 · 관로/결함/맨홀)", gj_path.read_bytes(),
                               file_name=f"{project}_GIS.geojson", mime="application/geo+json")
            for w in gj_warn:
                st.caption(w)
        dxf_path = export_dxf(res, Path(td) / "defects.dxf")
        st.download_button("📐 평면·종단 결함도면 (DXF)", dxf_path.read_bytes(), file_name=f"{project}_결함도면.dxf",
                           mime="application/dxf")
    enc = st.radio("LSP 인코딩", ["cp949", "utf-8"], horizontal=True,
                   help="AutoCAD 2020 이하/LISPSYS=0: cp949, AutoCAD 2021+ LISPSYS=1: utf-8")
    st.download_button("🧩 AutoLISP 주석 스크립트 (.lsp)",
                       build_lisp(res).replace("\n", "\r\n").encode(enc, errors="replace"),
                       file_name="gp_defect_annot.lsp", mime="text/plain")

with tabs[8]:
    from report_ui.manhole_tab import render as render_manhole
    render_manhole(project)
