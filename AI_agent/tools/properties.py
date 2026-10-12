"""物性・平衡系ツール: 溶媒一覧 / 蒸気圧・沸点 / VLE / LLE 層分離 / 濃縮（レイリー蒸留）"""

from __future__ import annotations

from functools import lru_cache

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .common import (
    AMOUNT_UNIT, SOLVENT_AMOUNTS, ToolError, interp, num, parse_solvent_amounts,
    r, resolve_solvent, solvent_category, string, tool,
)
from core.solvents import ALL_SOLVENTS


@tool(
    "list_solvents",
    "計算に使える溶媒（63成分）の一覧と分子量・密度・比熱を返す。",
    {},
)
def list_solvents(ws) -> dict:
    from .common import liquid_density
    return {
        "solvents": [
            {"name": s["name"], "分類": solvent_category(s), "MW": s["mw"],
             "密度_25C_g_mL": r(liquid_density(s, 25.0)), "cp_J_gK": s["cp"]}
            for s in ALL_SOLVENTS
        ]
    }


# ---------------------------------------------------------------------------
# 蒸気圧・沸点
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _vp_func(thermo_id: str, offset_K: float):
    from thermo import ChemicalConstantsPackage
    from core.thermo_engine import _VaporPressureShifted
    _, props = ChemicalConstantsPackage.from_IDs([thermo_id])
    vp = props.VaporPressures[0]
    return _VaporPressureShifted(vp, offset_K) if offset_K else vp


def _boiling_point_C(vp, P_kPa: float) -> float | None:
    lo = (getattr(vp, "Tmin", None) or 150.0) + 0.01
    hi = (getattr(vp, "Tmax", None) or 700.0) - 0.01
    P = P_kPa * 1000.0
    try:
        if not (vp(lo) <= P <= vp(hi)):
            return None
    except Exception:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if vp(mid) < P:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0 - 273.15


@tool(
    "vapor_pressure",
    "純成分の蒸気圧曲線を計算する。指定温度での蒸気圧、指定圧力での沸点（減圧沸点など）、"
    "大気圧沸点を返し、蒸気圧曲線グラフを出力する。",
    {
        "solvent": string("溶媒名"),
        "temperatures_C": {"type": "array", "items": {"type": "number"},
                           "description": "蒸気圧を知りたい温度 [°C] のリスト"},
        "pressures_kPa": {"type": "array", "items": {"type": "number"},
                          "description": "沸点を知りたい圧力 [kPa abs] のリスト"},
        "T_min_C": num("グラフ下限温度 [°C]（既定 0）"),
        "T_max_C": num("グラフ上限温度 [°C]（既定 150）"),
    },
    ["solvent"],
)
def vapor_pressure(ws, solvent: str, temperatures_C=None, pressures_kPa=None,
                   T_min_C: float = 0.0, T_max_C: float = 150.0) -> dict:
    from core.thermo_engine import calc_vapor_pressure_curve

    s = resolve_solvent(solvent)
    tid = s.get("thermo_surrogate", s["thermo_id"])
    offset = s.get("vp_T_offset", 0.0)
    vp = _vp_func(tid, offset)

    temps = list(temperatures_C or [])
    press = list(pressures_kPa or [])
    # グラフ範囲を問い合わせ点が収まるよう広げる
    bps = {P: _boiling_point_C(vp, P) for P in press}
    span = temps + [t for t in bps.values() if t is not None]
    if span:
        T_min_C = min(T_min_C, min(span) - 10)
        T_max_C = max(T_max_C, max(span) + 10)

    curve = calc_vapor_pressure_curve(tid, T_min_C, T_max_C, T_offset_K=offset)

    out: dict = {
        "solvent": s["name"],
        "大気圧沸点_C": r(curve["T_bp_C"]) if curve["T_bp_C"] is not None
        else r(_boiling_point_C(vp, 101.325)),
        "相関式有効範囲_C": [r(curve["T_valid_min_C"]), r(curve["T_valid_max_C"])],
    }
    if temps:
        rows = []
        for t in temps:
            try:
                rows.append({"T_C": t, "P_kPa": r(vp(t + 273.15) / 1000.0)})
            except Exception as e:
                rows.append({"T_C": t, "P_kPa": None, "error": str(e)})
        out["蒸気圧"] = rows
    if press:
        out["沸点"] = [{"P_kPa": P, "T_bp_C": r(T)} for P, T in bps.items()]

    vmin, vmax = curve["T_valid_min_C"], curve["T_valid_max_C"]
    if (vmin is not None and T_min_C < vmin) or (vmax is not None and T_max_C > vmax):
        out["注意"] = "表示範囲の一部が相関式の有効範囲外です（外挿値）。"

    fig = go.Figure(go.Scatter(x=curve["T_C"], y=curve["P_kPa"], mode="lines", name=s["name"]))
    fig.add_hline(y=101.325, line_dash="dot", annotation_text="101.325 kPa")
    fig.update_layout(title=f"{s['name']} 蒸気圧曲線", xaxis_title="温度 [°C]",
                      yaxis_title="蒸気圧 [kPa]")
    out["graph"] = ws.add_figure(f"蒸気圧曲線_{s['name']}", fig)
    return out


