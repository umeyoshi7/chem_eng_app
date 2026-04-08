from __future__ import annotations
import os
import sys

import streamlit as st

# ── サブモジュールをパスに追加 ────────────────────────────────────────────────
_HT_DIR = os.path.join(os.path.dirname(__file__), "heat_transfer")
# remove → insert(0) で確実に先頭に置く（他モジュールが先にパスを追加済みの場合も対応）
if _HT_DIR in sys.path:
    sys.path.remove(_HT_DIR)
sys.path.insert(0, _HT_DIR)

for _key in list(sys.modules.keys()):
    if _key == "src" or _key.startswith("src."):
        del sys.modules[_key]

from src.models import ReactorSpec
from src.reactor_db import get_reactor_spec, list_tag_nos
from src.geometry import calc_geometry
from src.heat_calc import (
    simulate_inner_control,
    simulate_outer_control,
    simulate_addition,
)
from src.plotting import plot_temperature_profile

# ── 溶媒 DB ──────────────────────────────────────────────────────────────────
_APP_DIR = os.path.dirname(__file__)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
from solvents import ALL_SOLVENTS, get_solvent_by_name

_SOLVENT_NAMES = [s["name"] for s in ALL_SOLVENTS]

_U_FACTOR = 3.6  # W/(m²·K) → kJ/(m²·h·K): 1 W/(m²·K) = 3.6 kJ/(m²·h·K)


# ── セッション初期化 ──────────────────────────────────────────────────────────

