import streamlit as st
import plotly.graph_objects as go

from engine import calc_vle_xy
from solvents import ALL_SOLVENTS, get_solvent_by_name


def render_vle_tab(tab):
    with tab:
        st.header("VLE線図（2成分系）")
        col_v1, col_v2, col_v3 = st.columns(3)
        with col_v1:
            vle_s1_name = st.selectbox("成分 1", [s["name"] for s in ALL_SOLVENTS],
                                        key="vle_s1")
        with col_v2:
            vle_s2_opts = [s["name"] for s in ALL_SOLVENTS if s["name"] != vle_s1_name]
            vle_s2_name = st.selectbox("成分 2", vle_s2_opts, key="vle_s2")
        with col_v3:
            vle_P = st.number_input("圧力 (kPa)", min_value=1.0, value=101.325,
                                     step=1.0, format="%.3f", key="vle_P")

        run_vle = st.button("計算実行", key="run_vle", type="primary")

        if run_vle:
            vle_sol1 = get_solvent_by_name(vle_s1_name, ALL_SOLVENTS)
            vle_sol2 = get_solvent_by_name(vle_s2_name, ALL_SOLVENTS)
            with st.spinner("VLE計算中（初回はしばらくかかります）..."):
                try:
                    vle_res = calc_vle_xy([vle_sol1, vle_sol2], vle_P)
                    st.session_state["vle_res"] = vle_res
                    st.session_state["vle_s1_saved"] = vle_s1_name
                    st.session_state["vle_s2_saved"] = vle_s2_name
                    st.session_state["vle_P_saved"] = vle_P
                except Exception as e:
                    st.error(f"計算エラー: {e}")

        if "vle_res" in st.session_state:
            vle_res = st.session_state["vle_res"]
            s1d = st.session_state.get("vle_s1_saved", vle_s1_name)
            s2d = st.session_state.get("vle_s2_saved", vle_s2_name)
            Pd = st.session_state.get("vle_P_saved", vle_P)
            st.caption(f"計算結果: {s1d} – {s2d} @ {Pd:.3f} kPa")

            col_xy, col_txy = st.columns(2)
            with col_xy:
                fig_xy = go.Figure()
                fig_xy.add_trace(go.Scatter(
                    x=[0, 1], y=[0, 1], mode="lines",
                    line=dict(color="gray", dash="dash"),
                    showlegend=False, hoverinfo="skip",
                ))
                pts = [(x, y) for x, y in zip(vle_res["x1"], vle_res["y1"])
                       if y is not None]
                if pts:
                    xs, ys = zip(*pts)
                    fig_xy.add_trace(go.Scatter(
                        x=list(xs), y=list(ys), name="VLE",
                        line=dict(color="royalblue", width=2),
                    ))
                fig_xy.update_layout(
                    title=f"xy線図 @ {Pd:.3f} kPa",
                    xaxis=dict(
                        title=f"x₁ ({s1d})", range=[0, 1],
                        scaleanchor="y", scaleratio=1,
                        constrain="domain",
                    ),
                    yaxis=dict(title=f"y₁ ({s1d})", range=[0, 1], constrain="domain"),
                    height=480, plot_bgcolor="white",
                )
                st.plotly_chart(fig_xy, use_container_width=True)

            with col_txy:
                fig_txy = go.Figure()
                pts_b = [(x, T) for x, T in zip(vle_res["x1"], vle_res["T_bubble_C"])
                         if T is not None]
                pts_d = [(x, T) for x, T in zip(vle_res["x1"], vle_res["T_dew_C"])
                         if T is not None]
                if pts_b:
                    xs, Ts = zip(*pts_b)
                    fig_txy.add_trace(go.Scatter(
                        x=list(xs), y=list(Ts), name="沸点",
                        line=dict(color="blue", width=2),
                    ))
                if pts_d:
                    xs, Ts = zip(*pts_d)
                    fig_txy.add_trace(go.Scatter(
                        x=list(xs), y=list(Ts), name="露点",
                        line=dict(color="orange", width=2, dash="dash"),
                    ))
                three_phase = vle_res.get("three_phase")
                if three_phase is not None:
                    T3 = three_phase["T3_C"]
                    fig_txy.add_trace(go.Scatter(
                        x=[0.0, 1.0], y=[T3, T3], mode="lines",
                        name=f"三相温度 {T3:.1f}°C",
                        line=dict(color="green", width=1.5, dash="dot"),
                    ))
                    fig_txy.add_trace(go.Scatter(
                        x=[three_phase["x_alpha"], three_phase["x_beta"]],
                        y=[T3, T3], mode="markers",
                        name="α/β端点",
                        marker=dict(color="green", size=8, symbol="circle"),
                    ))
                    if three_phase["y3"] is not None:
                        fig_txy.add_trace(go.Scatter(
                            x=[three_phase["y3"]], y=[T3], mode="markers",
                            name=f"y₃={three_phase['y3']:.3f}（異質共沸）",
                            marker=dict(color="red", size=10, symbol="diamond"),
                        ))
                fig_txy.update_layout(
                    title=f"T-xy線図 @ {Pd:.3f} kPa",
                    xaxis=dict(title=f"モル分率 ({s1d})", range=[0, 1]),
                    yaxis_title="温度 (°C)",
                    width=480, height=480, plot_bgcolor="white",
                )
                st.plotly_chart(fig_txy, use_container_width=False)
