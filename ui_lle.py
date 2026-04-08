import contextlib
import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from engine import calc_lle_diagram, calc_layer_composition
from solvents import MISCIBLE_SOLVENTS, IMMISCIBLE_SOLVENTS, get_solvent_by_name


def _init_state() -> None:
    defaults = {
        "lle_T_C": 25,
        "lle_n_grid": 25,
        "lle_unit": "g",
        "lle_amt_water": 1.0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def render_lle_tab(tab=None):
    _init_state()
    with (tab if tab is not None else contextlib.nullcontext()):
        _col_hdr, _col_rst = st.columns([9, 1])
        with _col_hdr:
            st.subheader("LLE線図")
        with _col_rst:
            if st.button("リセット", key="lle_reset_btn"):
                for _k in list(st.session_state.keys()):
                    if _k.startswith("lle_") or _k.startswith("amt_"):
                        del st.session_state[_k]
                for _k in ["tie_lines", "binodal_pts", "T_C", "layer_result"]:
                    st.session_state.pop(_k, None)
                st.rerun()

        col_ctrl, col_plot = st.columns([1, 2])

        with col_ctrl:
            st.header("計算条件")
            T_C = st.slider("温度 (°C)", 10, 100, 25, key="lle_T_C")
            n_grid = st.slider("格子点数", 10, 50, 25, key="lle_n_grid")

            st.divider()
            st.header("溶媒選択")
            st.markdown("**Component 0:** Water（固定）")
            sel_misc = st.selectbox("Component 1 (水溶性)", [s["name"] for s in MISCIBLE_SOLVENTS], index=0, key="lle_sel_misc")
            sel_immis = st.selectbox("Component 2 (非水溶性)", [s["name"] for s in IMMISCIBLE_SOLVENTS], index=0, key="lle_sel_immis")
            solvent1 = get_solvent_by_name(sel_misc, MISCIBLE_SOLVENTS)
            solvent2 = get_solvent_by_name(sel_immis, IMMISCIBLE_SOLVENTS)

            run = st.button("計算実行", type="primary")

            st.divider()
            st.header("仕込み組成 → 層分離計算")
            unit = st.radio("単位", ['g', 'mol', 'mL'], horizontal=True, key="lle_unit")
            amt_water = st.number_input("Water", min_value=0.0, value=1.0, step=0.1, format="%.3f", key="lle_amt_water")
            amt_misc = st.number_input(solvent1["name"], min_value=0.0, value=1.0, step=0.1,
                                       format="%.3f", key=f"amt_{solvent1['thermo_id']}")
            amt_immis = st.number_input(solvent2["name"], min_value=0.0, value=1.0, step=0.1,
                                        format="%.3f", key=f"amt_{solvent2['thermo_id']}")
            calc_layers = st.button("層分離計算", type="secondary")

        # 溶媒または温度・格子点数が変わったらキャッシュをリセット
        calc_key = (solvent1["thermo_id"], solvent2["thermo_id"], T_C, n_grid)
        if st.session_state.get("lle_calc_key") != calc_key:
            for k in ["tie_lines", "binodal_pts", "T_C", "layer_result"]:
                st.session_state.pop(k, None)
            st.session_state["lle_calc_key"] = calc_key

        # LLE計算（計算実行ボタンを押したときのみ実行）
        if run:
            with st.spinner("LLE 計算中..."):
                try:
                    tie_lines, binodal_pts = calc_lle_diagram(T_C, solvent1, solvent2, n_grid)
                except Exception as e:
                    st.error(f"選択した溶媒のUNIFACグループデータが見つかりません: {e}")
                    tie_lines, binodal_pts = [], []
            st.session_state["tie_lines"] = tie_lines
            st.session_state["binodal_pts"] = binodal_pts
            st.session_state["T_C"] = T_C
        elif "tie_lines" in st.session_state:
            tie_lines = st.session_state["tie_lines"]
            binodal_pts = st.session_state["binodal_pts"]
            T_C = st.session_state["T_C"]
        else:
            tie_lines = []
            binodal_pts = []

        # 層分離計算
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

        # 層分離結果表示（左カラム下部）
        with col_ctrl:
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
                    abs_rows = []
                    for phase_name, key in [("水層", "water_layer"), ("有機層", "organic_layer")]:
                        d = layer_result[key]
                        for i, comp in enumerate(labels):
                            abs_rows.append({
                                "相": phase_name, "成分": comp,
                                "mol": f"{d['moles'][i]:.4f}",
                                "g":   f"{d['grams'][i]:.4f}",
                                "mL":  f"{d['volumes_mL'][i]:.4f}",
                            })
                    st.caption("絶対量")
                    st.dataframe(pd.DataFrame(abs_rows), use_container_width=True, hide_index=True)
                else:
                    st.info("2相分離なし（均一相）")

        # 三角図（右カラム）
        with col_plot:
            if not run and "tie_lines" not in st.session_state:
                st.info("「計算実行」ボタンを押してください。")
            st.caption(f"Water – {solvent1['name']} – {solvent2['name']} | UNIFAC Dortmund モデル")

            fig = go.Figure()

            # 三角形外枠
            fig.add_trace(go.Scatter(
                x=[0, 1, 0, 0], y=[0, 0, 1, 0],
                mode='lines', line=dict(color='black', width=2),
                showlegend=False, hoverinfo='skip'
            ))

            # グリッド線
            grid_vals = [v / 10 for v in range(1, 10)]
            for v in grid_vals:
                fig.add_trace(go.Scatter(x=[v, 0], y=[0, v], mode='lines',
                                         line=dict(color='#cccccc', width=0.5, dash='dot'),
                                         showlegend=False, hoverinfo='skip'))
                fig.add_trace(go.Scatter(x=[v, v], y=[0, 1 - v], mode='lines',
                                         line=dict(color='#ccddff', width=0.5, dash='dot'),
                                         showlegend=False, hoverinfo='skip'))
                fig.add_trace(go.Scatter(x=[0, 1 - v], y=[v, v], mode='lines',
                                         line=dict(color='#ffeecc', width=0.5, dash='dot'),
                                         showlegend=False, hoverinfo='skip'))

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
                    customdata=[round(1 - p[2] - p[1], 4) for p in binodal_pts],
                    hovertemplate=f'{solvent2["name"]}=%{{x:.3f}}<br>{solvent1["name"]}=%{{y:.3f}}<br>Water=%{{customdata:.3f}}<extra></extra>'
                ))

            # 仕込み組成マーカー
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
                    dict(x=0, y=0, text='Water', showarrow=False, font=dict(size=13), yshift=-20),
                    dict(x=1, y=0, text=solvent2["name"], showarrow=False, font=dict(size=13), yshift=-20),
                    dict(x=0, y=1, text=solvent1["name"], showarrow=False, font=dict(size=13), xshift=-40),
                ],
            )

            st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': False})

            # 統計 & データテーブル
            st.metric("検出タイライン数", len(tie_lines))

            if tie_lines:
                s1 = solvent1["name"]
                s2 = solvent2["name"]
                df = pd.DataFrame(
                    [(L1[0], L1[1], L1[2], L2[0], L2[1], L2[2])
                     for L1, L2 in tie_lines],
                    columns=['L1_Water', f'L1_{s1}', f'L1_{s2}',
                             'L2_Water', f'L2_{s1}', f'L2_{s2}'],
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
