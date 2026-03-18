import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from plotly.subplots import make_subplots

from engine import (calc_lle_diagram, calc_layer_composition,
                    calc_vapor_pressure_curve, calc_vle_xy,
                    calc_rayleigh_distillation, density_water, density_solvent)
from solvents import (MISCIBLE_SOLVENTS, IMMISCIBLE_SOLVENTS,
                      ALL_SOLVENTS, get_solvent_by_name)

st.set_page_config(page_title="Ternary LLE Calculator", layout="wide")
st.title("3成分系 液液平衡（LLE）計算・可視化")

# ── サイドバー ──────────────────────────────────────────────
with st.sidebar:
    st.header("計算条件")
    T_C = st.slider("温度 (°C)", 10, 100, 25)
    n_grid = st.slider("格子点数", 10, 50, 25)

    st.divider()
    st.header("溶媒選択")
    st.markdown("**Component 0:** Water（固定）")
    sel_misc  = st.selectbox("Component 1 (水溶性)", [s["name"] for s in MISCIBLE_SOLVENTS], index=0)
    sel_immis = st.selectbox("Component 2 (非水溶性)", [s["name"] for s in IMMISCIBLE_SOLVENTS], index=0)
    solvent1 = get_solvent_by_name(sel_misc, MISCIBLE_SOLVENTS)
    solvent2 = get_solvent_by_name(sel_immis, IMMISCIBLE_SOLVENTS)

    run = st.button("計算実行", type="primary")

    st.divider()
    st.header("仕込み組成 → 層分離計算")
    unit = st.radio("単位", ['g', 'mol', 'mL'], horizontal=True)
    amt_water = st.number_input("Water", min_value=0.0, value=1.0, step=0.1, format="%.3f")
    amt_misc  = st.number_input(solvent1["name"], min_value=0.0, value=1.0, step=0.1,
                                format="%.3f", key=f"amt_{solvent1['thermo_id']}")
    amt_immis = st.number_input(solvent2["name"], min_value=0.0, value=1.0, step=0.1,
                                format="%.3f", key=f"amt_{solvent2['thermo_id']}")
    calc_layers = st.button("層分離計算", type="secondary")

# ── 溶媒変更時にセッション状態をリセット ──────────────────────
solvent_key = (solvent1["thermo_id"], solvent2["thermo_id"])
if st.session_state.get("solvent_key") != solvent_key:
    for k in ["tie_lines", "binodal_pts", "T_C", "layer_result"]:
        st.session_state.pop(k, None)
    st.session_state["solvent_key"] = solvent_key

# ── タブ構造 ──────────────────────────────────────────────────
tab_lle, tab_vp, tab_vle, tab_conc, tab_logic = st.tabs([
    "LLE線図", "蒸気圧曲線", "VLE線図", "濃縮シミュレーション", "ロジック"
])

# ── LLE ダイアグラム計算 ──────────────────────────────────────
if run or "tie_lines" not in st.session_state:
    with st.spinner("LLE 計算中..."):
        try:
            tie_lines, binodal_pts = calc_lle_diagram(T_C, solvent1, solvent2, n_grid)
        except Exception as e:
            st.error(f"選択した溶媒のUNIFACグループデータが見つかりません: {e}")
            tie_lines, binodal_pts = [], []
    st.session_state["tie_lines"] = tie_lines
    st.session_state["binodal_pts"] = binodal_pts
    st.session_state["T_C"] = T_C
else:
    tie_lines = st.session_state["tie_lines"]
    binodal_pts = st.session_state["binodal_pts"]
    T_C = st.session_state["T_C"]

# ── 層分離計算 ────────────────────────────────────────────────
layer_result = None
if calc_layers:
    with st.spinner("層分離計算中..."):
        try:
            layer_result = calc_layer_composition(
                T_C, [amt_water, amt_misc, amt_immis], unit, solvent1, solvent2
            )
        except Exception as e:
            st.error(f"選択した溶媒のUNIFACグループデータが見つかりません: {e}")
            layer_result = None
    if layer_result is not None:
        st.session_state["layer_result"] = layer_result
elif "layer_result" in st.session_state:
    layer_result = st.session_state["layer_result"]

# サイドバーに層分離結果表示
with st.sidebar:
    if layer_result:
        if layer_result.get("error"):
            st.warning(layer_result["error"])
        elif layer_result["phase_count"] == 2:
            st.success("2相分離を検出")
            labels = ["Water", solvent1["name"], solvent2["name"]]
            rows = []
            for phase_name, key in [("水層", "water_layer"), ("有機層", "organic_layer")]:
                d = layer_result[key]
                for i, comp in enumerate(labels):
                    rows.append({
                        "相": phase_name, "成分": comp,
                        "mol%": f"{d['mol_pct'][i]:.2f}",
                        "w/w%": f"{d['ww_pct'][i]:.2f}",
                        "v/v%": f"{d['vv_pct'][i]:.2f}",
                    })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            bw = layer_result["beta_water"]
            bo = layer_result["beta_organic"]
            st.caption(f"水層 β={bw:.4f} | 有機層 β={bo:.4f}")
        else:
            st.info("2相分離なし（均一相）")

