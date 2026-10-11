"""プロセス時間系ツール: 機器DB / 伝熱（昇降温・添加）/ 濃縮時間 / ろ過"""

from __future__ import annotations

import math

import plotly.graph_objects as go

from .common import (
    AMOUNT_UNIT, REACTOR_MANUAL, SOLVENT_AMOUNTS, ToolError, equipment_repo, fmt_min,
    get_filter_area, get_reactor, interp, mix_volume_L, num, parse_solvent_amounts, r,
    resolve_solvent, string, tool,
)


@tool(
    "list_equipment",
    "機器データベース（反応槽・フィルター）の一覧と仕様（U値・容量・内径・鏡形状・ろ過面積・種別）を返す。",
    {},
)
def list_equipment(ws) -> dict:
    repo = equipment_repo()
    reactors, filters = [], []
    for item in repo.list_all():
        if item.equip_type == "反応槽":
            sp = repo.get_reactor_spec(item.tag_no)
            reactors.append({"tag_no": sp.tag_no, "U_kJ_m2hK": r(sp.U * 3.6), "容量_L": sp.volume_L,
                             "内径_m": sp.diameter_m, "鏡形状": sp.mirror_type})
        else:
            fs = repo.get_filter_spec(item.tag_no)
            filters.append({"tag_no": fs.tag_no, "面積_m2": fs.area_m2, "種別": fs.filter_type})
    return {"反応槽": reactors, "フィルター": filters}


# ---------------------------------------------------------------------------
# 伝熱
# ---------------------------------------------------------------------------

def _liquid_props(solvent: str | None, density, cp, T_C: float) -> tuple[float, float, str]:
    """溶媒名から密度・比熱を補完する。明示値が優先。"""
    src = []
    if solvent:
        from .common import liquid_density
        s = resolve_solvent(solvent)
        if density is None:
            density = liquid_density(s, T_C)
            src.append(f"密度は {s['name']} の物性値")
        if cp is None:
            cp = s["cp"]
            src.append(f"比熱は {s['name']} の物性値")
    if density is None:
        density = 1.0
        src.append("密度は既定値 1.0 g/mL")
    if cp is None:
        cp = 2.0
        src.append("比熱は既定値 2.0 J/(g·K)")
    return float(density), float(cp), "、".join(src)


def heat_time(reactor, liquid_volume_L: float, density: float, cp: float,
              T0: float, T_target: float, dT_offset: float | None = None):
    """内温制御の昇降温時間計算（タイムテーブルからも利用）。"""
    from heat_transfer.src.geometry import calc_geometry
    from heat_transfer.src.heat_calc import simulate_inner_control
    if dT_offset is None:
        dT_offset = 20.0 if T_target >= T0 else -20.0
    try:
        geo = calc_geometry(reactor, liquid_volume_L)
    except ValueError as e:
        raise ToolError(str(e)) from e
    res = simulate_inner_control(reactor, geo, T0, T_target, dT_offset,
                                 liquid_volume_L * density * 1000.0, cp)
    return geo, res


