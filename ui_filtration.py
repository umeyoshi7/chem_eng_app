from __future__ import annotations
import math
import os
import sys

import pandas as pd
import streamlit as st

# ── filtration サブモジュールをパスに追加 ─────────────────────────────────────
_FI_DIR = os.path.join(os.path.dirname(__file__), "filtration")
if _FI_DIR not in sys.path:
    sys.path.insert(0, _FI_DIR)

for _key in list(sys.modules.keys()):
    if _key == "src" or _key.startswith("src."):
        del sys.modules[_key]

from src.calc import (
    calc_cake_resistance,
    calc_compressibility,
    calc_filtration_time_pressure,
    calc_filtration_time_centrifuge,
)
from src.plotting import plot_filtration_curve, plot_compressibility


# ── ヘルパー ──────────────────────────────────────────────────────────────────

def _fmt_alpha(alpha: float) -> str:
    """ケーキ比抵抗を X.XX × 10^N m/kg 形式でフォーマットする。"""
    if alpha is None or alpha <= 0:
        return "N/A"
    exp = int(math.floor(math.log10(alpha)))
    mant = alpha / (10 ** exp)
    return f"{mant:.2f} × 10^{exp}"


# ── セッション初期化 ──────────────────────────────────────────────────────────

def _init_state() -> None:
    defaults = {
        "fi_cake_results": None,   # list[CakeResistanceResult | None]
        "fi_comp_result": None,    # CompressibilityResult | None
        "fi_time_result": None,    # FiltrationTimeResult | None
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── メイン描画 ────────────────────────────────────────────────────────────────

def render() -> None:
    _init_state()
    st.title("ろ過時間推算")
    st.caption("Ruth のろ過方程式を用いてケーキ比抵抗・圧縮性指数の算出とろ過時間の推算を行います。")

    tab1, tab2, tab3 = st.tabs(["ケーキ比抵抗算出", "圧縮性指数算出", "ろ過時間推算"])

    # ════════════════════════════════════════════════════════════════════════
    # Tab 1: ケーキ比抵抗算出
    # ════════════════════════════════════════════════════════════════════════
    with tab1:
        st.subheader("ケーキ比抵抗算出")
        st.markdown(
            "Ruth 方程式に基づき、一定速度ろ過の実測値からケーキ比抵抗 α を算出します。  \n"
            "ブランク（ろ材のみ）のろ材抵抗 Rm を各圧力点で手動入力してください。"
        )
        st.divider()

        # 共通入力
        c1, c2, c3 = st.columns(3)
        mu_mPas = c1.number_input(
            "ろ液粘度 μ [mPa·s]", 0.01, 10000.0, 1.0,
            format="%.3f", key="fi_mu",
        )
        A_m2 = c2.number_input(
            "フィルター面積 A [m²]", 0.00001, 100.0, 0.01,
            format="%.5f", key="fi_A",
        )
        m_cake_g = c3.number_input(
            "乾燥ケーキ質量 [g]", 0.001, 1e7, 100.0,
            format="%.3f", key="fi_m_cake",
        )

        st.divider()
        n_rows = st.number_input(
            "圧力点数", min_value=1, max_value=10, value=3, step=1, key="fi_n_rows",
        )

        _DEFAULT_DP = [0.05, 0.10, 0.20, 0.30, 0.50, 0.10, 0.10, 0.10, 0.10, 0.10]

        st.markdown("**各圧力点の入力**")
        header_cols = st.columns([1, 1, 1, 1])
        header_cols[0].markdown("**ΔP [MPaG]**")
        header_cols[1].markdown("**Q [L/min]**")
        header_cols[2].markdown("**Rm [m⁻¹]**")
        header_cols[3].markdown("**α (結果)**")

        rows_data = []
        for i in range(n_rows):
            rc1, rc2, rc3, rc4 = st.columns([1, 1, 1, 1])
            dP = rc1.number_input(
                f"ΔP #{i+1}", 0.001, 10.0,
                _DEFAULT_DP[i] if i < len(_DEFAULT_DP) else 0.1,
                format="%.3f", key=f"fi_dP_{i}",
                label_visibility="collapsed",
            )
            Q = rc2.number_input(
                f"Q #{i+1}", 0.00001, 10000.0, 1.0,
                format="%.4f", key=f"fi_Q_{i}",
                label_visibility="collapsed",
            )
            Rm = rc3.number_input(
                f"Rm #{i+1}", 0.0, 1e15, 0.0,
                format="%.3e", key=f"fi_Rm_{i}",
                label_visibility="collapsed",
            )
            rows_data.append((dP, Q, Rm))

            # 既存結果を表示
            cake_results = st.session_state.get("fi_cake_results")
            if cake_results and i < len(cake_results) and cake_results[i] is not None:
                rc4.markdown(_fmt_alpha(cake_results[i].alpha_m_per_kg) + " m/kg")
            else:
                rc4.markdown("—")

        if st.button("ケーキ比抵抗を計算", key="run_fi_cake", type="primary"):
            results = []
            all_ok = True
            for i, (dP, Q, Rm) in enumerate(rows_data):
                try:
                    r = calc_cake_resistance(dP, Q, mu_mPas, A_m2, m_cake_g, Rm)
                    results.append(r)
                    for note in r.notes:
                        st.warning(f"点{i+1}: {note}")
                except ValueError as e:
                    st.error(f"点{i+1}: {e}")
                    results.append(None)
                    all_ok = False
            st.session_state["fi_cake_results"] = results
            if all_ok:
                st.success("計算完了")
            st.rerun()

        # 結果テーブル
        cake_results = st.session_state.get("fi_cake_results")
        if cake_results:
            valid = [(i, r) for i, r in enumerate(cake_results) if r is not None]
            if valid:
                st.divider()
                st.markdown("**算出結果**")
                df_data = {
                    "点": [i + 1 for i, _ in valid],
                    "ΔP [MPaG]": [r.delta_P_Pa / 1e6 for _, r in valid],
                    "Q [L/min]": [r.Q_m3_s * 60 * 1000 for _, r in valid],
                    "α [m/kg]": [_fmt_alpha(r.alpha_m_per_kg) for _, r in valid],
                    "Rm [m⁻¹]": [f"{r.Rm_m_inv:.2e}" for _, r in valid],
                }
                st.dataframe(pd.DataFrame(df_data), use_container_width=True, hide_index=True)

    # ════════════════════════════════════════════════════════════════════════
    # Tab 2: 圧縮性指数算出
    # ════════════════════════════════════════════════════════════════════════
    with tab2:
        st.subheader("圧縮性指数算出")
        st.markdown(
            "ケーキ比抵抗 α とろ過圧力 ΔP の log-log 回帰から圧縮性指数 n を算出します。  \n"
            "α = α₀ · ΔP^n　（n=0: 非圧縮性、n=1: 高圧縮性）"
        )
        st.divider()

        comp_mode = st.radio(
            "データ入力方法",
            ["Tab1 から自動取得", "手動入力"],
            horizontal=True, key="fi_comp_mode",
        )

        if comp_mode == "Tab1 から自動取得":
            cake_results = st.session_state.get("fi_cake_results")
            if not cake_results:
                st.info("先に Tab1 でケーキ比抵抗を計算してください。")
                dP_list_MPa, alpha_list = [], []
            else:
                valid = [(r.delta_P_Pa / 1e6, r.alpha_m_per_kg)
                         for r in cake_results if r is not None and r.alpha_m_per_kg > 0]
                if not valid:
                    st.warning("有効なケーキ比抵抗データがありません。")
                    dP_list_MPa, alpha_list = [], []
                else:
                    dP_list_MPa = [v[0] for v in valid]
                    alpha_list = [v[1] for v in valid]
                    st.info(f"{len(valid)} 点のデータを取得しました。")
                    df_comp = pd.DataFrame({
                        "ΔP [MPaG]": dP_list_MPa,
                        "α [m/kg]": [_fmt_alpha(a) for a in alpha_list],
                    })
                    st.dataframe(df_comp, use_container_width=True, hide_index=True)
        else:
            st.markdown("**（ΔP [MPaG], α [m/kg]）を入力してください**")
            n_comp = st.number_input("データ点数", 2, 10, 3, step=1, key="fi_comp_n")
            dP_list_MPa, alpha_list = [], []
            for i in range(n_comp):
                cc1, cc2 = st.columns(2)
                dp_i = cc1.number_input(
                    f"ΔP #{i+1} [MPaG]", 0.001, 10.0, 0.1 * (i + 1),
                    format="%.3f", key=f"fi_comp_dP_{i}",
                )
                al_i = cc2.number_input(
                    f"α #{i+1} [m/kg]", 1e6, 1e16, 1e11,
                    format="%.3e", key=f"fi_comp_al_{i}",
                )
                dP_list_MPa.append(dp_i)
                alpha_list.append(al_i)

        if st.button("圧縮性指数を計算", key="run_fi_comp", type="primary"):
            if len(dP_list_MPa) < 2:
                st.error("データ点が 2 点以上必要です。")
            else:
                try:
                    comp_result = calc_compressibility(dP_list_MPa, alpha_list)
                    st.session_state["fi_comp_result"] = comp_result
                except ValueError as e:
                    st.error(str(e))

        comp_result = st.session_state.get("fi_comp_result")
        if comp_result is not None:
            st.divider()
            m1, m2, m3 = st.columns(3)
            m1.metric("圧縮性指数 n", f"{comp_result.n_compress:.3f}")
            m2.metric("α₀ [m/kg]", f"{comp_result.alpha0:.3e}")
            m3.metric("R²", f"{comp_result.r_squared:.4f}")

            if len(comp_result.log_dP) == 2:
                st.info("データ点が 2 点のため R² = 1.00 は参考値です（内挿）。")

            fig_comp = plot_compressibility(comp_result)
            st.plotly_chart(fig_comp, use_container_width=True)

    # ════════════════════════════════════════════════════════════════════════
    # Tab 3: ろ過時間推算
    # ════════════════════════════════════════════════════════════════════════
    with tab3:
        st.subheader("ろ過時間推算")
        st.divider()

        # α の入力元
        alpha_source = st.radio(
            "ケーキ比抵抗 α の入力元",
            ["Tab1 計算値を使用", "Tab2 圧縮性考慮（α = α₀·ΔP^n）", "手動入力"],
            horizontal=False, key="fi_alpha_src",
        )

        alpha_val = None

        if alpha_source == "Tab1 計算値を使用":
            cake_results = st.session_state.get("fi_cake_results")
            if not cake_results:
                st.info("先に Tab1 でケーキ比抵抗を計算してください。")
            else:
                valid_idx = [i for i, r in enumerate(cake_results) if r is not None and r.alpha_m_per_kg > 0]
                if not valid_idx:
                    st.warning("有効なケーキ比抵抗データがありません。")
                else:
                    options = {
                        f"点{i+1}: ΔP={cake_results[i].delta_P_Pa/1e6:.3f} MPaG  α={_fmt_alpha(cake_results[i].alpha_m_per_kg)} m/kg": i
                        for i in valid_idx
                    }
                    sel_label = st.selectbox("使用する圧力点", list(options.keys()), key="fi_t3_sel")
                    sel_idx = options[sel_label]
                    alpha_val = cake_results[sel_idx].alpha_m_per_kg
                    st.caption(f"α = {_fmt_alpha(alpha_val)} m/kg")

        elif alpha_source == "Tab2 圧縮性考慮（α = α₀·ΔP^n）":
            comp_result = st.session_state.get("fi_comp_result")
            if comp_result is None:
                st.info("先に Tab2 で圧縮性指数を計算してください。")
            else:
                dP_apply = st.number_input(
                    "適用するろ過圧力 ΔP [MPaG]", 0.001, 10.0, 0.1,
                    format="%.3f", key="fi_t3_dP_apply",
                )
                alpha_val = comp_result.alpha0 * (dP_apply * 1e6) ** comp_result.n_compress
                st.caption(
                    f"α = {comp_result.alpha0:.3e} × ({dP_apply:.3f}×10⁶)^{comp_result.n_compress:.3f} "
                    f"= {_fmt_alpha(alpha_val)} m/kg"
                )

        else:  # 手動入力
            alpha_val = st.number_input(
                "α [m/kg]", 1e6, 1e16, 1e11,
                format="%.3e", key="fi_t3_alpha_manual",
            )
            st.caption(f"α = {_fmt_alpha(alpha_val)} m/kg")

        st.divider()

        # ろ過方式
        fi_mode = st.radio(
            "ろ過方式", ["加圧ろ過", "遠心ろ過"],
            horizontal=True, key="fi_mode",
        )

        # 共通入力（粘度・ろ材抵抗・面積・ケーキ量・ろ液量）
        st.markdown("**共通パラメータ**")
        fi_mu = st.number_input("粘度 μ [mPa·s]", 0.01, 10000.0, 1.0, format="%.3f", key="fi_t3_mu")
        fi_Rm = st.number_input("ろ材抵抗 Rm [m⁻¹]", 0.0, 1e15, 0.0, format="%.3e", key="fi_t3_Rm")
        fi_A = st.number_input("フィルター面積 A [m²]", 0.00001, 100.0, 0.01, format="%.5f", key="fi_t3_A")
        fi_m_cake = st.number_input("乾燥ケーキ質量 [g]", 0.001, 1e7, 100.0, format="%.3f", key="fi_t3_m_cake")
        fi_V_total = st.number_input("総ろ液量 [L]", 0.001, 1e6, 10.0, format="%.3f", key="fi_t3_V_total")

        # 方式別入力
        if fi_mode == "加圧ろ過":
            st.markdown("**加圧ろ過パラメータ**")
            fi_dP = st.number_input(
                "ろ過圧力 ΔP [MPaG]", 0.001, 10.0, 0.1,
                format="%.3f", key="fi_t3_dP",
            )
        else:
            st.markdown("**遠心ろ過パラメータ**")
            fi_RPM = st.number_input("RPM", 100.0, 50000.0, 3000.0, format="%.0f", key="fi_t3_RPM")
            fi_r_in = st.number_input("内半径 [m]", 0.001, 5.0, 0.05, format="%.3f", key="fi_t3_r_in")
            fi_r_out = st.number_input("外半径 [m]", 0.001, 5.0, 0.15, format="%.3f", key="fi_t3_r_out")
            fi_rho = st.number_input("液密度 ρ [g/mL]", 0.3, 3.0, 1.0, format="%.3f", key="fi_t3_rho")

            if fi_r_out > fi_r_in:
                omega = 2 * math.pi * fi_RPM / 60.0
                dP_eq = fi_rho * 1000.0 * omega ** 2 * (fi_r_out ** 2 - fi_r_in ** 2) / 2.0
                st.info(f"等価差圧 ΔP_eq = {dP_eq/1e6:.4f} MPaG")

        if st.button("ろ過時間を計算", key="run_fi_time", type="primary"):
            if alpha_val is None or alpha_val <= 0:
                st.error("ケーキ比抵抗 α を先に設定してください。")
            else:
                try:
                    if fi_mode == "加圧ろ過":
                        result = calc_filtration_time_pressure(
                            delta_P_MPaG=fi_dP,
                            mu_mPas=fi_mu,
                            alpha_m_per_kg=alpha_val,
                            Rm_m_inv=fi_Rm,
                            A_m2=fi_A,
                            m_cake_g=fi_m_cake,
                            V_total_L=fi_V_total,
                        )
                    else:
                        result = calc_filtration_time_centrifuge(
                            RPM=fi_RPM,
                            r_inner_m=fi_r_in,
                            r_outer_m=fi_r_out,
                            rho_g_mL=fi_rho,
                            mu_mPas=fi_mu,
                            alpha_m_per_kg=alpha_val,
                            Rm_m_inv=fi_Rm,
                            A_m2=fi_A,
                            m_cake_g=fi_m_cake,
                            V_total_L=fi_V_total,
                        )
                    st.session_state["fi_time_result"] = result
                except (ValueError, Exception) as e:
                    st.error(f"計算エラー: {e}")

        time_result = st.session_state.get("fi_time_result")
        if time_result is not None:
            st.divider()

            for note in time_result.notes:
                st.info(note)

            total_min = time_result.total_time_s / 60.0
            st.metric("合計ろ過時間", f"{total_min:.2f} min")

            fig_fi = plot_filtration_curve(time_result)
            st.plotly_chart(fig_fi, use_container_width=True)

            with st.expander("計算詳細"):
                n_show = min(50, len(time_result.t_s))
                step = max(1, len(time_result.t_s) // n_show)
                df_fi = pd.DataFrame({
                    "時間 [min]": [t / 60.0 for t in time_result.t_s[::step]],
                    "累積ろ液量 [L]": [v * 1e3 for v in time_result.V_m3[::step]],
                })
                st.dataframe(
                    df_fi.style.format({"時間 [min]": "{:.2f}", "累積ろ液量 [L]": "{:.3f}"}),
                    use_container_width=True,
                )
