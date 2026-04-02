from __future__ import annotations

import numpy as np
from scipy.integrate import solve_ivp

from .models import ReactorSpec, GeometryResult, SimResult


# ── ヘルパー ──────────────────────────────────────────────────────────────────

def calc_cp_mix(cp_list: list[float], mass_list: list[float]) -> float:
    """質量加重平均比熱 [J/(g·K)]。"""
    total_mass = sum(mass_list)
    if total_mass <= 0:
        raise ValueError("質量の合計が 0 以下です。")
    return sum(c * m for c, m in zip(cp_list, mass_list)) / total_mass


def tau_seconds(U: float, A: float, mass_g: float, cp: float) -> float:
    """時定数 τ = m·Cp / (U·A) [s]。"""
    return (mass_g * cp) / (U * A)


# ── 機能①-A: 内温制御 ────────────────────────────────────────────────────────

def simulate_inner_control(
    reactor: ReactorSpec,
    geo: GeometryResult,
    T0_C: float,
    T_target_C: float,
    dT_offset_K: float,
    mass_g: float,
    cp: float,
    n_points: int = 300,
) -> SimResult:
    """
    内温制御シミュレーション（PI的: 一定ΔT追従）。

    T_jacket(t) = T_inner(t) + dT_offset_K
    dT/dt = U·A·dT_offset / (m·Cp)  [定数]
    t_total = |ΔT_target| · m·Cp / (U·A·|dT_offset|)
    """
    notes: list[str] = []

    A = geo.A_total
    if A <= 0:
        return SimResult("内温制御", [0.0], [T0_C], [T0_C + dT_offset_K],
                         notes=["伝熱面積が 0 です。液量を確認してください。"])

    if abs(dT_offset_K) < 0.01:
        return SimResult("内温制御", [0.0], [T0_C], [T0_C],
                         notes=["ジャケット温度オフセットが 0 です。"])

    # 昇降温方向チェック
    delta_T = T_target_C - T0_C
    # dT_offset の符号が昇降温方向と一致しているか確認
    if delta_T * dT_offset_K < 0:
        notes.append(
            "警告: ΔT_offsetの符号が目標温度の方向と逆です。目標に到達できない可能性があります。"
        )

    rate_K_per_s = reactor.U * A * dT_offset_K / (mass_g * cp)  # [K/s]
    if abs(rate_K_per_s) < 1e-12:
        return SimResult("内温制御", [0.0], [T0_C], [T0_C + dT_offset_K],
                         notes=["昇降温速度がほぼ 0 です。"])

    t_total_s = abs(delta_T) / abs(rate_K_per_s)

    t_arr = np.linspace(0.0, t_total_s, n_points)
    T_inner = T0_C + rate_K_per_s * t_arr
    # 目標を超えないようにクリップ
    if delta_T >= 0:
        T_inner = np.minimum(T_inner, T_target_C)
    else:
        T_inner = np.maximum(T_inner, T_target_C)

    T_jacket = T_inner + dT_offset_K

    return SimResult(
        mode="内温制御",
        t_s=t_arr.tolist(),
        T_inner=T_inner.tolist(),
        T_jacket=T_jacket.tolist(),
        t_target_s=t_total_s,
        heating_rate_K_per_min=rate_K_per_s * 60.0,
        notes=notes,
    )


# ── 機能①-B: 外温制御 ────────────────────────────────────────────────────────

def simulate_outer_control(
    reactor: ReactorSpec,
    geo: GeometryResult,
    T0_C: float,
    T_jacket_C: float,
    mass_g: float,
    cp: float,
    t_end_s: float | None = None,
    n_points: int = 300,
) -> SimResult:
    """
    外温制御シミュレーション（ジャケット温固定・指数応答）。

    τ = m·Cp / (U·A)
    T_inner(t) = T_jacket + (T0 - T_jacket)·exp(-t/τ)
    """
    notes: list[str] = []

    A = geo.A_total
    if A <= 0:
        return SimResult("外温制御", [0.0], [T0_C], [T_jacket_C],
                         notes=["伝熱面積が 0 です。液量を確認してください。"])

    tau = tau_seconds(reactor.U, A, mass_g, cp)
    if t_end_s is None or t_end_s <= 0:
        t_end_s = 5.0 * tau  # 5τ まで（99.3% 到達）

    t_arr = np.linspace(0.0, t_end_s, n_points)
    T_inner = T_jacket_C + (T0_C - T_jacket_C) * np.exp(-t_arr / tau)
    T_jacket_arr = np.full_like(t_arr, T_jacket_C)

    if abs(T0_C - T_jacket_C) < 0.5:
        notes.append("初期内温とジャケット温が近似的に等しいため、温度変化はほぼありません。")

    return SimResult(
        mode="外温制御",
        t_s=t_arr.tolist(),
        T_inner=T_inner.tolist(),
        T_jacket=T_jacket_arr.tolist(),
        tau_s=tau,
        notes=notes,
    )


# ── 機能②: 添加シミュレーション ──────────────────────────────────────────────

