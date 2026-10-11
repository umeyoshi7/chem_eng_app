"""計算エンジン不具合修正の回帰テスト（pytest 不要: python tests/test_calc_fixes.py）"""

from __future__ import annotations

import logging
import math
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")
logging.getLogger("streamlit").setLevel(logging.ERROR)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import engine  # noqa: E402
from solvents import ALL_SOLVENTS, get_solvent_by_name  # noqa: E402
from heat_transfer.src.geometry import calc_geometry, mirror_area  # noqa: E402
from heat_transfer.src.heat_calc import simulate_addition  # noqa: E402
from heat_transfer.src.models import ReactorSpec  # noqa: E402
# react_analysis は `from src.X` 形式のため、パス先頭に追加して src を解決する
sys.path.insert(0, str(ROOT / "react_analysis"))
for _k in [k for k in sys.modules if k == "src" or k.startswith("src.")]:
    del sys.modules[_k]


def _sols(*names):
    return [get_solvent_by_name(n, ALL_SOLVENTS) for n in names]


def _last_valid(res):
    pts = [(e, T) for e, T in zip(res["evap_fraction"], res["T_bp"]) if T is not None]
    return pts[-1]


def test_rayleigh_thf_water_runs_to_end():
    """均一共沸近傍の泡点停滞で三相域キャッシュが誤作動し、途中で止まっていた。"""
    res = engine.calc_rayleigh_distillation(_sols("THF", "Water"), [0.6, 0.4], 101.325)
    e, T = _last_valid(res)
    assert e > 0.95, e
    assert 95.0 < T < 101.0, T  # 最後は水のみ → 約 100 °C
    # 液組成が変われば気相組成も変わる（一定値に固定されていない）
    ys = [y[0] for y in res["vapor_fracs"] if y is not None]
    assert max(ys) - min(ys) > 0.5


def test_rayleigh_toluene_water_continues_after_water_depleted():
    """水が枯渇して泡点が 84→110 °C に跳ぶと探索範囲外で計算が止まっていた。"""
    res = engine.calc_rayleigh_distillation(_sols("Toluene", "Water"), [0.5, 0.5], 101.325)
    Ts = [T for T in res["T_bp"] if T is not None]
    assert abs(Ts[0] - 84.3) < 1.5, Ts[0]  # 不均一共沸（三相）温度
    e, T = _last_valid(res)
    assert e > 0.95, e
    assert 105.0 < T < 112.0, T  # トルエン単独の沸点


def test_rayleigh_ethanol_water_unchanged():
    res = engine.calc_rayleigh_distillation(_sols("Ethanol", "Water"), [0.3, 0.7], 101.325)
    Ts = [T for T in res["T_bp"] if T is not None]
    assert all(b >= a - 0.05 for a, b in zip(Ts, Ts[1:]))  # 泡点は単調増加
    assert abs(Ts[0] - 81.3) < 1.0, Ts[0]


def test_thf_water_homogeneous_azeotrope():
    """UNIFAC Dortmund は THF/水 を部分混和と誤予測 → 共沸データ由来の NRTL で補正。"""
    res = engine.calc_vle_xy(_sols("THF", "Water"), 101.325, n=40)
    assert res["three_phase"] is None
    pts = [(x, y, T) for x, y, T in zip(res["x1"], res["y1"], res["T_bubble_C"])
           if T is not None and y is not None and 0.0 < x < 1.0]
    x_min, _, T_min = min(pts, key=lambda p: p[2])
    az = engine.THF_WATER_AZEOTROPE
    assert abs(T_min - az["T_C"]) < 1.0, T_min
    assert 0.75 < x_min < 0.9, x_min
    # 25 °C で THF/水 は 1 液相
    r = engine.calc_layer_composition(25, [100, 100, 1e-3], "g", *_sols("THF", "Toluene"))
    assert r["phase_count"] == 1


def test_toluene_water_bubble_not_below_T3():
    """液液分離を見逃した準安定解（T_b < T3）が VLE 線図に残っていた。"""
    res = engine.calc_vle_xy(_sols("Toluene", "Water"), 101.325, n=40)
    T3 = res["three_phase"]["T3_C"]
    assert all(T >= T3 - 0.01 for T in res["T_bubble_C"] if T is not None)


def test_mirror_area_exceeds_projection():
    D = 1.2
    proj = math.pi * (D / 2) ** 2
    assert abs(mirror_area(D, "ED") / D**2 - 1.084) < 0.005
    assert mirror_area(D, "SD") > proj
    r = ReactorSpec("T", 300.0, 2000.0, D, "ED")
    geo = calc_geometry(r, 1500.0)
    assert geo.A_total > geo.A_mirror_full


def test_batch_addition_uses_reagent_temperature():
    r = ReactorSpec("T", 300.0, 500.0, 0.8, "ED")
    geo = calc_geometry(r, 200.0)
    res = simulate_addition(r, geo, T0_inner_C=20.0, T_jacket_C=20.0, T_reagent_C=80.0,
                            mass_initial_g=100_000, cp_initial=4.18,
                            mass_reagent_g=100_000, cp_reagent=4.18,
                            Q_rxn_total_kJ=0.0, addition_mode="batch", t_addition_s=0.0)
    assert abs(res.T_inner[0] - 50.0) < 0.01, res.T_inner[0]  # 等量・等比熱 → 平均温度


def test_zero_order_does_not_go_below_zero():
    from src.ode_systems import _ode_simple
    assert _ode_simple(0.0, [0.0], 0.05, 0.0) == [0.0]


def test_arrhenius_uses_common_order():
    """温度ごとに次数が違う k をそのまま回帰していた（k の単位が不一致）。"""
    from src.analysis import run_analysis
    rng = np.random.default_rng(3)
    groups = {}
    for T, k in [(30, 0.02), (45, 0.05), (60, 0.12)]:
        t = np.linspace(0, 60, 9)
        c = np.exp(-k * t) * (1 + rng.normal(0, 0.03, len(t)))
        groups[float(T)] = pd.DataFrame({"time": t, "concentration": c, "temperature": T})
    df = pd.concat(groups.values(), ignore_index=True)
    res = run_analysis(df, "simple", groups)
    orders = {round(f.order, 6) for _, f in res.per_temp_fits_common}
    assert orders == {round(res.optimal_order, 6)}
    true_Ea = 8.314 * np.polyfit([1 / (T + 273.15) for T in (30, 45, 60)],
                                 -np.log([0.02, 0.05, 0.12]), 1)[0] / 1000
    assert abs(res.arrhenius.Ea_kJmol - true_Ea) < 2.0, (res.arrhenius.Ea_kJmol, true_Ea)


def test_config_strips_whitespace():
    import os
    from timetable_agent.config import load_config
    keys = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT",
            "AZURE_OPENAI_API_VERSION")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ.update({"AZURE_OPENAI_ENDPOINT": "https://x.openai.azure.com/\n",
                           "AZURE_OPENAI_API_KEY": "abc\n", "AZURE_OPENAI_DEPLOYMENT": " gpt-4o ",
                           "AZURE_OPENAI_API_VERSION": "\n"})
        cfg = load_config()
        assert cfg.endpoint == "https://x.openai.azure.com/"
        assert cfg.api_key == "abc" and cfg.deployment == "gpt-4o"
        assert cfg.api_version == "2024-10-21"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"\n{len(tests)} tests passed.")
