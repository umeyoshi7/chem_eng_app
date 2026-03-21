import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from engine import calc_rayleigh_distillation, density_water, density_solvent
from solvents import ALL_SOLVENTS, get_solvent_by_name


def render_conc_tab(tab):
    with tab:
        st.header("濃縮シミュレーション（レイリー蒸留）")
        conc_n = st.radio("成分数", [2, 3, 4], horizontal=True, key="conc_n")
        conc_unit = st.radio("単位", ["mol", "g", "mL"], horizontal=True, key="conc_unit")

        cols_conc = st.columns(conc_n)
        conc_names, conc_amts = [], []
        for ci in range(conc_n):
            with cols_conc[ci]:
                sel = st.selectbox(f"成分 {ci + 1}", [s["name"] for s in ALL_SOLVENTS],
                                   key=f"conc_sel_{ci}")
                conc_names.append(sel)
                amt = st.number_input(f"量 ({conc_unit})", min_value=0.0, value=1.0,
                                       step=0.1, format="%.3f", key=f"conc_amt_{ci}")
                conc_amts.append(amt)

        col_cP, col_cT = st.columns(2)
        with col_cP:
            conc_P = st.number_input("圧力 (kPa)", min_value=1.0, value=101.325,
                                       step=1.0, format="%.3f", key="conc_P")
        with col_cT:
            conc_T_ref = st.number_input("仕込み温度 (°C) ※mL換算用",
                                          min_value=-50.0, max_value=200.0, value=25.0,
                                          step=1.0, format="%.1f", key="conc_T_ref")
        run_conc = st.button("計算実行", key="run_conc", type="primary")

        if run_conc:
            if len(set(conc_names)) < len(conc_names):
                st.error("同一成分が複数選択されています。異なる成分を選択してください。")
            else:
                conc_sol_dicts = [get_solvent_by_name(nm, ALL_SOLVENTS) for nm in conc_names]
                moles = []
                for s, amt in zip(conc_sol_dicts, conc_amts):
                    if conc_unit == "mol":
                        moles.append(amt)
                    elif conc_unit == "g":
                        moles.append(amt / s["mw"])
                    else:  # mL
                        rho = density_water(conc_T_ref) if s["name"] == "Water" else density_solvent(s, conc_T_ref)
                        moles.append(amt * rho / s["mw"])

                with st.spinner("レイリー蒸留計算中（初回はしばらくかかります）..."):
                    try:
                        conc_result = calc_rayleigh_distillation(conc_sol_dicts, moles, conc_P)
                        st.session_state["conc_result"] = conc_result
                        st.session_state["conc_sol_dicts"] = conc_sol_dicts
                        st.session_state["conc_unit_saved"] = conc_unit
                        st.session_state["conc_T_ref_saved"] = conc_T_ref
                        st.session_state["conc_P_saved"] = conc_P
                    except Exception as e:
                        st.error(f"計算エラー: {e}")

        if "conc_result" in st.session_state:
            conc_result = st.session_state["conc_result"]
            conc_sol_sv = st.session_state["conc_sol_dicts"]
            unit_sv = st.session_state.get("conc_unit_saved", "mol")
            P_sv = st.session_state.get("conc_P_saved", conc_P)
            T_ref_sv = st.session_state.get("conc_T_ref_saved", 25.0)

            def _mol_to_disp(mol_vals, s, unit):
                if unit == "mol":
                    return mol_vals
                elif unit == "g":
                    return [v * s["mw"] for v in mol_vals]
                else:  # mL
                    rho = density_water(T_ref_sv) if s["name"] == "Water" else density_solvent(s, T_ref_sv)
                    return [v * s["mw"] / rho for v in mol_vals]

            fig_conc = make_subplots(specs=[[{"secondary_y": True}]])
            _colors = ["royalblue", "tomato", "green", "purple"]
            total_disp = None
            for idx, s in enumerate(conc_sol_sv):
                mol_vals = conc_result["amounts"].get(s["name"], [])
                disp_vals = _mol_to_disp(mol_vals, s, unit_sv)
                fig_conc.add_trace(
                    go.Scatter(x=conc_result["evap_fraction"], y=disp_vals,
                               name=s["name"], line=dict(color=_colors[idx % 4])),
                    secondary_y=False,
                )
                total_disp = disp_vals[:] if total_disp is None else [
                    a + b for a, b in zip(total_disp, disp_vals)]

            if total_disp:
                fig_conc.add_trace(
                    go.Scatter(x=conc_result["evap_fraction"], y=total_disp,
                               name="合計", line=dict(color="black", dash="dash")),
                    secondary_y=False,
                )

            valid_T = [(ef, T) for ef, T in zip(conc_result["evap_fraction"],
                                                 conc_result["T_bp"]) if T is not None]
            if valid_T:
                efs, Ts = zip(*valid_T)
                fig_conc.add_trace(
                    go.Scatter(x=list(efs), y=list(Ts), name="沸点 (°C)",
                               line=dict(color="red", dash="dot", width=2)),
                    secondary_y=True,
                )

            fig_conc.update_yaxes(title_text=f"量 ({unit_sv})", secondary_y=False)
            fig_conc.update_yaxes(title_text="沸点 (°C)", secondary_y=True)
            fig_conc.update_xaxes(title_text="蒸発割合", range=[0, 1])
            fig_conc.update_layout(
                title=f"レイリー蒸留 @ {P_sv:.3f} kPa",
                width=800, height=400, plot_bgcolor="white",
            )
            st.plotly_chart(fig_conc, use_container_width=False)

            # 三相域注記: 沸点が一定な区間を検出してユーザーに説明
            T_bp_vals = [T for T in conc_result["T_bp"] if T is not None]
            if len(T_bp_vals) >= 3:
                plateau_T = None
                streak = 1
                for _i in range(1, len(T_bp_vals)):
                    if abs(T_bp_vals[_i] - T_bp_vals[_i - 1]) < 0.5:
                        streak += 1
                        if streak >= 3:
                            plateau_T = T_bp_vals[_i]
                            break
                    else:
                        streak = 1
                if plateau_T is not None:
                    st.info(
                        f"沸点が **{plateau_T:.1f} °C** 付近で一定になっている区間があります。"
                        "これは不均一共沸系の**三相共存域**（蒸気＋二液相）を通過しているためで、"
                        "Gibbs の相律により温度・気相組成が一定に保たれる物理的に正常な挙動です。"
                    )
