import streamlit as st

from ui.lle import render_lle_tab
from ui.vp import render_vp_tab
from ui.vle import render_vle_tab
from ui.conc import render_conc_tab
from ui.logic import render_logic_tab
from ui.timetable import render as render_timetable
from ui.reaction import render as render_reaction
from ui.heat_transfer import render as render_heat_transfer
from ui.conc_time import render as render_conc_time
from ui.filtration import render as render_filtration
from AI_agent.ui import render as render_ai_agent

st.set_page_config(page_title="バッチ製造支援ツール", layout="wide")


def _coming_soon(name: str):
    st.title(name)
    st.info("このページは現在準備中です。")


def _page_timetable():    render_timetable()
def _page_lle():          render_lle_tab()
def _page_vle():          render_vle_tab()
def _page_vp():           render_vp_tab()
def _page_conc():         render_conc_tab()
def _page_reaction():     render_reaction()
def _page_conc_time():    render_conc_time()
def _page_heat():         render_heat_transfer()
def _page_filter():       render_filtration()
def _page_logic():        render_logic_tab()
def _page_ai_agent():     render_ai_agent()

pg = st.navigation({
    "生産管理": [
        st.Page(_page_timetable, title="タイムテーブル作成"),
    ],
    "化学工学計算": [
        st.Page(_page_lle,       title="LLE線図"),
        st.Page(_page_vle,       title="VLE線図"),
        st.Page(_page_vp,        title="蒸気圧曲線"),
        st.Page(_page_conc,      title="濃縮シミュレーション"),
        st.Page(_page_reaction,  title="反応速度解析"),
        st.Page(_page_conc_time, title="濃縮時間推算"),
        st.Page(_page_heat,      title="伝熱計算"),
        st.Page(_page_filter,    title="ろ過時間推算"),
    ],
    "AI": [
        st.Page(_page_ai_agent,  title="AIエージェント"),
    ],
    "": [
        st.Page(_page_logic,     title="ロジック"),
    ],
})
pg.run()