# ---------------------------------------------------------------------------
# VLE
# ---------------------------------------------------------------------------

@tool(
    "vle_binary",
    "2成分系の気液平衡（UNIFAC Dortmund）を計算する。純成分沸点・共沸点の有無と組成/温度・"
    "不均一共沸（三相）・代表組成での泡点/露点を返し、xy 線図と T-xy 線図を出力する。",
    {
        "solvent1": string("成分1（グラフ横軸 x1 の成分）"),
        "solvent2": string("成分2"),
        "pressure_kPa": num("圧力 [kPa abs]（既定 101.325）"),
        "x1_query": {"type": "array", "items": {"type": "number"},
                     "description": "泡点・気相組成を知りたい液相モル分率 x1 のリスト（0〜1）"},
    },
    ["solvent1", "solvent2"],
)
def vle_binary(ws, solvent1: str, solvent2: str, pressure_kPa: float = 101.325,
               x1_query=None) -> dict:
    from core.thermo_engine import calc_vle_xy

    s1, s2 = resolve_solvent(solvent1), resolve_solvent(solvent2)
    if s1 is s2:
        raise ToolError("異なる2成分を指定してください。")
    res = calc_vle_xy([s1, s2], pressure_kPa, n=80)
    x1, y1, Tb, Td = res["x1"], res["y1"], res["T_bubble_C"], res["T_dew_C"]

    out: dict = {
        "系": f"{s1['name']}(1) / {s2['name']}(2)",
        "圧力_kPa": pressure_kPa,
        f"{s1['name']}_沸点_C": r(Tb[-1]),
        f"{s2['name']}_沸点_C": r(Tb[0]),
    }

    # 共沸点: y1 - x1 の符号反転（内部組成）
    azeos = []
    pts = [(x, y, T) for x, y, T in zip(x1, y1, Tb) if y is not None and T is not None and 0 < x < 1]
    for (xa, ya, Ta), (xb, yb, Tb_) in zip(pts, pts[1:]):
        da, db = ya - xa, yb - xb
        if da == 0 or da * db < 0:
            f = da / (da - db) if da != db else 0.0
            azeos.append({"x1": r(xa + f * (xb - xa), 3), "T_C": r(Ta + f * (Tb_ - Ta))})
    tp = res.get("three_phase")
    if tp:
        out["不均一共沸（三相）"] = {
            "T3_C": r(tp["T3_C"]), "液相α_x1": r(tp["x_alpha"], 3),
            "液相β_x1": r(tp["x_beta"], 3), "気相_y1": r(tp["y3"], 3),
        }
    elif azeos:
        out["共沸点"] = azeos
    else:
        out["共沸点"] = "なし"

    table = []
    for i in range(0, len(x1), 8):
        table.append({"x1": r(x1[i], 3), "y1": r(y1[i], 3), "泡点_C": r(Tb[i]), "露点_C": r(Td[i])})
    out["線図データ(抜粋)"] = table

    if x1_query:
        q = []
        for xq in x1_query:
            q.append({"x1": xq, "y1": r(interp(xq, x1, y1), 3), "泡点_C": r(interp(xq, x1, Tb)),
                      "露点_C": r(interp(xq, x1, Td))})
        out["指定組成"] = q

    fig = make_subplots(rows=1, cols=2, subplot_titles=("xy 線図", "T-xy 線図"))
    fig.add_trace(go.Scatter(x=x1, y=y1, name="y1", mode="lines"), 1, 1)
    fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], name="対角線", mode="lines",
                             line=dict(dash="dot", color="gray")), 1, 1)
    fig.add_trace(go.Scatter(x=x1, y=Tb, name="泡点", mode="lines"), 1, 2)
    fig.add_trace(go.Scatter(x=x1, y=Td, name="露点", mode="lines", line=dict(dash="dash")), 1, 2)
    fig.update_xaxes(title_text=f"x1 ({s1['name']})", range=[0, 1])
    fig.update_yaxes(title_text="y1", row=1, col=1)
    fig.update_yaxes(title_text="温度 [°C]", row=1, col=2)
    fig.update_layout(title=f"VLE {s1['name']} / {s2['name']} @ {pressure_kPa:g} kPa")
    out["graph"] = ws.add_figure(f"VLE_{s1['name']}_{s2['name']}", fig)
    return out


