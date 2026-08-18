# -*- coding: utf-8 -*-
"""네이버 파워링크 노출순위 — Streamlit 화면.

요구사항 문서의 표를 그대로 낸다:  날짜 | 키워드 | MO - 판매
미노출은 문서 표기를 따라 **0위** 로 적는다.

수집·분석 엔진(app/collector.py, app/analytics.py)은 화면과 무관한 라이브러리라
그대로 재사용한다. 이 파일은 화면만 담당한다.

실행:  streamlit run streamlit_app.py
"""
from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
import streamlit as st

from app import analytics, archive, config as config_mod
from app.collector import PowerLinkCollector, Target, measure

RANK_COL = "MO - 판매"     # 문서의 컬럼명
OUT_RANK = 0               # 문서 표기: 미노출은 0위

st.set_page_config(
    page_title="파워링크 노출순위",
    page_icon="📊",
    layout="wide",
    # expanded 로 고정하면 폰에서 사이드바가 화면을 덮어 본문이 안 보인다.
    # auto 는 넓은 화면에서만 펼치고 폰에서는 접어 둔다.
    initial_sidebar_state="auto",
)


# ───────────────────────────────────────────────────────────────── 설정 로드
@st.cache_resource
def _cfg():
    return config_mod.load()


def rank_text(rank: int | None) -> str:
    """문서 표기법. 미노출은 '0위'."""
    return f"{OUT_RANK}위" if rank is None else f"{rank} 위"