@tool(
    "heat_transfer",
    "反応釜の伝熱計算。mode=inner: 内温制御（ジャケット温=内温+ΔT）で目標温度までの昇温/冷却時間、"
    "mode=outer: ジャケット温度一定での内温推移（目標温度到達時間）、"
    "mode=addition: 試薬添加時の内温変化（反応熱・顕熱を考慮）。温度プロファイルのグラフを出力する。",
    {
        "mode": string("計算モード", ["inner", "outer", "addition"]),
        "reactor_tag": string("反応槽 Tag No.（例: R-102）"),
        "reactor_manual": REACTOR_MANUAL,
        "liquid_volume_L": num("仕込み液量 [L]"),
        "solvent": string("内容液の溶媒名（密度・比熱を物性DBから補完）"),
        "density_g_mL": num("液密度 [g/mL]（省略時は solvent から、なければ 1.0）"),
        "cp_J_gK": num("比熱 [J/(g·K)]（省略時は solvent から、なければ 2.0）"),
        "initial_temp_C": num("初期内温 [°C]"),
        "target_temp_C": num("目標内温 [°C]（inner は必須、outer は到達時間の算出に使用）"),
        "dT_offset_K": num("inner: ジャケット温度オフセット [K]（既定 昇温+20 / 冷却-20）"),
        "jacket_temp_C": num("outer/addition: ジャケット温度 [°C]"),
        "duration_min": num("outer/addition: シミュレーション時間 [min]（省略時は自動）"),
        "reagent_mass_g": num("addition: 添加試薬の質量 [g]"),
        "reagent_cp_J_gK": num("addition: 試薬の比熱 [J/(g·K)]（既定 2.0）"),
        "reagent_temp_C": num("addition: 試薬温度 [°C]"),
        "reaction_heat_kJ": num("addition: 総反応熱 [kJ]（発熱を正、既定 0）"),
        "addition_mode": string("addition: 添加方法", ["continuous", "batch"]),
        "addition_time_min": num("addition: 連続添加の添加時間 [min]"),
    },
    ["mode", "liquid_volume_L", "initial_temp_C"],
)
def heat_transfer(ws, mode: str, liquid_volume_L: float, initial_temp_C: float,
                  reactor_tag=None, reactor_manual=None, solvent=None, density_g_mL=None,
                  cp_J_gK=None, target_temp_C=None, dT_offset_K=None, jacket_temp_C=None,
                  duration_min=None, reagent_mass_g=None, reagent_cp_J_gK=2.0,
                  reagent_temp_C=None, reaction_heat_kJ=0.0, addition_mode="continuous",
                  addition_time_min=None) -> dict:
    from heat_transfer.src.geometry import calc_geometry
    from heat_transfer.src.heat_calc import simulate_addition, simulate_outer_control, tau_seconds
    from heat_transfer.src.plotting import plot_temperature_profile

    reactor = get_reactor(reactor_tag, reactor_manual)
    density, cp, prop_note = _liquid_props(solvent, density_g_mL, cp_J_gK, initial_temp_C)
    mass_g = liquid_volume_L * density * 1000.0
    out: dict = {"反応槽": reactor.tag_no, "U_kJ_m2hK": r(reactor.U * 3.6),
                 "液量_L": liquid_volume_L, "密度_g_mL": r(density), "cp_J_gK": r(cp)}
    if prop_note:
        out["物性の前提"] = prop_note

    if mode == "inner":
        if target_temp_C is None:
            raise ToolError("inner モードでは target_temp_C が必要です。")
        geo, res = heat_time(reactor, liquid_volume_L, density, cp,
                             initial_temp_C, target_temp_C, dT_offset_K)
        out.update({
            "伝熱面積_m2": r(geo.A_total),
            "ΔT_offset_K": r(res.T_jacket[0] - res.T_inner[0]),
            "所要時間_min": r(res.t_target_s / 60.0) if res.t_target_s else None,
            "所要時間": fmt_min(res.t_target_s / 60.0) if res.t_target_s else None,
            "昇降温速度_K_min": r(res.heating_rate_K_per_min),
        })
        title = f"内温制御 {initial_temp_C:g}→{target_temp_C:g}°C"
    elif mode == "outer":
        if jacket_temp_C is None:
            raise ToolError("outer モードでは jacket_temp_C が必要です。")
        try:
            geo = calc_geometry(reactor, liquid_volume_L)
        except ValueError as e:
            raise ToolError(str(e)) from e
        t_end = duration_min * 60.0 if duration_min else None
        res = simulate_outer_control(reactor, geo, initial_temp_C, jacket_temp_C, mass_g, cp, t_end)
        tau = tau_seconds(reactor.U, geo.A_total, mass_g, cp)
        out.update({"伝熱面積_m2": r(geo.A_total), "時定数τ_min": r(tau / 60.0),
                    "最終内温_C": r(res.T_inner[-1])})
        if target_temp_C is not None:
            ratio = (target_temp_C - jacket_temp_C) / (initial_temp_C - jacket_temp_C) \
                if initial_temp_C != jacket_temp_C else -1
            if 0 < ratio < 1:
                t_min = -tau * math.log(ratio) / 60.0
                out["目標温度到達時間_min"] = r(t_min)
                out["目標温度到達時間"] = fmt_min(t_min)
            else:
                out["目標温度到達時間_min"] = None
                out["注意"] = "ジャケット温度一定では目標温度に到達しません（漸近値はジャケット温度）。"
        title = f"外温制御 ジャケット {jacket_temp_C:g}°C"
    elif mode == "addition":
        if jacket_temp_C is None or reagent_mass_g is None or reagent_temp_C is None:
            raise ToolError("addition モードでは jacket_temp_C・reagent_mass_g・reagent_temp_C が必要です。")
        if addition_mode == "continuous" and not addition_time_min:
            raise ToolError("連続添加では addition_time_min が必要です。")
        try:
            geo = calc_geometry(reactor, liquid_volume_L)
        except ValueError as e:
            raise ToolError(str(e)) from e
        res = simulate_addition(
            reactor, geo, initial_temp_C, jacket_temp_C, reagent_temp_C, mass_g, cp,
            reagent_mass_g, reagent_cp_J_gK, reaction_heat_kJ or 0.0, addition_mode,
            (addition_time_min or 0.0) * 60.0, duration_min * 60.0 if duration_min else None,
        )
        i_max = max(range(len(res.T_inner)), key=lambda i: res.T_inner[i])
        i_min = min(range(len(res.T_inner)), key=lambda i: res.T_inner[i])
        out.update({
            "伝熱面積_m2": r(geo.A_total),
            "最高内温_C": r(res.T_inner[i_max]), "最高内温到達_min": r(res.t_s[i_max] / 60.0),
            "最低内温_C": r(res.T_inner[i_min]), "最終内温_C": r(res.T_inner[-1]),
            "計算終了_min": r(res.t_s[-1] / 60.0),
        })
        title = f"添加シミュレーション（{'連続' if addition_mode == 'continuous' else '一括'}）"
    else:
        raise ToolError(f"未対応の mode: {mode}")

    if res.notes:
        out["notes"] = res.notes
    fig = plot_temperature_profile(res, title=title,
                                   T_target_C=target_temp_C if mode == "inner" else None)
    out["graph"] = ws.add_figure(title, fig)
    return out