with tab_lle:
    st.caption(f"Water – {solvent1['name']} – {solvent2['name']} | UNIFAC Dortmund モデル")

    # ── 直角三角形 三角図 ─────────────────────────────────────────
    # 座標: x = solvent2 mol fr., y = solvent1 mol fr., Water = 1 - x - y

    fig = go.Figure()

    # 三角形外枠
    fig.add_trace(go.Scatter(
        x=[0, 1, 0, 0], y=[0, 0, 1, 0],
        mode='lines', line=dict(color='black', width=2),
        showlegend=False, hoverinfo='skip'
    ))

    # グリッド線
    grid_vals = [v/10 for v in range(1, 10)]
    for v in grid_vals:
        fig.add_trace(go.Scatter(x=[v, 0], y=[0, v], mode='lines',
            line=dict(color='#cccccc', width=0.5, dash='dot'), showlegend=False, hoverinfo='skip'))
        fig.add_trace(go.Scatter(x=[v, v], y=[0, 1-v], mode='lines',
            line=dict(color='#ccddff', width=0.5, dash='dot'), showlegend=False, hoverinfo='skip'))
        fig.add_trace(go.Scatter(x=[0, 1-v], y=[v, v], mode='lines',
            line=dict(color='#ffeecc', width=0.5, dash='dot'), showlegend=False, hoverinfo='skip'))

    # タイライン
    for (L1, L2) in tie_lines:
        fig.add_trace(go.Scatter(
            x=[L1[2], L2[2]], y=[L1[1], L2[1]],
            mode='lines', line=dict(color='gray', width=1),
            showlegend=False, hoverinfo='skip'
        ))

    # バイノーダル点
    if binodal_pts:
        fig.add_trace(go.Scatter(
            x=[p[2] for p in binodal_pts],
            y=[p[1] for p in binodal_pts],
            mode='markers', marker=dict(color='royalblue', size=6, opacity=0.8),
            name='バイノーダル点',
            customdata=[round(1-p[2]-p[1], 4) for p in binodal_pts],
            hovertemplate=f'{solvent2["name"]}=%{{x:.3f}}<br>{solvent1["name"]}=%{{y:.3f}}<br>Water=%{{customdata:.3f}}<extra></extra>'
        ))

    # 仕込み組成マーカー (layer_result がある場合)
    if layer_result and layer_result.get("phase_count") == 2:
        z = layer_result["input_zs"]
        wl = layer_result["water_layer"]["zs"]
        ol = layer_result["organic_layer"]["zs"]
        fig.add_trace(go.Scatter(
            x=[wl[2], ol[2]], y=[wl[1], ol[1]],
            mode='lines+markers',
            line=dict(color='red', width=2, dash='dash'),
            marker=dict(color='red', size=8), name='仕込みタイライン'
        ))
        fig.add_trace(go.Scatter(
            x=[z[2]], y=[z[1]],
            mode='markers', marker=dict(color='red', size=10, symbol='star'),
            name='仕込み組成',
            hovertemplate=f'{solvent2["name"]}={z[2]:.3f}<br>{solvent1["name"]}={z[1]:.3f}<br>Water={z[0]:.3f}<extra></extra>'
        ))

    fig.update_layout(
        xaxis=dict(
            title=f"{solvent2['name']} (mol fr.)",
            range=[0, 1],
            fixedrange=True,
            scaleanchor='y', scaleratio=1,
            constrain='domain',
            dtick=0.1, showgrid=False,
        ),
        yaxis=dict(
            title=f"{solvent1['name']} (mol fr.)",
            range=[0, 1],
            fixedrange=True,
            constrain='domain',
            dtick=0.1, showgrid=False,
        ),
        title=f"Water–{solvent1['name']}–{solvent2['name']} LLE  @ {T_C}°C, 101.325 kPa",
        height=600,
        plot_bgcolor='white',
        legend=dict(x=0.75, y=0.95),
        dragmode='zoom',
        annotations=[
            dict(x=0, y=0, text='Water',              showarrow=False, font=dict(size=13), yshift=-20),
            dict(x=1, y=0, text=solvent2["name"],     showarrow=False, font=dict(size=13), yshift=-20),
            dict(x=0, y=1, text=solvent1["name"],     showarrow=False, font=dict(size=13), xshift=-40),
        ],
    )

    st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': False})

    # ── 統計 & データテーブル ────────────────────────────────────
    st.metric("検出タイライン数", len(tie_lines))

    if tie_lines:
        s1 = solvent1["name"]
        s2 = solvent2["name"]
        df = pd.DataFrame(
            [(L1[0], L1[1], L1[2], L2[0], L2[1], L2[2])
             for L1, L2 in tie_lines],
            columns=[f'L1_Water', f'L1_{s1}', f'L1_{s2}',
                     f'L2_Water', f'L2_{s1}', f'L2_{s2}'],
        )
        n = len(df)
        if n <= 10:
            df_display = df
        else:
            indices = [int(round(i * (n - 1) / 9)) for i in range(10)]
            df_display = df.iloc[indices].reset_index(drop=True)
        st.caption(f"代表タイライン（最大10行 / 全{n}本）")
        st.dataframe(df_display.style.format("{:.4f}"), use_container_width=True)
        st.download_button(
            "CSV ダウンロード（全データ）",
            df.to_csv(index=False),
            "lle_result.csv",
            mime="text/csv",
        )
    else:
        st.info("2相分離点が検出されませんでした。温度を下げるか格子点数を増やしてください。")

