"""反応速度解析ツール: 実験データ解析（k・次数・アレニウス）/ 反応シミュレーション"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from core.paths import EXPERIMENT_TEMPLATE, FLOW_TEMPLATE
from core.reaction.analysis import run_analysis
from core.reaction.data_loader import (
    auto_detect_reaction_type,
    get_temperature_groups,
    load_experiment_data,
)
from core.reaction.plotting import plot_arrhenius, plot_fit, plot_simulation_results
from core.reaction.reporter import generate_excel_report
from core.reaction.simulation import SimulationCondition, k_from_arrhenius, run_all_simulations

from .common import ToolError, num, r, string, tool

_TYPE_LABEL = {"simple": "単純反応 A→products", "sequential": "逐次反応 A→B→C",
               "parallel": "並列反応 A→B + A→C"}

# core.reaction の関数をまとめた名前空間（ツール本体からは ra.xxx で参照）
ra = SimpleNamespace(
    run_analysis=run_analysis, auto_detect_reaction_type=auto_detect_reaction_type,
    get_temperature_groups=get_temperature_groups, load_experiment_data=load_experiment_data,
    plot_arrhenius=plot_arrhenius, plot_fit=plot_fit, plot_simulation_results=plot_simulation_results,
    generate_excel_report=generate_excel_report, SimulationCondition=SimulationCondition,
    k_from_arrhenius=k_from_arrhenius, run_all_simulations=run_all_simulations,
)


def _template_path(kind: str) -> Path:
    return {"flow": FLOW_TEMPLATE, "reaction": EXPERIMENT_TEMPLATE}[kind]


@tool(
    "get_template",
    "入力用 Excel テンプレートを出力する。flow: 製造フローシート（タイムテーブル用）、"
    "reaction: 反応速度解析の実験データシート。",
    {"kind": string("テンプレート種別", ["flow", "reaction"])},
    ["kind"],
)
def get_template(ws, kind: str) -> dict:
    p = _template_path(kind)
    fname = ws.add_file(f"{kind} テンプレート", p.name, p.read_bytes())
    return {"file": fname, "説明": "記入後にアップロード（添付）してください。"}


@tool(
    "reaction_analysis",
    "添付された反応実験データ（Excel テンプレート or CSV: time, concentration[, _B, _C], temperature）"
    "を RK45 + 最小二乗法で解析し、速度定数 k・反応次数 n・R²、複数温度ならアレニウスパラメータ "
    "(Ea, A) を求める。Excel レポートとフィッティンググラフを出力する。",
    {
        "file_name": string("添付ファイル名"),
        "reaction_type": string("反応タイプ（auto は自動判定）",
                                ["auto", "simple", "sequential", "parallel"]),
    },
    ["file_name"],
)
def reaction_analysis(ws, file_name: str, reaction_type: str = "auto") -> dict:
    data = ws.get_input_file(file_name)
    try:
        df, _, load_warn = ra.load_experiment_data(io.BytesIO(data), file_name)
    except ValueError as e:
        raise ToolError(str(e)) from e
    detected, reason = ra.auto_detect_reaction_type(df)
    rtype = detected if reaction_type in (None, "", "auto") else reaction_type
    temp_groups = ra.get_temperature_groups(df)
    result = ra.run_analysis(df, rtype, temp_groups if len(temp_groups) >= 2 else None)
    ws.state["reaction"] = {"df": df, "result": result}

    fit = result.fit
    fit_key = "全データフィット(参考: 温度混在のため温度別・アレニウスを優先)"         if result.is_multi_temp else "全データフィット"
    out: dict = {
        "反応タイプ": _TYPE_LABEL[rtype],
        "自動判定": f"{_TYPE_LABEL.get(detected, detected)}（{reason}）",
        "データ点数": fit.n_points,
        fit_key: {
            "k": r(fit.k), "k_95%CI": [r(fit.k_ci_lower), r(fit.k_ci_upper)],
            "次数n": r(fit.order), "R2": r(fit.r2), "RMSE": r(fit.rmse), "収束": fit.success,
        },
        "注意": "k の単位は (濃度単位)^(1-n)/(時間単位)。時間・濃度の単位は入力データに従う。",
    }
    if fit.k2 is not None:
        out[fit_key]["k2"] = r(fit.k2)
        out[fit_key]["k2_95%CI"] = [r(fit.k2_ci_lower), r(fit.k2_ci_upper)]
    if fit.order2 is not None:
        out[fit_key]["次数n2"] = r(fit.order2)
    if result.per_temp_fits:
        out["温度別"] = [{"T_C": T, "k": r(f.k), "k2": r(f.k2), "n": r(f.order), "R2": r(f.r2)}
                       for T, f in result.per_temp_fits]
        if result.optimal_order is not None:
            out["最適次数(R2加重平均)"] = r(result.optimal_order)
        if result.optimal_order2 is not None:
            out["最適次数n2(R2加重平均)"] = r(result.optimal_order2)
        if result.per_temp_fits_common:
            out["温度別(共通次数で再フィット・アレニウスに使用)"] = [
                {"T_C": T, "k": r(f.k), "k2": r(f.k2), "R2": r(f.r2)}
                for T, f in result.per_temp_fits_common]
    for key, arr in (("アレニウス(k1)", result.arrhenius), ("アレニウス(k2)", result.arrhenius_k2)):
        if arr is not None:
            out[key] = {"Ea_kJ_mol": r(arr.Ea_kJmol), "A": r(arr.A), "R2": r(arr.r2)}
    warns = list(load_warn) + list(result.warnings)
    if warns:
        out["warnings"] = warns

    if fit.success and not result.is_multi_temp:
        out["graph"] = ws.add_figure("反応速度フィッティング", ra.plot_fit(df, fit))
    if result.arrhenius is not None:
        out["graph_arrhenius"] = ws.add_figure("アレニウスプロット", ra.plot_arrhenius(result.arrhenius))
    out["report"] = ws.add_file("反応速度解析レポート", "reaction_report.xlsx",
                                ra.generate_excel_report(df, result))
    return out


@tool(
    "reaction_simulation",
    "反応速度式（単純/逐次/並列、n 次）で濃度プロファイルを計算する。パラメータを直接指定するか、"
    "use_last_analysis=true で直前の reaction_analysis 結果（温度指定時はアレニウス外挿）を使う。"
    "目標転化率への到達時間、中間体 B の最大濃度とその時刻も返す。",
    {
        "use_last_analysis": {"type": "boolean", "description": "直前の解析結果を使う"},
        "temperatures_C": {"type": "array", "items": {"type": "number"},
                           "description": "use_last_analysis 時: アレニウスで k を外挿する温度 [°C] のリスト"},
        "reaction_type": string("反応タイプ", ["simple", "sequential", "parallel"]),
        "k": num("速度定数 k (k1)"), "n": num("反応次数 n (n1)（既定 1）"),
        "k2": num("逐次/並列: k2"), "n2": num("逐次: B→C の次数（既定 1）"),
        "A0": num("A の初期濃度（既定 1.0）"),
        "t_end": num("計算終了時間（データと同じ時間単位）"),
        "target_conversion": num("到達時間を知りたい A の転化率（0〜1）"),
    },
    ["t_end"],
)
def reaction_simulation(ws, t_end: float, use_last_analysis: bool = False, temperatures_C=None,
                        reaction_type=None, k=None, n=1.0, k2=None, n2=1.0, A0=1.0,
                        target_conversion=None) -> dict:
    conds = []
    if use_last_analysis:
        last = ws.state.get("reaction")
        if not last:
            raise ToolError("直前の reaction_analysis 結果がありません。先に解析を実行してください。")
        res = last["result"]
        rtype, fit = res.reaction_type, res.fit
        order = res.optimal_order if res.optimal_order is not None else fit.order
        order2 = res.optimal_order2 if res.optimal_order2 is not None else fit.order2
        if temperatures_C:
            if res.arrhenius is None:
                raise ToolError("アレニウスパラメータがありません（多温度データが必要）。")
            for T in temperatures_C:
                k_T = ra.k_from_arrhenius(res.arrhenius, T)
                k2_T = ra.k_from_arrhenius(res.arrhenius_k2, T) if res.arrhenius_k2 else fit.k2
                conds.append(ra.SimulationCondition(f"{T:g}°C", rtype, k_T, order, k2_T,
                                                    order2, A0, t_end))
        elif res.is_multi_temp:
            raise ToolError("多温度データの解析結果です。temperatures_C で温度を指定してください。")
        else:
            conds.append(ra.SimulationCondition("解析値", rtype, fit.k, fit.order, fit.k2,
                                                fit.order2, A0, t_end))
    else:
        if not reaction_type or k is None:
            raise ToolError("reaction_type と k を指定するか、use_last_analysis=true にしてください。")
        if reaction_type in ("sequential", "parallel") and k2 is None:
            raise ToolError(f"{reaction_type} では k2 が必要です。")
        conds.append(ra.SimulationCondition("指定値", reaction_type, k, n, k2, n2, A0, t_end))
        rtype = reaction_type

    sims = ra.run_all_simulations(conds)
    rows = []
    for cond, t, c in sims:
        if t is None:
            rows.append({"条件": cond.label, "error": "ODE 求解に失敗しました"})
            continue
        row = {"条件": cond.label, "k": r(cond.k), "k2": r(cond.k2),
               "終了時_A": r(c["A"][-1]), "終了時転化率": r(1 - c["A"][-1] / cond.A0, 3)}
        if "B" in c:
            i = int(np.argmax(c["B"]))
            row.update({"B最大濃度": r(c["B"][i]), "B最大時刻": r(t[i]), "終了時_B": r(c["B"][-1]),
                        "終了時_C": r(c["C"][-1])})
        if target_conversion is not None:
            A_target = cond.A0 * (1 - target_conversion)
            idx = np.where(c["A"] <= A_target)[0]
            row[f"転化率{target_conversion:g}到達時間"] = r(t[idx[0]]) if len(idx) else None
        rows.append(row)
    out = {"反応タイプ": _TYPE_LABEL[rtype], "結果": rows,
           "注意": "時間は解析データ（または指定 k）と同じ時間単位。"}
    out["graph"] = ws.add_figure("反応シミュレーション", ra.plot_simulation_results(sims, rtype))
    return out
