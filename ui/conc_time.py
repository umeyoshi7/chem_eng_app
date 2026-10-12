from __future__ import annotations

import numpy as np
import streamlit as st
import plotly.graph_objects as go

from core.heat_transfer.models import ReactorSpec
from core.heat_transfer.reactor_db import list_tag_nos, get_reactor_spec
from core.solvents import ALL_SOLVENTS, get_solvent_by_name
from core.concentration_time import calc_concentration_time
from core.thermo_engine import calc_rayleigh_distillation, density_solvent, density_water

_U_FACTOR = 3.6   # W/(m²·K) → kJ/(m²·h·K)
_SOLVENT_NAMES = [s["name"] for s in ALL_SOLVENTS]


# ── セッション初期化 ──────────────────────────────────────────────────────────

def _init_state() -> None:
    defaults = {
        "ct_rayleigh_result": None,
        "ct_rayleigh_fingerprint": None,
        "ct_rayleigh_solvents": None,
        "ct_time_result": None,
        "ct_conc_src": "手動入力",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── メイン描画 ────────────────────────────────────────────────────────────────

def render() -> None:
    _init_state()
    _col_hdr, _col_rst = st.columns([9, 1])
    with _col_hdr:
        st.title("濃縮時間推算")
    with _col_rst:
        st.write("")
        if st.button("リセット", key="ct_reset_btn"):
            for _k in list(st.session_state.keys()):
                if _k.startswith("ct_"):
                    del st.session_state[_k]
            st.rerun()
    st.caption("レイリー蒸留シミュレーションと伝熱計算を組み合わせて、濃縮に要する時間を推算します。")

    # ════════════════════════════════════════════════════════════════════════
    # Section 1 & 2: 左右カラムで溶媒組成 / 反応槽仕様
    # ════════════════════════════════════════════════════════════════════════
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("溶媒組成")
        conc_src = st.radio(
            "入力元",
            ["手動入力", "濃縮シミュレーションから引用"],
            horizontal=True,
            key="ct_conc_src",
        )

        if conc_src == "濃縮シミュレーションから引用":
            conc_result_ref = st.session_state.get("conc_result")
            if conc_result_ref is None:
                st.warning("先に濃縮シミュレーションを実行してください。")
                ct_names, ct_amts, conc_unit, ct_P, ct_T_ref, sol_dicts = [], [], "mol", 101.325, 25.0, []
                _conc_src_ready = False
            else:
                sol_dicts = st.session_state["conc_sol_dicts"]
                ct_names = [s["name"] for s in sol_dicts]
                conc_unit = st.session_state.get("conc_unit_saved", "mol")
                ct_P = st.session_state.get("conc_P_saved", 101.325)
                ct_T_ref = st.session_state.get("conc_T_ref_saved", 25.0)

                # 引用元の初期量を amounts の最初のステップから取得
                ct_amts = [conc_result_ref["amounts"][nm][0] for nm in ct_names]

                lines = [f"- 溶媒: {', '.join(ct_names)}"]
                lines.append(f"- 圧力: {ct_P:.3f} kPa")
                lines.append(f"- 仕込み温度: {ct_T_ref:.1f} °C")
                st.info("**濃縮シミュレーションの設定を引用**\n" + "\n".join(lines))
                _conc_src_ready = True
        else:
            _conc_src_ready = True
            conc_n = st.radio("成分数", [2, 3, 4], horizontal=True, key="ct_n")
            conc_unit = st.radio("単位", ["mol", "g", "mL"], horizontal=True, key="ct_unit")

            cols_solv = st.columns(conc_n)
            ct_names, ct_amts = [], []
            for ci in range(conc_n):
                with cols_solv[ci]:
                    sel = st.selectbox(
                        f"成分 {ci + 1}", _SOLVENT_NAMES,
                        key=f"ct_sel_{ci}",
                    )
                    ct_names.append(sel)
                    amt = st.number_input(
                        f"量 ({conc_unit})", min_value=0.0, value=1.0,
                        step=0.1, format="%.3f", key=f"ct_amt_{ci}",
                    )
                    ct_amts.append(amt)

            col_cP, col_cT = st.columns(2)
            with col_cP:
                ct_P = st.number_input(
                    "圧力 (kPa)", min_value=1.0, value=101.325,
                    step=1.0, format="%.3f", key="ct_P",
                )
            with col_cT:
                ct_T_ref = st.number_input(
                    "仕込み温度 (°C) ※mL換算用",
                    min_value=-50.0, max_value=200.0, value=25.0,
                    step=1.0, format="%.1f", key="ct_T_ref",
                )
            sol_dicts = None  # 手動入力時は計算ブロックで取得

    with col_right:
        st.subheader("反応槽仕様")
        input_method = st.radio(
            "入力方法", ["データベースから選択", "手動入力"],
            horizontal=True, key="ct_input_method",
        )

        if input_method == "データベースから選択":
            try:
                tags = list_tag_nos()
            except Exception as e:
                st.error(f"DB 読み込みエラー: {e}")
                return

            selected_tag = st.selectbox("Tag No.", tags, key="ct_tag_select")
            try:
                reactor_base = get_reactor_spec(selected_tag)
            except Exception as e:
                st.error(str(e))
                return

            c1, c2, c3 = st.columns(3)
            c1.metric("容量 [L]", f"{reactor_base.volume_L:.0f}")
            c2.metric("直径 [m]", f"{reactor_base.diameter_m:.2f}")
            c3.metric("鏡形状", reactor_base.mirror_type)

            U_kJ_h = st.number_input(
                "U [kJ/(m²·h·K)]", 36.0, 7200.0,
                float(reactor_base.U * _U_FACTOR),
                step=36.0, key="ct_U_override",
                help="実測値で補正する場合に変更",
            )
            reactor = ReactorSpec(
                reactor_base.tag_no, U_kJ_h / _U_FACTOR,
                reactor_base.volume_L, reactor_base.diameter_m, reactor_base.mirror_type,
            )

        else:
            tag_no = st.text_input("Tag No.", value="手動入力", key="ct_manual_tag")
            mr1, mr2 = st.columns(2)
            U_kJ_h = mr1.number_input(
                "U [kJ/(m²·h·K)]", 36.0, 7200.0, 900.0,
                step=36.0, key="ct_manual_U",
            )
            vol_L = mr2.number_input("容量 [L]", 1.0, 100000.0, 200.0, key="ct_manual_vol")
            mr3, mr4 = st.columns(2)
            D_m = mr3.number_input(
                "内径 [m]", 0.1, 10.0, 0.70,
                format="%.2f", key="ct_manual_D",
            )
            mirror = mr4.selectbox("鏡形状", ["ED", "SD"], key="ct_manual_mirror")
            reactor = ReactorSpec(tag_no, U_kJ_h / _U_FACTOR, vol_L, D_m, mirror)

        st.subheader("操作条件")
        T_jacket = st.number_input(
            "ジャケット設定温度 [°C]", -50.0, 300.0, 80.0,
            step=1.0, format="%.1f", key="ct_T_jacket",
        )
        target_pct = st.slider(
            "目標蒸発分率 [%]", min_value=1, max_value=99, value=80, step=1,
            key="ct_target_pct",
        )

    st.divider()

    # ════════════════════════════════════════════════════════════════════════
    # 計算実行ボタン
    # ════════════════════════════════════════════════════════════════════════
    run_btn = st.button(
        "計算実行", key="run_ct", type="primary",
        disabled=(conc_src == "濃縮シミュレーションから引用" and not _conc_src_ready),
    )

    if run_btn:
        if conc_src == "濃縮シミュレーションから引用":
            # 濃縮シミュレーション結果をそのまま利用（Rayleigh 再計算不要）
            rayleigh_result = st.session_state["conc_result"]
            sol_dicts_calc = st.session_state["conc_sol_dicts"]
        else:
            # 手動入力: 入力バリデーション → mol 換算 → Rayleigh 計算
            if len(set(ct_names)) < len(ct_names):
                st.error("同一成分が複数選択されています。異なる成分を選択してください。")
                st.stop()

            sol_dicts_calc = [get_solvent_by_name(nm, ALL_SOLVENTS) for nm in ct_names]

            # mol 換算
            moles = []
            for s, amt in zip(sol_dicts_calc, ct_amts):
                if conc_unit == "mol":
                    moles.append(amt)
                elif conc_unit == "g":
                    moles.append(amt / s["mw"])
                else:  # mL
                    rho = density_water(ct_T_ref) if s["name"] == "Water" else density_solvent(s, ct_T_ref)
                    moles.append(amt * rho / s["mw"])

            # フィンガープリントで Rayleigh 計算の再実行を判断
            fingerprint = (tuple(ct_names), tuple(round(m, 6) for m in moles), round(ct_P, 4))

            if st.session_state["ct_rayleigh_fingerprint"] != fingerprint:
                with st.spinner("レイリー蒸留計算中（初回はしばらくかかります）..."):
                    try:
                        rayleigh_result = calc_rayleigh_distillation(sol_dicts_calc, moles, ct_P)
                        st.session_state["ct_rayleigh_result"] = rayleigh_result
                        st.session_state["ct_rayleigh_solvents"] = sol_dicts_calc
                        st.session_state["ct_rayleigh_fingerprint"] = fingerprint
                    except Exception as e:
                        st.error(f"レイリー蒸留計算エラー: {e}")
                        st.stop()
            else:
                rayleigh_result = st.session_state["ct_rayleigh_result"]
                sol_dicts_calc = st.session_state["ct_rayleigh_solvents"]

        # 濃縮時間積算
        try:
            time_result = calc_concentration_time(
                rayleigh_result, sol_dicts_calc, reactor, T_jacket,
            )
            st.session_state["ct_time_result"] = {
                **time_result,
                "target_pct": target_pct,
                "reactor_tag": reactor.tag_no,
                "T_jacket": T_jacket,
            }
        except Exception as e:
            st.error(f"濃縮時間計算エラー: {e}")
            st.stop()

    # ════════════════════════════════════════════════════════════════════════
    # 結果表示
    # ════════════════════════════════════════════════════════════════════════
    if st.session_state["ct_time_result"] is not None:
        res = st.session_state["ct_time_result"]
        evap_frac = res["evap_fraction"]
        time_min = res["time_min"]
        saved_target = res.get("target_pct", target_pct)
        saved_Tjk = res.get("T_jacket", T_jacket)

        if res.get("jacket_too_cold"):
            st.warning(
                "ジャケット温度が沸点以下のステップがあります。"
                "そのステップ以降の蒸発は停止とみなしました。"
                "ジャケット温度を上げてください。"
            )
        if res.get("hvap_fallback_used"):
            st.info("一部成分の蒸発熱が取得できなかったため、40 kJ/mol でフォールバックしました。")

        # 目標時間の補間
        target_frac = saved_target / 100.0
        if max(evap_frac) >= target_frac:
            target_time = float(np.interp(target_frac, evap_frac, time_min))
        else:
            target_time = None

        # メトリクス
        col_m1, col_m2 = st.columns(2)
        col_m1.metric(
            f"目標 {saved_target}% 到達時間",
            f"{target_time:.1f} min" if target_time is not None else "到達不可",
        )
        if time_min and max(time_min) > 0:
            col_m2.metric(
                "最大蒸発分率での推算時間",
                f"{max(time_min):.1f} min  ({max(evap_frac)*100:.0f}%)",
            )

        # グラフ
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=[f * 100 for f in evap_frac],
            y=time_min,
            mode="lines",
            name="濃縮時間",
            line=dict(color="royalblue", width=2),
        ))

        if target_time is not None:
            fig.add_trace(go.Scatter(
                x=[saved_target],
                y=[target_time],
                mode="markers",
                marker=dict(size=12, color="tomato", symbol="circle"),
                name=f"目標 {saved_target}%: {target_time:.1f} min",
            ))

        fig.update_layout(
            title=f"濃縮時間推算  (T_jacket = {saved_Tjk:.1f} °C)",
            xaxis_title="蒸発分率 [%]",
            yaxis_title="濃縮時間 [min]",
            template="plotly_white",
            height=450,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, use_container_width=True)

        with st.expander("計算詳細"):
            import pandas as pd
            df = pd.DataFrame({
                "蒸発分率 [%]": [f * 100 for f in evap_frac],
                "累積時間 [min]": time_min,
            })
            st.dataframe(df.style.format({"蒸発分率 [%]": "{:.1f}", "累積時間 [min]": "{:.2f}"}),
                         use_container_width=True)