# ---------------------------------------------------------------------------
# 濃縮時間
# ---------------------------------------------------------------------------

def concentration_time_core(sols, moles, pressure_kPa, reactor, jacket_temp_C,
                            target_evap_fraction, temperature_ref_C=25.0) -> dict:
    """レイリー蒸留 + 伝熱で濃縮時間を推算する（タイムテーブルからも利用）。"""
    from ui_conc_time import _calc_concentration_time
    from .properties import run_rayleigh

    V0 = mix_volume_L(sols, moles, temperature_ref_C)
    if V0 > reactor.volume_L:
        raise ToolError(f"仕込み液量 {V0:.1f} L が反応槽 {reactor.tag_no} の容量 "
                        f"{reactor.volume_L:.0f} L を超えています。")
    if not 0 < target_evap_fraction < 1:
        raise ToolError("target_evap_fraction は 0〜1 の範囲で指定してください。")
    ray = run_rayleigh(sols, moles, pressure_kPa, n_steps=50)
    tres = _calc_concentration_time(ray, sols, reactor, jacket_temp_C)
    t_target = interp(target_evap_fraction, tres["evap_fraction"], tres["time_min"])
    return {"rayleigh": ray, "time": tres, "V0_L": V0, "t_target_min": t_target}


@tool(
    "concentration_time",
    "反応槽での濃縮（蒸発）所要時間を推算する。レイリー蒸留で沸点・留出組成の変化を計算し、"
    "ジャケット温度・U値・液量から決まる伝熱面積で蒸発速度を求めて目標蒸発率までの時間を積算する。",
    {
        "components": SOLVENT_AMOUNTS,
        "unit": AMOUNT_UNIT,
        "pressure_kPa": num("操作圧力 [kPa abs]（減圧濃縮なら減圧度）"),
        "reactor_tag": string("反応槽 Tag No."),
        "reactor_manual": REACTOR_MANUAL,
        "jacket_temp_C": num("ジャケット温度 [°C]"),
        "target_evap_fraction": num("目標蒸発率（モル基準 0〜1、例: 0.8 = 80% 留去）"),
        "temperature_ref_C": num("体積→モル換算の液温 [°C]（既定 25）"),
    },
    ["components", "unit", "pressure_kPa", "jacket_temp_C", "target_evap_fraction"],
)
def concentration_time(ws, components: list, unit: str, pressure_kPa: float, jacket_temp_C: float,
                       target_evap_fraction: float, reactor_tag=None, reactor_manual=None,
                       temperature_ref_C: float = 25.0) -> dict:
    sols, moles = parse_solvent_amounts(components, unit, temperature_ref_C, n_min=1, n_max=4)
    if len(sols) == 1:
        raise ToolError("濃縮時間推算は 2 成分以上が必要です（単一溶媒の場合は微量の第2成分を加えてください）。")
    reactor = get_reactor(reactor_tag, reactor_manual)
    core = concentration_time_core(sols, moles, pressure_kPa, reactor, jacket_temp_C,
                                   target_evap_fraction, temperature_ref_C)
    tres, ray = core["time"], core["rayleigh"]
    t = core["t_target_min"]
    out: dict = {
        "反応槽": reactor.tag_no, "初期液量_L": r(core["V0_L"]), "圧力_kPa": pressure_kPa,
        "ジャケット温度_C": jacket_temp_C, "目標蒸発率": target_evap_fraction,
        "所要時間_min": r(t), "所要時間": fmt_min(t) if t is not None else None,
        "初期沸点_C": r(ray["T_bp"][0]) if ray["T_bp"] else None,
        "目標時沸点_C": r(interp(target_evap_fraction, ray["evap_fraction"], ray["T_bp"])),
        "推移": [{"蒸発率": f, "経過_min": r(interp(f, tres["evap_fraction"], tres["time_min"]))}
                for f in (0.2, 0.4, 0.6, 0.8, 0.9) if f <= target_evap_fraction + 1e-9],
    }
    if t is None:
        reached = max((f for f, T in zip(ray["evap_fraction"], ray["T_bp"]) if T is not None), default=0.0)
        out["注意"] = (f"レイリー蒸留計算が蒸発率 {reached:.2f} までしか収束しなかったため、"
                     "目標蒸発率までの時間を算出できませんでした（平衡計算エンジンの収束限界）。"
                     "目標蒸発率を下げるか条件を変えてください。")
    if tres["jacket_too_cold"]:
        out["警告"] = "ジャケット温度が沸点以下になる区間があり、その区間は蒸発が進みません。ジャケット温度を上げるか減圧してください。"
    if tres["hvap_fallback_used"]:
        out["注意(蒸発熱)"] = "一部ステップで蒸発熱の相関式が使えず 40 kJ/mol で代用しました。"

    fig = go.Figure(go.Scatter(x=tres["time_min"], y=tres["evap_fraction"], mode="lines", name="蒸発率"))
    if t is not None:
        fig.add_vline(x=t, line_dash="dot", annotation_text=f"{fmt_min(t)}")
    fig.update_layout(title=f"濃縮時間プロファイル（{reactor.tag_no}, ジャケット {jacket_temp_C:g}°C）",
                      xaxis_title="時間 [min]", yaxis_title="蒸発率 [-]")
    out["graph"] = ws.add_figure("濃縮時間プロファイル", fig)
    return out