# ── 蒸気圧曲線タブ ────────────────────────────────────────────
with tab_vp:
    st.header("蒸気圧曲線")
    col_vp1, col_vp2 = st.columns([1, 3])
    with col_vp1:
        vp_name = st.selectbox("成分", [s["name"] for s in ALL_SOLVENTS], key="vp_name")
        vp_T_range = st.slider("温度範囲 (°C)", -50, 250, (0, 150), key="vp_T_range")
        vp_sol = get_solvent_by_name(vp_name, ALL_SOLVENTS)
        vp_tid = vp_sol.get("thermo_surrogate", vp_sol["thermo_id"])

    with st.spinner("計算中..."):
        try:
            vp_data = calc_vapor_pressure_curve(vp_tid, vp_T_range[0], vp_T_range[1])
        except Exception as e:
            vp_data = None
            with col_vp2:
                st.error(f"計算エラー: {e}")

    if vp_data:
        with col_vp2:
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
                height=450, plot_bgcolor="white",
            )
            st.plotly_chart(fig_vp, use_container_width=True)

# ── VLE線図タブ ────────────────────────────────────────────────
with tab_vle:
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
                xaxis=dict(title=f"x₁ ({s1d})", range=[0, 1]),
                yaxis=dict(title=f"y₁ ({s1d})", range=[0, 1]),
                height=420, plot_bgcolor="white",
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
                    x=list(xs), y=list(Ts), name="泡点",
                    line=dict(color="blue", width=2),
                ))
            if pts_d:
                xs, Ts = zip(*pts_d)
                fig_txy.add_trace(go.Scatter(
                    x=list(xs), y=list(Ts), name="露点",
                    line=dict(color="orange", width=2, dash="dash"),
                ))
            fig_txy.update_layout(
                title=f"T-xy線図 @ {Pd:.3f} kPa",
                xaxis=dict(title=f"モル分率 ({s1d})", range=[0, 1]),
                yaxis_title="温度 (°C)",
                height=420, plot_bgcolor="white",
            )
            st.plotly_chart(fig_txy, use_container_width=True)

# ── 濃縮シミュレーションタブ ───────────────────────────────────
with tab_conc:
    st.header("濃縮シミュレーション（レイリー蒸留）")
    conc_n = st.radio("成分数", [2, 3, 4], horizontal=True, key="conc_n")
    conc_unit = st.radio("単位", ["mol", "g", "mL"], horizontal=True, key="conc_unit")

    cols_conc = st.columns(conc_n)
    conc_names, conc_amts = [], []
    for ci in range(conc_n):
        with cols_conc[ci]:
            sel = st.selectbox(f"成分 {ci+1}", [s["name"] for s in ALL_SOLVENTS],
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
            height=500, plot_bgcolor="white",
        )
        st.plotly_chart(fig_conc, use_container_width=True)

