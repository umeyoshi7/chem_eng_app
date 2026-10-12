"""濃縮時間推算: レイリー蒸留結果 × 伝熱条件 → 濃縮所要時間"""

from __future__ import annotations

from core.heat_transfer.geometry import calc_geometry
from core.heat_transfer.models import ReactorSpec
from core.thermo_engine import (
    _solvents_to_flasher_args,
    calc_hvap_mix_J_mol,
    density_solvent,
    density_water,
)


def calc_concentration_time(
    rayleigh_result: dict,
    solvents: list,
    reactor: ReactorSpec,
    T_jacket_C: float,
) -> dict:
    """Rayleigh 蒸留結果と伝熱条件から濃縮時間を積算する。

    Returns
    -------
    dict:
        "evap_fraction" : list[float]  蒸発分率
        "time_min"      : list[float]  累積時間 [min]
        "Q_W"           : list[float]  各ステップの熱量 [W]
        "jacket_too_cold": bool        ジャケット温度が沸点以下になったか
    """
    thermo_ids, _, _ = _solvents_to_flasher_args(solvents)
    names = [s["name"] for s in solvents]

    evap_fracs = rayleigh_result["evap_fraction"]
    totals = rayleigh_result["total"]
    T_bps = rayleigh_result["T_bp"]
    vapor_fracs = rayleigh_result["vapor_fracs"]
    amounts = rayleigh_result["amounts"]

    n = len(evap_fracs)
    time_min = [0.0]
    Q_W_list = []
    jacket_too_cold = False
    hvap_fallback_used = False

    for i in range(n - 1):
        T_bp = T_bps[i]
        y = vapor_fracs[i]
        if T_bp is None or y is None:
            time_min.append(time_min[-1])
            Q_W_list.append(0.0)
            continue

        # 液相モル数 → 液量 [L]
        moles_i = [amounts[nm][i] for nm in names]
        V_L = 0.0
        for j, s in enumerate(solvents):
            if moles_i[j] <= 0:
                continue
            if s["name"] == "Water":
                rho = density_water(T_bp)
            else:
                rho = density_solvent(s, T_bp)
            V_L += moles_i[j] * s["mw"] / rho / 1000.0  # mol×(g/mol)/(g/mL)/1000 = L

        if V_L <= 0:
            time_min.append(time_min[-1])
            Q_W_list.append(0.0)
            continue

        # 伝熱面積
        try:
            geo = calc_geometry(reactor, V_L)
            A_m2 = geo.A_total
        except Exception:
            time_min.append(time_min[-1])
            Q_W_list.append(0.0)
            continue

        # 熱量 [W]
        Q_W = reactor.U * A_m2 * (T_jacket_C - T_bp)
        Q_W_list.append(Q_W)

        if Q_W <= 0:
            jacket_too_cold = True
            time_min.append(time_min[-1])
            continue

        # 混合蒸発熱 [J/mol]
        dHvap = calc_hvap_mix_J_mol(thermo_ids, y, T_bp + 273.15)
        if dHvap < 100:
            hvap_fallback_used = True
            dHvap = 40000.0

        # 蒸発モル数（このステップで蒸発する量）
        dn = totals[i] - totals[i + 1]
        if dn <= 0:
            time_min.append(time_min[-1])
            continue

        evap_rate_mol_s = Q_W / dHvap  # [mol/s]
        dt_s = dn / evap_rate_mol_s     # [s]
        time_min.append(time_min[-1] + dt_s / 60.0)

    # 最終ステップ（n-1）の T_bp を記録して調整
    while len(time_min) < n:
        time_min.append(time_min[-1])

    return {
        "evap_fraction": evap_fracs,
        "time_min": time_min,
        "Q_W": Q_W_list,
        "jacket_too_cold": jacket_too_cold,
        "hvap_fallback_used": hvap_fallback_used,
    }