# ---------------------------------------------------------------------------
# ろ過
# ---------------------------------------------------------------------------

def _area(area_m2, filter_tag) -> tuple[float, str]:
    if filter_tag:
        a, typ = get_filter_area(filter_tag)
        return a, f"{filter_tag} ({typ}, {a} m²)"
    if area_m2:
        return float(area_m2), f"{area_m2} m²（手動）"
    raise ToolError("filter_tag（フィルター Tag No.）または area_m2 を指定してください。")


@tool(
    "filtration_cake_resistance",
    "一定速度ろ過の実測値（差圧・流量）から Ruth 式でケーキ比抵抗 α を算出する。"
    "2点以上の圧力データがあれば log-log 回帰で圧縮性指数 n と基準比抵抗 α0 も算出する。",
    {
        "measurements": {
            "type": "array", "description": "測定点のリスト",
            "items": {"type": "object", "properties": {
                "pressure_MPa": num("差圧 [MPaG]"), "flow_L_min": num("ろ液流量 [L/min]")},
                "required": ["pressure_MPa", "flow_L_min"]},
        },
        "viscosity_mPas": num("ろ液粘度 [mPa·s]"),
        "area_m2": num("ろ過面積 [m²]"),
        "filter_tag": string("フィルター Tag No.（面積を DB から取得）"),
        "dry_cake_mass_g": num("乾燥ケーキ質量 [g]"),
        "medium_resistance_m_inv": num("ろ材抵抗 Rm [1/m]（既定 1e10）"),
    },
    ["measurements", "viscosity_mPas", "dry_cake_mass_g"],
)
def filtration_cake_resistance(ws, measurements: list, viscosity_mPas: float, dry_cake_mass_g: float,
                               area_m2=None, filter_tag=None,
                               medium_resistance_m_inv: float = 1e10) -> dict:
    from filtration.src.calc import calc_cake_resistance, calc_compressibility
    from filtration.src.plotting import plot_compressibility

    A, a_desc = _area(area_m2, filter_tag)
    rows, dps, alphas = [], [], []
    for m in measurements:
        try:
            res = calc_cake_resistance(m["pressure_MPa"], m["flow_L_min"], viscosity_mPas, A,
                                       dry_cake_mass_g, medium_resistance_m_inv)
        except ValueError as e:
            raise ToolError(str(e)) from e
        rows.append({"差圧_MPa": m["pressure_MPa"], "流量_L_min": m["flow_L_min"],
                     "α_m_kg": r(res.alpha_m_per_kg), "notes": res.notes or None})
        dps.append(m["pressure_MPa"])
        alphas.append(res.alpha_m_per_kg)
    out: dict = {"ろ過面積": a_desc, "ケーキ比抵抗": rows}
    if len([a for a in alphas if a > 0]) >= 2 and len(set(dps)) >= 2:
        comp = calc_compressibility(dps, alphas)
        out["圧縮性"] = {"n": r(comp.n_compress), "α0_m_kg(ΔP=1Pa)": r(comp.alpha0),
                       "R2": r(comp.r_squared), "式": "α = α0 × (ΔP[Pa])^n"}
        out["graph"] = ws.add_figure("圧縮性指数", plot_compressibility(comp))
    return out