# ---------------------------------------------------------------------------
# LLE 層分離
# ---------------------------------------------------------------------------

@tool(
    "lle_layer_split",
    "水 + 溶媒A + 溶媒B の3成分を仕込んだときの液液平衡（層分離）を計算し、"
    "水層・有機層それぞれの組成（mol%/w/w%/v/v%）と量を返す。抽出・分液の検討に使う。",
    {
        "water_amount": num("水の量"),
        "solvent1": string("溶媒A（通常は水溶性溶媒）"),
        "amount1": num("溶媒A の量"),
        "solvent2": string("溶媒B（通常は非水溶性溶媒）"),
        "amount2": num("溶媒B の量"),
        "unit": string("量の単位", ["g", "mol", "mL"]),
        "temperature_C": num("温度 [°C]（既定 25）"),
    },
    ["water_amount", "solvent1", "amount1", "solvent2", "amount2", "unit"],
)
def lle_layer_split(ws, water_amount: float, solvent1: str, amount1: float,
                    solvent2: str, amount2: float, unit: str, temperature_C: float = 25.0) -> dict:
    from core.thermo_engine import calc_layer_composition

    s1, s2 = resolve_solvent(solvent1), resolve_solvent(solvent2)
    if "Water" in (s1["name"], s2["name"]) or s1 is s2:
        raise ToolError("溶媒A・B には水以外の異なる2溶媒を指定してください。")
    res = calc_layer_composition(temperature_C, [water_amount, amount1, amount2], unit, s1, s2)
    if res.get("error"):
        raise ToolError(f"計算エラー: {res['error']}")

    names = ["Water", s1["name"], s2["name"]]
    out: dict = {"温度_C": temperature_C, "仕込み組成_mol%": {
        n: r(z * 100, 3) for n, z in zip(names, res["input_zs"])}}
    if res["phase_count"] != 2:
        out["結果"] = "均一相（層分離しない）"
        return out

    def layer(d: dict) -> dict:
        return {
            "相分率β(mol基準)": r(d["beta"], 3),
            "合計_g": r(sum(d["grams"])),
            "合計_mL": r(sum(d["volumes_mL"])),
            "組成": [{"成分": n, "mol%": r(d["mol_pct"][i], 3), "w/w%": r(d["ww_pct"][i], 3),
                     "v/v%": r(d["vv_pct"][i], 3), "g": r(d["grams"][i])}
                    for i, n in enumerate(names)],
        }
    out["結果"] = "2液相に分離"
    out["水層"] = layer(res["water_layer"])
    out["有機層"] = layer(res["organic_layer"])
    return out


