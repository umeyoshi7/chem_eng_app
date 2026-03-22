import streamlit as st

from ui_lle import render_lle_tab
from ui_vp import render_vp_tab
from ui_vle import render_vle_tab
from ui_conc import render_conc_tab
from ui_logic import render_logic_tab
from ui_timetable import render as render_timetable_tab

st.set_page_config(page_title="バッチ製造支援ツール", layout="wide")
st.title("バッチ製造支援ツール")

tab_timetable, tab_vp, tab_lle, tab_vle, tab_conc, tab_logic = st.tabs([
    "タイムテーブル作成", "蒸気圧曲線", "LLE線図", "VLE線図", "濃縮シミュレーション", "ロジック"
])

render_timetable_tab(tab_timetable)
render_vp_tab(tab_vp)
render_lle_tab(tab_lle)
render_vle_tab(tab_vle)
render_conc_tab(tab_conc)
render_logic_tab(tab_logic)
