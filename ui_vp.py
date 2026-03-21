import streamlit as st
import plotly.graph_objects as go

from engine import calc_vapor_pressure_curve
from solvents import ALL_SOLVENTS, get_solvent_by_name


def render_vp_tab(tab):
    with tab:
        st.header("蒸気圧曲線")
        col_vp1, col_vp2 = st.columns([1, 3])
        with col_vp1:
            vp_name = st.selectbox("成分", [s["name"] for s in ALL_SOLVENTS], key="vp_name")
            vp_T_range = st.slider("温度範囲 (°C)", -50, 250, (0, 150), key="vp_T_range")
            vp_sol = get_solvent_by_name(vp_name, ALL_SOLVENTS)
            vp_tid = vp_sol.get("thermo_surrogate", vp_sol["thermo_id"])
            vp_offset = vp_sol.get("vp_T_offset", 0.0)

        with st.spinner("計算中..."):
            try:
                vp_data = calc_vapor_pressure_curve(vp_tid, vp_T_range[0], vp_T_range[1],
                                                    T_offset_K=vp_offset)
            except Exception as e:
                vp_data = None
                with col_vp2:
                    st.error(f"計算エラー: {e}")

        if vp_data:
            with col_vp2:
                # Antoine 有効範囲チェック
                t_vmin = vp_data.get("T_valid_min_C")
                t_vmax = vp_data.get("T_valid_max_C")
                if t_vmin is not None or t_vmax is not None:
                    out_of_range = []
                    if t_vmin is not None and vp_T_range[0] < t_vmin:
                        out_of_range.append(f"下限 {t_vmin:.0f} °C")
                    if t_vmax is not None and vp_T_range[1] > t_vmax:
                        out_of_range.append(f"上限 {t_vmax:.0f} °C")
                    if out_of_range:
                        valid_str = ""
                        if t_vmin is not None:
                            valid_str += f"{t_vmin:.0f}"
                        valid_str += " 〜 "
                        if t_vmax is not None:
                            valid_str += f"{t_vmax:.0f}"
                        st.warning(
                            f"表示範囲が蒸気圧相関式の有効範囲外です（有効範囲: {valid_str} °C）。"
                            "範囲外の値は外挿のため精度が低下します。"
                        )
                if vp_data["T_bp_C"] is not None:
                    st.info(f"沸点 = **{vp_data['T_bp_C']:.1f} °C** @ 101.325 kPa")
                else:
                    st.warning("沸点が指定温度範囲内にありません")
                fig_vp = go.Figure()
                fig_vp.add_trace(go.Scatter(
                    x=vp_data["T_C"], y=vp_data["P_kPa"],
                    name=vp_name, line=dict(color="royalblue", width=2),
                ))
                fig_vp.add_hline(y=101.325, line_dash="dash", line_color="red",
                                 annotation_text="101.325 kPa",
                                 annotation_position="bottom right")
                if vp_data["T_bp_C"] is not None:
                    fig_vp.add_vline(x=vp_data["T_bp_C"], line_dash="dot",
                                     line_color="orange",
                                     annotation_text=f"{vp_data['T_bp_C']:.1f}°C")
                fig_vp.update_layout(
                    xaxis_title="温度 (°C)", yaxis_title="蒸気圧 (kPa)",
                    title=f"{vp_name} 蒸気圧曲線",
                    width=480, height=480, plot_bgcolor="white",
                )
                st.plotly_chart(fig_vp, use_container_width=False)