# ---------------------------------------------------------------------------
# 濃縮シミュレーション（レイリー蒸留）
# ---------------------------------------------------------------------------

def run_rayleigh(sols: list[dict], moles: list[float], pressure_kPa: float, n_steps: int):
    from core.thermo_engine import calc_rayleigh_distillation
    return calc_rayleigh_distillation(sols, moles, pressure_kPa, n_steps=n_steps)


@tool(
    "concentration_simulation",
    "2〜4成分の溶媒混合物を連続蒸発（レイリー蒸留）させたときの液組成・沸点・留出組成の変化を計算する。"
    "溶媒置換・濃縮での残液組成や沸点上昇の検討に使う。",
    {
        "components": SOLVENT_AMOUNTS,
        "unit": AMOUNT_UNIT,
        "pressure_kPa": num("圧力 [kPa abs]（既定 101.325）"),
        "temperature_ref_C": num("体積→モル換算の液温 [°C]（既定 25）"),
        "evap_fractions": {"type": "array", "items": {"type": "number"},
                           "description": "状態を知りたい蒸発率（モル基準 0〜1）のリスト"},
    },
    ["components", "unit"],
)
def concentration_simulation(ws, components: list, unit: str, pressure_kPa: float = 101.325,
                             temperature_ref_C: float = 25.0, evap_fractions=None) -> dict:
    sols, moles = parse_solvent_amounts(components, unit, temperature_ref_C, n_min=2, n_max=4)
    res = run_rayleigh(sols, moles, pressure_kPa, n_steps=50)
    ef, Tbp = res["evap_fraction"], res["T_bp"]
    names = [s["name"] for s in sols]

    def state_at(f: float) -> dict:
        row = {"蒸発率": r(f, 3), "沸点_C": r(interp(f, ef, Tbp))}
        amts = [interp(f, ef, res["amounts"][n]) or 0.0 for n in names]
        tot = sum(amts)
        row["液組成_mol%"] = {n: r(a / tot * 100 if tot > 0 else 0, 3) for n, a in zip(names, amts)}
        row["残液_mol"] = {n: r(a) for n, a in zip(names, amts)}
        return row

    targets = evap_fractions or [0, 0.2, 0.4, 0.6, 0.8, 0.9]
    out = {
        "圧力_kPa": pressure_kPa,
        "初期_mol": {n: r(m) for n, m in zip(names, moles)},
        "推移": [state_at(f) for f in targets if 0 <= f <= (ef[-1] if ef else 0)],
    }
    if ef and ef[-1] < 0.999:
        out["注意"] = f"蒸発率 {ef[-1]:.2f} で計算が終了しました（フラッシュ未収束の可能性）。"

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    for n in names:
        fig.add_trace(go.Scatter(x=ef, y=res["amounts"][n], name=f"{n} [mol]"), secondary_y=False)
    fig.add_trace(go.Scatter(x=ef, y=Tbp, name="沸点 [°C]", line=dict(dash="dash", color="black")),
                  secondary_y=True)
    fig.update_xaxes(title_text="蒸発率 [-]")
    fig.update_yaxes(title_text="残液量 [mol]", secondary_y=False)
    fig.update_yaxes(title_text="沸点 [°C]", secondary_y=True)
    fig.update_layout(title=f"濃縮シミュレーション @ {pressure_kPa:g} kPa")
    out["graph"] = ws.add_figure("濃縮シミュレーション", fig)
    return out