def simulate_addition(
    reactor: ReactorSpec,
    geo: GeometryResult,
    T0_inner_C: float,
    T_jacket_C: float,
    T_reagent_C: float,
    mass_initial_g: float,
    cp_initial: float,
    mass_reagent_g: float,
    cp_reagent: float,
    Q_rxn_total_kJ: float,
    addition_mode: str,       # "continuous" | "batch"
    t_addition_s: float,
    t_end_s: float | None = None,
    n_points: int = 500,
) -> SimResult:
    """
    添加シミュレーション（ODE）。

    連続添加:
        m(t) = m_initial + m_dot·t,  m_dot = mass_reagent / t_addition
        dT/dt = [U·A·(T_jk - T) + Q_rxn_rate(t) - m_dot·cp_reagent·(T - T_reagent)]
                / (m(t)·Cp_mix(t))

    一括添加:
        断熱温度変化: ΔT_adiabatic = Q_rxn·1000 / (m_total·Cp_mix)
        T0_ode = T0 + ΔT_adiabatic
        ODE: ジャケット冷却のみ（m = m_total, Cp = Cp_mix_final）
    """
    notes: list[str] = []
    A = geo.A_total
    U = reactor.U
    m_total = mass_initial_g + mass_reagent_g
    cp_mix_final = calc_cp_mix([cp_initial, cp_reagent], [mass_initial_g, mass_reagent_g])

    if addition_mode == "batch":
        # 断熱温度変化を初期条件として適用
        dT_adiabatic = Q_rxn_total_kJ * 1000.0 / (m_total * cp_mix_final)
        T0_ode = T0_inner_C + dT_adiabatic

        if t_end_s is None or t_end_s <= 0:
            tau_f = tau_seconds(U, A, m_total, cp_mix_final)
            t_end_s = max(5.0 * tau_f, 600.0)

        t_eval = np.linspace(0.0, t_end_s, n_points)

        def ode_batch(t, y):
            T = y[0]
            q_jacket = U * A * (T_jacket_C - T)
            dT = q_jacket / (m_total * cp_mix_final)
            return [dT]

        sol = solve_ivp(ode_batch, [0.0, t_end_s], [T0_ode],
                        t_eval=t_eval, method="RK45", rtol=1e-4, atol=1e-6)

        if not sol.success:
            notes.append(f"ODE ソルバーエラー: {sol.message}")

        T_inner = sol.y[0].tolist()
        T_jacket_arr = np.full(len(sol.t), T_jacket_C).tolist()

        if abs(dT_adiabatic) > 0.1:
            notes.append(
                f"一括添加による断熱温度変化: {dT_adiabatic:+.1f} °C "
                f"(初期内温 {T0_inner_C:.1f} → {T0_ode:.1f} °C)"
            )

        return SimResult(
            mode="添加",
            t_s=sol.t.tolist(),
            T_inner=T_inner,
            T_jacket=T_jacket_arr,
            notes=notes,
        )

    else:  # continuous
        if t_addition_s <= 0:
            notes.append("添加時間が 0 以下です。連続添加では正の添加時間が必要です。")
            t_addition_s = 1.0

        if t_end_s is None or t_end_s <= 0:
            tau_f = tau_seconds(U, A, m_total, cp_mix_final)
            t_end_s = max(t_addition_s * 1.5, t_addition_s + 3.0 * tau_f)

        m_dot = mass_reagent_g / t_addition_s          # [g/s]
        Q_rxn_rate_W = Q_rxn_total_kJ * 1000.0 / t_addition_s  # [W]

        t_eval = np.linspace(0.0, t_end_s, n_points)

        def ode_continuous(t, y):
            T = y[0]
            m_now = mass_initial_g + m_dot * min(t, t_addition_s)
            m_added = m_dot * min(t, t_addition_s)
            if mass_initial_g + m_added > 0:
                cp_now = calc_cp_mix(
                    [cp_initial, cp_reagent],
                    [mass_initial_g, m_added],
                )
            else:
                cp_now = cp_initial

            q_jacket = U * A * (T_jacket_C - T)
            q_rxn = Q_rxn_rate_W if t <= t_addition_s else 0.0
            q_sensible = m_dot * cp_reagent * (T - T_reagent_C) if t <= t_addition_s else 0.0

            dT = (q_jacket + q_rxn - q_sensible) / (m_now * cp_now)
            return [dT]

        sol = solve_ivp(ode_continuous, [0.0, t_end_s], [T0_inner_C],
                        t_eval=t_eval, method="RK45", rtol=1e-4, atol=1e-6)

        if not sol.success:
            notes.append(f"ODE ソルバーエラー: {sol.message}")

        T_inner = sol.y[0].tolist()
        T_jacket_arr = np.full(len(sol.t), T_jacket_C).tolist()

        return SimResult(
            mode="添加",
            t_s=sol.t.tolist(),
            T_inner=T_inner,
            T_jacket=T_jacket_arr,
            notes=notes,
        )