def _init_state() -> None:
    defaults = {
        # 計算結果キャッシュ
        "ht_reactor_spec": None,
        "ht_geo": None,
        "ht_sim_result_1": None,
        "ht_sim_result_2": None,
        # Widget デフォルト（ページ切り替え後も値を保持するために明示的に初期化）
        "ht_input_method": "データベースから選択",
        "ht_manual_tag": "手動入力",
        "ht_manual_U": 900.0,
        "ht_manual_vol": 200.0,
        "ht_manual_D": 0.70,
        "ht_manual_mirror": "ED",
        "ht_density": 1.0,
        "ht_main_cp_mode": "溶媒選択",
        "ht_ctrl_mode": "内温制御",
        "ht_T0": 20.0,
        "ht_T_target": 60.0,
        "ht_dT_offset": 20.0,
        "ht_T_jacket_fixed": 80.0,
        "ht_t_end_outer": 0.0,
        "ht_add_mode": "連続添加",
        "ht_add_T0": 20.0,
        "ht_add_Tjk": 20.0,
        "ht_add_Treag": 20.0,
        "ht_add_mass": 1000.0,
        "ht_add_Qrxn": 0.0,
        "ht_add_time": 60.0,
        "ht_reagent_cp_mode": "溶媒選択",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── cp 入力ウィジェット（共通） ───────────────────────────────────────────────

def _cp_input(key_prefix: str, label: str = "比熱入力") -> float:
    """溶媒選択または手動入力で cp [J/(g·K)] を返す。"""
    mode = st.radio(f"{label}方法", ["溶媒選択", "手動入力"],
                    horizontal=True, key=f"{key_prefix}_cp_mode")
    if mode == "溶媒選択":
        name = st.selectbox("溶媒", _SOLVENT_NAMES, key=f"{key_prefix}_solvent")
        s = get_solvent_by_name(name, ALL_SOLVENTS)
        cp = s.get("cp", 2.0)
        st.caption(f"cp = {cp:.2f} J/(g·K)")
        return cp
    else:
        return st.number_input("cp [J/(g·K)]", 0.1, 10.0, 2.0,
                               format="%.2f", key=f"{key_prefix}_cp_manual")


# ── メイン描画 ────────────────────────────────────────────────────────────────

def render() -> None:
    _init_state()
    _col_hdr, _col_rst = st.columns([9, 1])
    with _col_hdr:
        st.title("伝熱計算")
    with _col_rst:
        st.write("")
        if st.button("リセット", key="ht_reset_btn"):
            for _k in list(st.session_state.keys()):
                if _k.startswith("ht_"):
                    del st.session_state[_k]
            st.rerun()

    # ════════════════════════════════════════════════════════════════════════
    # Section 1: 反応器選択
    # ════════════════════════════════════════════════════════════════════════
    st.subheader("反応器仕様")
    input_method = st.radio("入力方法", ["データベースから選択", "手動入力"],
                            horizontal=True, key="ht_input_method")

    if input_method == "データベースから選択":
        try:
            tags = list_tag_nos()
        except Exception as e:
            st.error(f"DB 読み込みエラー: {e}")
            return

        selected_tag = st.selectbox("Tag No.", tags, key="ht_tag_select")
        try:
            reactor_base = get_reactor_spec(selected_tag)
        except Exception as e:
            st.error(str(e))
            return

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Tag No.", reactor_base.tag_no)
        c2.metric("U [kJ/(m²·h·K)]", f"{reactor_base.U * _U_FACTOR:.0f}")
        c3.metric("容量 [L]", f"{reactor_base.volume_L:.0f}")
        c4.metric("直径 [m]", f"{reactor_base.diameter_m:.2f}")
        c5.metric("鏡形状", reactor_base.mirror_type)

        reactor = ReactorSpec(reactor_base.tag_no, reactor_base.U, reactor_base.volume_L,
                              reactor_base.diameter_m, reactor_base.mirror_type)

    else:
        tag_no = st.text_input("Tag No.", value="手動入力", key="ht_manual_tag")
        mr1, mr2 = st.columns(2)
        U_kJ_h = mr1.number_input("U [kJ/(m²·h·K)]", 36.0, 7200.0, 900.0,
                                   step=36.0, key="ht_manual_U")
        vol_L = mr2.number_input("容量 [L]", 1.0, 100000.0, 200.0, key="ht_manual_vol")
        mr3, mr4 = st.columns(2)
        D_m = mr3.number_input("内径 [m]", 0.1, 10.0, 0.70,
                                format="%.2f", key="ht_manual_D")
        mirror = mr4.selectbox("鏡形状", ["ED", "SD"], key="ht_manual_mirror")
        U_val = U_kJ_h / _U_FACTOR
        reactor = ReactorSpec(tag_no, U_val, vol_L, D_m, mirror)

    st.divider()

    # ════════════════════════════════════════════════════════════════════════
    # Section 2: 内容液設定
    # ════════════════════════════════════════════════════════════════════════
    st.subheader("内容液設定")

    col_l1, col_l2 = st.columns(2)
    V_liq = col_l1.number_input(
        "仕込み液量 [L]", 0.1, reactor.volume_L * 1.5,
        min(reactor.volume_L * 0.7, reactor.volume_L),
        format="%.1f", key="ht_V_liq",
    )
    density = col_l2.number_input("液密度 [g/mL]", 0.3, 3.0, 1.0,
                                   format="%.3f", key="ht_density")
    mass_g = V_liq * density * 1000.0
    st.caption(f"液質量: {mass_g/1000:.2f} kg")

    cp_main = _cp_input("ht_main", label="液 cp")

    # 仕込み超過チェック & 伝面計算
    if V_liq > reactor.volume_L:
        st.error(
            f"仕込み量 {V_liq:.1f} L が機器容量 {reactor.volume_L:.1f} L を超えています。"
            " 計算できません。"
        )
        return

    try:
        geo = calc_geometry(reactor, V_liq)
    except ValueError as e:
        st.error(str(e))
        return

    cg1, cg2, cg3 = st.columns(3)
    cg1.metric("伝熱面積 A", f"{geo.A_total:.3f} m²")
    cg2.metric("液高さ", f"{geo.h_liquid_m:.3f} m")
    cg3.metric("充填率", f"{V_liq / reactor.volume_L * 100:.1f} %")

    st.divider()

    # ════════════════════════════════════════════════════════════════════════
    # Section 3: 計算モード
    # ════════════════════════════════════════════════════════════════════════
    tab1, tab2 = st.tabs(["機能① 昇降温シミュレーション", "機能② 添加シミュレーション"])

    # ────────────────────────────────────────────────────────────────────────
    # 機能①
    # ────────────────────────────────────────────────────────────────────────
    with tab1:
        control_mode = st.radio("制御方式", ["内温制御", "外温制御"],
                                horizontal=True, key="ht_ctrl_mode")

        ct1, ct2 = st.columns(2)
        T0 = ct1.number_input("初期内温 [°C]", -50.0, 300.0, 20.0,
                               format="%.1f", key="ht_T0")

        T_target_for_plot: float | None = None

        if control_mode == "内温制御":
            T_target = ct2.number_input("目標内温 [°C]", -50.0, 300.0, 60.0,
                                        format="%.1f", key="ht_T_target")
            dT_offset = st.number_input(
                "ジャケット温度オフセット ΔT [K]", -150.0, 150.0, 20.0,
                format="%.1f", key="ht_dT_offset",
                help="正=加熱（T_jacket = T_inner + ΔT）、負=冷却",
            )

            if st.button("計算実行", key="run_inner", type="primary"):
                with st.spinner("計算中..."):
                    result = simulate_inner_control(
                        reactor, geo, T0, T_target, dT_offset, mass_g, cp_main
                    )
                st.session_state["ht_sim_result_1"] = result

            T_target_for_plot = T_target

        else:  # 外温制御
            T_jacket_fixed = ct2.number_input("ジャケット設定温 [°C]", -50.0, 300.0, 80.0,
                                              format="%.1f", key="ht_T_jacket_fixed")
            t_end_input = st.number_input(
                "シミュレーション時間 [min]（0 で自動: 5τ）",
                0.0, 1440.0, 0.0, format="%.1f", key="ht_t_end_outer",
            )
            t_end_s = t_end_input * 60.0 if t_end_input > 0 else None

            if st.button("計算実行", key="run_outer", type="primary"):
                with st.spinner("計算中..."):
                    result = simulate_outer_control(
                        reactor, geo, T0, T_jacket_fixed, mass_g, cp_main, t_end_s
                    )
                st.session_state["ht_sim_result_1"] = result

        # 結果表示
        res1 = st.session_state.get("ht_sim_result_1")
        if res1 and res1.mode in ("内温制御", "外温制御"):
            for note in res1.notes:
                st.warning(note)

            if res1.mode == "内温制御":
                rr1, rr2, rr3 = st.columns(3)
                t_min = (res1.t_target_s or 0) / 60.0
                rr1.metric("到達時間", f"{t_min:.1f} min")
                rate = res1.heating_rate_K_per_min or 0.0
                rr2.metric("昇降温速度", f"{rate:.2f} K/min")
                rr3.metric("伝熱面積", f"{geo.A_total:.3f} m²")
                T_target_for_plot = (
                    T_target if "T_target" in dir() else None  # type: ignore[name-defined]
                )
            else:
                rr1, rr2, rr3 = st.columns(3)
                tau_min = (res1.tau_s or 0) / 60.0
                rr1.metric("時定数 τ", f"{tau_min:.1f} min")
                rr2.metric("3τ (95% 到達)", f"{3 * tau_min:.1f} min")
                rr3.metric("伝熱面積", f"{geo.A_total:.3f} m²")

            fig = plot_temperature_profile(
                res1,
                title=f"昇降温プロファイル ({res1.mode})",
                T_target_C=T_target_for_plot if res1.mode == "内温制御" else None,
            )
            st.plotly_chart(fig, use_container_width=True)

    # ────────────────────────────────────────────────────────────────────────
    # 機能②
    # ────────────────────────────────────────────────────────────────────────
    with tab2:
        add_mode_label = st.radio("添加方式", ["連続添加", "一括添加"],
                                  horizontal=True, key="ht_add_mode")
        add_mode = "continuous" if add_mode_label == "連続添加" else "batch"

        ca1, ca2 = st.columns(2)
        T0_add = ca1.number_input("初期内温 [°C]", -50.0, 300.0, 20.0,
                                   format="%.1f", key="ht_add_T0")
        T_jk_add = ca2.number_input("ジャケット温 [°C]", -50.0, 300.0, 20.0,
                                     format="%.1f", key="ht_add_Tjk")
        ca3, ca4 = st.columns(2)
        T_reagent = ca3.number_input("試薬温度 [°C]", -50.0, 300.0, 20.0,
                                     format="%.1f", key="ht_add_Treag")
        mass_reagent = ca4.number_input("添加量 [g]", 0.1, 1e7, 1000.0,
                                        format="%.1f", key="ht_add_mass")

        cb1, cb2 = st.columns(2)
        Q_rxn_per_kg = cb1.number_input(
            "反応熱 [kJ/kg]", -10000.0, 10000.0, 0.0,
            format="%.1f", key="ht_add_Qrxn",
            help="発熱=正値、吸熱=負値。反応液全体（仕込み液＋添加試薬）1kgあたりの発熱量",
        )

        t_add_s = 0.0
        if add_mode == "continuous":
            t_add_min = cb2.number_input("添加時間 [min]", 0.1, 1440.0, 60.0,
                                         format="%.1f", key="ht_add_time")
            t_add_s = t_add_min * 60.0

        cp_reagent = _cp_input("ht_reagent", label="試薬 cp")

        if st.button("計算実行", key="run_add", type="primary"):
            m_total_kg = (mass_g + mass_reagent) / 1000.0
            Q_rxn_total_kJ = Q_rxn_per_kg * m_total_kg
            with st.spinner("計算中..."):
                result2 = simulate_addition(
                    reactor=reactor,
                    geo=geo,
                    T0_inner_C=T0_add,
                    T_jacket_C=T_jk_add,
                    T_reagent_C=T_reagent,
                    mass_initial_g=mass_g,
                    cp_initial=cp_main,
                    mass_reagent_g=mass_reagent,
                    cp_reagent=cp_reagent,
                    Q_rxn_total_kJ=Q_rxn_total_kJ,
                    addition_mode=add_mode,
                    t_addition_s=t_add_s,
                )
            st.session_state["ht_sim_result_2"] = result2

        res2 = st.session_state.get("ht_sim_result_2")
        if res2 and res2.mode == "添加":
            for note in res2.notes:
                if note.startswith("警告"):
                    st.warning(note)
                elif note.startswith("ODE"):
                    st.error(note)
                else:
                    st.info(note)

            T_max = max(res2.T_inner)
            T_min = min(res2.T_inner)
            rm1, rm2 = st.columns(2)
            rm1.metric("最高内温", f"{T_max:.1f} °C")
            rm2.metric("最低内温", f"{T_min:.1f} °C")

            fig2 = plot_temperature_profile(
                res2,
                title=f"添加中温度プロファイル ({add_mode_label})",
            )
            # 連続添加の場合、添加終了時刻に縦線を追加
            if add_mode == "continuous" and t_add_s > 0:
                t_add_min_val = t_add_s / 60.0
                t_max_plot = max(res2.t_s) / 60.0
                if t_add_min_val <= t_max_plot:
                    fig2.add_vline(
                        x=t_add_min_val,
                        line_dash="dot",
                        line_color="orange",
                        annotation_text="添加終了",
                        annotation_position="top right",
                    )
            st.plotly_chart(fig2, use_container_width=True)