def to_table(measured, primary: str, when: datetime) -> pd.DataFrame:
    """요구사항 문서와 같은 3열 표."""
    day = when.strftime("%Y-%m-%d")
    rows = []
    for m in measured:
        tr = next((t for t in m.ranks if t.name == primary), None)
        rows.append({
            "날짜": day,
            "키워드": m.keyword,
            RANK_COL: rank_text(tr.rank if tr else None),
            "_순위": (10**6 if (not tr or tr.rank is None) else tr.rank),
            "_전체광고": m.total_ads,
            "_변동": "변동" if (tr and tr.unstable) else "",
            "_오류": m.error or "",
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────── 사이드바
cfg = _cfg()
st.sidebar.header("설정")

target_name = st.sidebar.text_input("기준 업체", value=cfg.targets[0].name if cfg.targets else "헤이딜러")
target_pats = st.sidebar.text_input(
    "매칭어 (콤마 구분)",
    value=", ".join(cfg.targets[0].patterns) if cfg.targets else "헤이딜러, heydealer",
    help="광고의 업체명·도메인에 이 말이 들어 있으면 우리 광고로 봅니다. "
         "느슨하면 남의 광고를 잡습니다(예: '다이렉트카' → '국민다이렉트카').",
)
repeat = st.sidebar.slider(
    "반복 측정 횟수", 1, 5, cfg.repeat,
    help="파워링크는 호출할 때마다 광고가 로테이션됩니다. 여러 번 재서 중앙값을 씁니다.",
)
max_pages = st.sidebar.slider(
    "최대 조회 페이지", 3, 20, cfg.max_pages,
    help=f"1페이지당 15위. {cfg.max_pages}이면 {cfg.max_pages * 15}위까지 확인하고, "
         "그보다 뒤면 0위(미노출)로 적습니다.",
)
workers = st.sidebar.slider(
    "동시 요청 수", 1, 10, cfg.max_workers,
    help="과하게 올리면 네이버 쪽에서 차단될 수 있습니다. 5 권장.",
)
save_history = st.sidebar.checkbox("결과를 이력에 저장", value=True)


# ─────────────────────────────────────────────────────────────────── 본문
st.title("네이버 파워링크 노출순위")
st.caption(
    f"모바일 파워링크(m.ad.search.naver.com) 기준 · 1페이지 15개 · "
    f"기준 업체 **{target_name}** · "
    "**버튼을 누른 그 시각**의 순위를 즉시 조회합니다"
)

tab_run, tab_hist = st.tabs(["순위 조회", "추이"])

with tab_run:
    kw_text = st.text_area(
        "키워드",
        value="\n".join(cfg.keywords),
        height=200,
        help="한 줄에 하나씩. 콤마·탭으로 구분해도 되고, 엑셀에서 셀을 복사해 붙여넣어도 됩니다.",
    )
    keywords = config_mod.parse_keywords(kw_text)
    st.caption(f"{len(keywords)}개 키워드")

    if st.button("순위 조회", type="primary", disabled=not keywords):
        targets = [Target(target_name.strip() or "기준업체",
                          [p.strip() for p in target_pats.split(",") if p.strip()])]
        collector = PowerLinkCollector(max_pages=max_pages, max_workers=workers)

        bar = st.progress(0.0, text="시작합니다…")
        started = datetime.now()

        def on_progress(frac, kw):
            bar.progress(min(1.0, frac), text=f"{kw} … {int(frac * 100)}%")

        measured = measure(collector, keywords, targets, repeat=repeat, on_progress=on_progress)
        bar.empty()

        df = to_table(measured, targets[0].name, started)
        st.session_state["result"] = df
        st.session_state["ran_at"] = started
        st.session_state["elapsed"] = (datetime.now() - started).total_seconds()

        if save_history:
            archive.append(config_mod.HISTORY_PATH,
                           archive.to_rows(measured, source="streamlit"))

    df = st.session_state.get("result")
    if df is not None:
        ran_at = st.session_state.get("ran_at", datetime.now())
        age_min = (datetime.now() - ran_at).total_seconds() / 60

        found = int((df["_순위"] < 10**6).sum())
        first = int((df["_순위"] <= 15).sum())
        c1, c2, c3, c4 = st.columns(4)
        # 파워링크는 시간대마다 순위가 바뀐다. '언제 잰 값인지'가 순위만큼 중요하다.
        c1.metric("조회 시각", ran_at.strftime("%H:%M"), help=ran_at.strftime("%Y-%m-%d %H:%M:%S"))
        c2.metric("노출", f"{found} / {len(df)}")
        c3.metric("첫 페이지(15위 내)", first)
        c4.metric("걸린 시간", f"{st.session_state.get('elapsed', 0):.0f}초")

        # 열어 둔 채 시간이 지나면 지금 순위가 아니다. 오래된 표를 현재로 착각하지 않게.
        if age_min >= 30:
            st.warning(
                f"이 표는 **{ran_at.strftime('%H:%M')}** 에 잰 값입니다 "
                f"({int(age_min)}분 전). 파워링크는 시간대마다 순위가 바뀌니, "
                "지금 순위가 필요하면 **순위 조회**를 다시 누르세요."
            )

        errs = df[df["_오류"] != ""]
        if len(errs):
            st.warning(f"{len(errs)}개 키워드에서 수집 오류가 있었습니다: "
                       + ", ".join(errs["키워드"].tolist()[:5]))

        sort_by = st.radio("정렬", ["문서 순서", "순위 좋은 순"], horizontal=True)
        view = df.sort_values("_순위") if sort_by == "순위 좋은 순" else df

        st.dataframe(
            view[["날짜", "키워드", RANK_COL]],
            hide_index=True,
            width="stretch",
            height=min(760, 40 + 35 * len(view)),
        )
        st.caption(
            f"**{ran_at.strftime('%Y-%m-%d %H:%M')} 시점의 순위**입니다. "
            "**0위 = 미노출**(설정한 페이지 범위 안에서 못 찾음). "
            "16위부터는 사용자가 '더보기'를 눌러야 보이는 자리입니다."
        )

        with_time = st.checkbox(
            "내려받기에 조회 시각 넣기", value=False,
            help="문서 양식은 날짜만 씁니다. 하루에 여러 번 재서 시간대별로 비교할 때만 켜세요.",
        )
        stamp = ran_at.strftime("%Y%m%d_%H%M")
        out = view[["날짜", "키워드", RANK_COL]].copy()
        if with_time:
            out.insert(1, "시각", ran_at.strftime("%H:%M"))
        d1, d2 = st.columns(2)
        # 엑셀이 UTF-8 CSV 를 CP949 로 읽어 한글을 깨뜨린다. BOM 을 붙여야 한다.
        d1.download_button(
            "CSV 내려받기", out.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"파워링크순위_{stamp}.csv", mime="text/csv",
        )
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            out.to_excel(w, sheet_name="노출순위", index=False)
        d2.download_button(
            "엑셀 내려받기", buf.getvalue(),
            file_name=f"파워링크순위_{stamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.info("키워드를 확인하고 **순위 조회**를 누르세요. "
                f"{len(keywords)}개 키워드는 1~2분쯤 걸립니다.")

with tab_hist:
    hours = st.selectbox("기간", [24, 72, 168, 720],
                         format_func=lambda h: f"최근 {h//24}일" if h >= 24 else f"최근 {h}시간",
                         index=1)
    rows = archive.load(config_mod.HISTORY_PATH, hours=hours)
    rows = [r for r in rows if r.get("target") == target_name]
    if not rows:
        st.info("아직 쌓인 이력이 없습니다. 조회를 한 번 이상 하면 여기에 추이가 그려집니다.")
    else:
        data = analytics.build(rows, [target_name])
        k = data.get("kpi") or {}
        c1, c2, c3 = st.columns(3)
        c1.metric("평균 순위", k.get("avg_rank") or "—")
        c2.metric("첫 페이지 노출", f"{k.get('first_page', 0)} / {k.get('keywords', 0)}")
        c3.metric("측정 건수", data.get("sample_count", 0))

        pick = st.selectbox("키워드", sorted(data.get("trend", {}).keys()))
        series = (data.get("trend", {}).get(pick) or {}).get(target_name) or []
        if len(series) < 2:
            st.info("이 키워드는 수집 시점이 한 번뿐이라 추이를 그릴 수 없습니다.")
        else:
            tdf = pd.DataFrame(
                [{"시각": pd.to_datetime(t), "순위": (r if r is not None else None)}
                 for t, r in series]
            ).set_index("시각")
            # 순위는 작을수록 좋다. 축을 뒤집어 위가 상위가 되게 한다.
            st.line_chart(tdf, y="순위", height=320)
            st.caption("Y축은 값이 작을수록 상위입니다. 빈 구간은 미노출입니다.")

        st.dataframe(
            pd.DataFrame([
                {"키워드": c["keyword"],
                 RANK_COL: rank_text(c["ranks"][target_name]["rank"]),
                 "직전 대비": c["ranks"][target_name]["delta"]}
                for c in data.get("current", []) if target_name in c["ranks"]
            ]),
            hide_index=True, width="stretch",
        )