@tool(
    "filtration_time",
    "Ruth のろ過方程式で加圧ろ過または遠心ろ過の所要時間を推算し、累積ろ液量-時間曲線を出力する。"
    "α は直接指定、または α0 と圧縮性指数 n から α = α0·ΔP^n で計算する。",
    {
        "mode": string("ろ過方式", ["pressure", "centrifuge"]),
        "pressure_MPa": num("pressure: ろ過差圧 [MPaG]"),
        "rpm": num("centrifuge: 回転数 [rpm]"),
        "r_inner_m": num("centrifuge: 液面半径 [m]"),
        "r_outer_m": num("centrifuge: バスケット半径 [m]"),
        "density_g_mL": num("centrifuge: 液密度 [g/mL]（既定 1.0）"),
        "viscosity_mPas": num("ろ液粘度 [mPa·s]（既定 1.0）"),
        "alpha_m_kg": num("ケーキ比抵抗 α [m/kg]"),
        "alpha0": num("基準比抵抗 α0 [m/kg]（圧縮性考慮時）"),
        "n_compress": num("圧縮性指数 n（圧縮性考慮時）"),
        "medium_resistance_m_inv": num("ろ材抵抗 Rm [1/m]（既定 1e10）"),
        "area_m2": num("ろ過面積 [m²]"),
        "filter_tag": string("フィルター Tag No.（面積を DB から取得）"),
        "dry_cake_mass_g": num("乾燥ケーキ質量 [g]"),
        "filtrate_volume_L": num("総ろ液量 [L]"),
    },
    ["mode", "dry_cake_mass_g", "filtrate_volume_L"],
)
def filtration_time(ws, mode: str, dry_cake_mass_g: float, filtrate_volume_L: float,
                    pressure_MPa=None, rpm=None, r_inner_m=None, r_outer_m=None, density_g_mL=1.0,
                    viscosity_mPas=1.0, alpha_m_kg=None, alpha0=None, n_compress=None,
                    medium_resistance_m_inv=1e10, area_m2=None, filter_tag=None) -> dict:
    from filtration.src.calc import calc_filtration_time_centrifuge, calc_filtration_time_pressure
    from filtration.src.plotting import plot_filtration_curve

    A, a_desc = _area(area_m2, filter_tag)

    if mode == "pressure":
        if not pressure_MPa:
            raise ToolError("pressure モードでは pressure_MPa が必要です。")
        dP_Pa = pressure_MPa * 1e6
    elif mode == "centrifuge":
        if not (rpm and r_inner_m is not None and r_outer_m):
            raise ToolError("centrifuge モードでは rpm・r_inner_m・r_outer_m が必要です。")
        omega = 2 * math.pi * rpm / 60
        dP_Pa = density_g_mL * 1000 * omega ** 2 * (r_outer_m ** 2 - r_inner_m ** 2) / 2
    else:
        raise ToolError(f"未対応の mode: {mode}")

    if alpha_m_kg:
        alpha, a_src = float(alpha_m_kg), "指定値"
    elif alpha0 and n_compress is not None:
        alpha = alpha0 * dP_Pa ** n_compress
        a_src = f"α0={alpha0:.3e}, n={n_compress:.3f} から算出"
    else:
        raise ToolError("alpha_m_kg、または alpha0 と n_compress を指定してください。")

    try:
        if mode == "pressure":
            res = calc_filtration_time_pressure(pressure_MPa, viscosity_mPas, alpha,
                                                medium_resistance_m_inv, A, dry_cake_mass_g,
                                                filtrate_volume_L)
        else:
            res = calc_filtration_time_centrifuge(rpm, r_inner_m, r_outer_m, density_g_mL,
                                                  viscosity_mPas, alpha, medium_resistance_m_inv,
                                                  A, dry_cake_mass_g, filtrate_volume_L)
    except ValueError as e:
        raise ToolError(str(e)) from e

    t = res.total_time_s / 60.0
    out = {
        "方式": res.mode, "ろ過面積": a_desc, "実効差圧_MPa": r(res.delta_P_Pa / 1e6),
        "α_m_kg": r(alpha), "α の由来": a_src, "総ろ液量_L": filtrate_volume_L,
        "所要時間_min": r(t), "所要時間": fmt_min(t),
    }
    if res.notes:
        out["notes"] = res.notes
    out["graph"] = ws.add_figure("ろ過時間曲線", plot_filtration_curve(res))
    return out