with tab_logic:
    st.header("計算ロジック・数式説明")

    with st.expander("1. UNIFAC Dortmund モデル（活量係数）", expanded=True):
        st.markdown(r"""
**活量係数の分解：**

$$
\ln \gamma_i = \ln \gamma_i^C + \ln \gamma_i^R
$$

**組み合わせ項（Combinatorial term）：**

$$
\ln \gamma_i^C = 1 - V_i' + \ln V_i' - 5q_i \left(1 - \frac{V_i}{F_i} + \ln \frac{V_i}{F_i}\right)
$$

$$
V_i' = \frac{r_i^{3/4}}{\sum_j x_j r_j^{3/4}}, \quad V_i = \frac{r_i}{\sum_j x_j r_j}, \quad F_i = \frac{q_i}{\sum_j x_j q_j}
$$

ここで $r_i$ はvan der Waals体積パラメータ, $q_i$ は表面積パラメータ。

**残差項（Residual term）：**

$$
\ln \gamma_i^R = \sum_k \nu_k^{(i)} \left(\ln \Gamma_k - \ln \Gamma_k^{(i)}\right)
$$

$$
\ln \Gamma_k = Q_k \left[1 - \ln\left(\sum_m \Theta_m \Psi_{mk}\right) - \sum_m \frac{\Theta_m \Psi_{km}}{\sum_n \Theta_n \Psi_{nm}}\right]
$$

**温度依存相互作用パラメータ（Dortmund修正）：**

$$
\Psi_{mn} = \exp\left(-\frac{a_{mn} + b_{mn}T + c_{mn}T^2}{T}\right)
$$
""")

    with st.expander("2. 液液平衡（LLE）条件"):
        st.markdown(r"""
**等フガシティー条件：**

$$
f_i^{L1} = f_i^{L2} \quad (i = 1, 2, \ldots, N)
$$

液相では $f_i^L = x_i \gamma_i P_i^{sat}$ なので：

$$
x_i^{L1} \gamma_i^{L1} = x_i^{L2} \gamma_i^{L2}
$$

**物質収支：**

$$
z_i = \beta^{L1} x_i^{L1} + \beta^{L2} x_i^{L2}, \quad \beta^{L1} + \beta^{L2} = 1
$$

**Rachford-Rice方程式（2液相）：**

$$
\sum_i \frac{z_i (K_i - 1)}{1 + \beta (K_i - 1)} = 0, \quad K_i = \frac{x_i^{L2}}{x_i^{L1}}
$$
""")

    with st.expander("3. フラッシュアルゴリズム"):
        st.markdown(r"""
**ステップ 1 — 安定性テスト（Tangent Plane Distance, TPD）：**

$$
\text{TPD}(\mathbf{y}) = \sum_i y_i \left[\ln y_i + \ln \gamma_i(\mathbf{y}) - \ln z_i - \ln \gamma_i(\mathbf{z})\right]
$$

$\text{TPD} < 0$ となる試験組成 $\mathbf{y}$ が存在する場合、相分離が起こる。

**ステップ 2 — 逐次代入法（Successive Substitution）：**

1. 初期 $K_i$ を推定（Wilson式など）
2. Rachford-Rice方程式を解いて $\beta$, $x_i^{L1}$, $x_i^{L2}$ を計算
3. 活量係数 $\gamma_i^{L1}$, $\gamma_i^{L2}$ を更新
4. $K_i \leftarrow \gamma_i^{L1} / \gamma_i^{L2}$
5. 収束まで繰り返す（$|\Delta K_i| < \varepsilon$）

本アプリでは **thermo ライブラリ**（`FlashVLN`）が上記を自動実行します。
""")

    with st.expander("4. 密度・単位変換"):
        st.markdown(r"""
**水の密度 — Kell (1975) 多項式（g/mL）：**

$$
\rho_{\text{water}}(T) = \frac{999.842594 + 6.793952\times10^{-2}T - 9.095290\times10^{-3}T^2 + \cdots}{1000}
$$

（$T$ は摂氏、有効範囲：10〜100 °C）

**有機溶媒の密度 — 線形近似（g/mL）：**

$$
\rho_{\text{solvent}}(T) = a + b \cdot T
$$

| パラメータ | 意味 |
|---|---|
| $a$ | 0°C外挿密度 (g/mL) |
| $b$ | 温度係数 (g/mL/°C)、通常負値 |

**単位変換（g / mol / mL → mol）：**

| 入力単位 | 変換式 |
|---|---|
| g | $n_i = m_i / M_i$ |
| mol | $n_i = $ 入力値そのまま |
| mL | $n_i = (V_i \cdot \rho_i) / M_i$ |
""")

    with st.expander("5. scipy の使用箇所"):
        st.markdown(r"""
**Rachford-Rice 方程式の数値解法：**

thermo ライブラリは内部で `scipy.optimize` を使用してRachford-Rice方程式を解いています。

具体的には：
- `scipy.optimize.brentq` または `scipy.optimize.ridder` などのブラケット法
- 解の存在区間 $[\beta_{\min}, \beta_{\max}]$ を解析的に求めた後、数値的に根を求める

**安定性テスト（TPD最小化）：**

`scipy.optimize.minimize` を用いて接平面距離関数を最小化し、相分離の判定を行います。

これらの計算はすべて `FlashVLN.flash()` 呼び出し内で自動的に処理されます。
""")
