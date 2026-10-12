from __future__ import annotations
import math
import numpy as np
from .models import CakeResistanceResult, CompressibilityResult, FiltrationTimeResult


def calc_cake_resistance(
    delta_P_MPaG: float,
    Q_L_per_min: float,
    mu_mPas: float,
    A_m2: float,
    m_cake_g: float,
    Rm_m_inv: float,
) -> CakeResistanceResult:
    """Ruth式からケーキ比抵抗 α を算出する。

    一定速度ろ過（定常状態）での Darcy 則:
        Q/A = ΔP / (μ · (α · m_cake/A² + Rm))   [注: R_cake = α·m_cake/A² は誤り]

    正確には:
        Q/A = ΔP / (μ · (α · m_cake/A + Rm))   ← α [m/kg], m_cake/A [kg/m²]
        → α = (A²·ΔP / (μ·Q·m_cake)) - (Rm·A / m_cake)   [m/kg]

    単位変換:
        ΔP [MPaG] → [Pa]:   × 1e6
        Q [L/min] → [m³/s]: × 1e-3 / 60
        μ [mPa·s] → [Pa·s]: × 1e-3
        m_cake [g] → [kg]:  × 1e-3

    Parameters
    ----------
    delta_P_MPaG : float  差圧 [MPaG]
    Q_L_per_min  : float  ろ液流量（一定速度時）[L/min]
    mu_mPas      : float  ろ液粘度 [mPa·s]
    A_m2         : float  フィルター面積 [m²]
    m_cake_g     : float  乾燥ケーキ質量 [g]
    Rm_m_inv     : float  ろ材抵抗 [m⁻¹]

    Returns
    -------
    CakeResistanceResult
    """
    if A_m2 <= 0:
        raise ValueError("フィルター面積 A は正の値である必要があります。")
    if m_cake_g <= 0:
        raise ValueError("乾燥ケーキ質量は正の値である必要があります。")
    if Q_L_per_min <= 0:
        raise ValueError("ろ液流量は正の値である必要があります。")
    if delta_P_MPaG <= 0:
        raise ValueError("ろ過圧力は正の値である必要があります。")
    if mu_mPas <= 0:
        raise ValueError("粘度は正の値である必要があります。")
    if Rm_m_inv < 0:
        raise ValueError("ろ材抵抗 Rm は 0 以上である必要があります。")

    dP_Pa = delta_P_MPaG * 1e6
    Q_m3s = Q_L_per_min * 1e-3 / 60.0
    mu_Pas = mu_mPas * 1e-3
    m_kg = m_cake_g * 1e-3

    alpha = (A_m2 ** 2 * dP_Pa) / (mu_Pas * Q_m3s * m_kg) - (Rm_m_inv * A_m2) / m_kg

    notes = []
    if alpha <= 0:
        notes.append("算出された α が 0 以下です。ろ材抵抗または入力値を確認してください。")

    return CakeResistanceResult(
        delta_P_Pa=dP_Pa,
        Q_m3_s=Q_m3s,
        alpha_m_per_kg=max(alpha, 0.0),
        Rm_m_inv=Rm_m_inv,
        notes=notes,
    )


def calc_compressibility(
    delta_P_list: list,
    alpha_list: list,
) -> CompressibilityResult:
    """log-log 線形回帰で圧縮性指数 n を算出する。

    モデル: α = α₀ · ΔP^n
    対数変換: log10(α) = log10(α₀) + n · log10(ΔP_Pa)

    Parameters
    ----------
    delta_P_list : list[float]  差圧リスト [MPaG]
    alpha_list   : list[float]  ケーキ比抵抗リスト [m/kg]

    Returns
    -------
    CompressibilityResult
    """
    valid = [(dP, a) for dP, a in zip(delta_P_list, alpha_list)
             if dP is not None and a is not None and dP > 0 and a > 0]
    if len(valid) < 2:
        raise ValueError("有効なデータ点が 2 点以上必要です。")

    dP_Pa_arr = np.array([v[0] * 1e6 for v in valid])
    alpha_arr = np.array([v[1] for v in valid])

    log_dP = np.log10(dP_Pa_arr).tolist()
    log_alpha = np.log10(alpha_arr).tolist()

    coeffs = np.polyfit(log_dP, log_alpha, 1)
    n = float(coeffs[0])
    log_alpha0 = float(coeffs[1])
    alpha0 = 10.0 ** log_alpha0

    log_alpha_pred = np.polyval(coeffs, log_dP)
    ss_res = np.sum((np.array(log_alpha) - log_alpha_pred) ** 2)
    ss_tot = np.sum((np.array(log_alpha) - np.mean(log_alpha)) ** 2)
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 1.0

    fit_x = np.linspace(min(log_dP), max(log_dP), 50).tolist()
    fit_y = np.polyval(coeffs, fit_x).tolist()

    return CompressibilityResult(
        alpha0=alpha0,
        n_compress=n,
        r_squared=r2,
        log_dP=log_dP,
        log_alpha=log_alpha,
        fit_log_dP=fit_x,
        fit_log_alpha=fit_y,
    )


def calc_filtration_time_pressure(
    delta_P_MPaG: float,
    mu_mPas: float,
    alpha_m_per_kg: float,
    Rm_m_inv: float,
    A_m2: float,
    m_cake_g: float,
    V_total_L: float,
    n_points: int = 200,
) -> FiltrationTimeResult:
    """Ruth方程式（加圧ろ過）でろ過時間曲線を計算する。

    t(V) = μ·α·c / (2·A²·ΔP) · V² + μ·Rm / (A·ΔP) · V
    c = m_cake_kg / V_total_m3  [kg/m³]

    Parameters
    ----------
    delta_P_MPaG    : float  差圧 [MPaG]
    mu_mPas         : float  粘度 [mPa·s]
    alpha_m_per_kg  : float  ケーキ比抵抗 α [m/kg]
    Rm_m_inv        : float  ろ材抵抗 [m⁻¹]
    A_m2            : float  フィルター面積 [m²]
    m_cake_g        : float  乾燥ケーキ質量 [g]
    V_total_L       : float  総ろ液量 [L]
    n_points        : int    出力点数

    Returns
    -------
    FiltrationTimeResult
    """
    if delta_P_MPaG <= 0:
        raise ValueError("ろ過圧力は正の値である必要があります。")
    if mu_mPas <= 0:
        raise ValueError("粘度は正の値である必要があります。")
    if alpha_m_per_kg <= 0:
        raise ValueError("ケーキ比抵抗 α は正の値である必要があります。")
    if A_m2 <= 0:
        raise ValueError("フィルター面積は正の値である必要があります。")
    if m_cake_g <= 0:
        raise ValueError("乾燥ケーキ質量は正の値である必要があります。")
    if V_total_L <= 0:
        raise ValueError("総ろ液量は正の値である必要があります。")
    if Rm_m_inv < 0:
        raise ValueError("ろ材抵抗 Rm は 0 以上である必要があります。")

    dP_Pa = delta_P_MPaG * 1e6
    mu_Pas = mu_mPas * 1e-3
    m_kg = m_cake_g * 1e-3
    V_total_m3 = V_total_L * 1e-3

    c = m_kg / V_total_m3  # ケーキ濃度 [kg/m³]

    coeff_A = mu_Pas * alpha_m_per_kg * c / (2.0 * A_m2 ** 2 * dP_Pa)
    coeff_B = mu_Pas * Rm_m_inv / (A_m2 * dP_Pa)

    V_arr = np.linspace(0.0, V_total_m3, n_points)
    t_arr = coeff_A * V_arr ** 2 + coeff_B * V_arr

    total_time_s = float(coeff_A * V_total_m3 ** 2 + coeff_B * V_total_m3)

    return FiltrationTimeResult(
        mode="加圧",
        t_s=t_arr.tolist(),
        V_m3=V_arr.tolist(),
        total_time_s=total_time_s,
        delta_P_Pa=dP_Pa,
    )


def calc_filtration_time_centrifuge(
    RPM: float,
    r_inner_m: float,
    r_outer_m: float,
    rho_g_mL: float,
    mu_mPas: float,
    alpha_m_per_kg: float,
    Rm_m_inv: float,
    A_m2: float,
    m_cake_g: float,
    V_total_L: float,
    n_points: int = 200,
) -> FiltrationTimeResult:
    """遠心ろ過の等価差圧を計算し Ruth 方程式を適用する。

    等価差圧:
        ω = 2π·RPM/60  [rad/s]
        ΔP_eq = ρ·ω²·(r_outer² - r_inner²) / 2  [Pa]

    以降は加圧ろ過と同じ Ruth 式を使用する。

    Parameters
    ----------
    RPM        : float  回転速度 [rpm]
    r_inner_m  : float  内半径（液面側）[m]
    r_outer_m  : float  外半径（フィルター壁）[m]
    rho_g_mL   : float  液密度 [g/mL]
    mu_mPas    : float  粘度 [mPa·s]
    alpha_m_per_kg : float  ケーキ比抵抗 α [m/kg]
    Rm_m_inv   : float  ろ材抵抗 [m⁻¹]
    A_m2       : float  フィルター面積 [m²]
    m_cake_g   : float  乾燥ケーキ質量 [g]
    V_total_L  : float  総ろ液量 [L]
    n_points   : int    出力点数

    Returns
    -------
    FiltrationTimeResult  (mode="遠心", delta_P_Pa = ΔP_eq)
    """
    if RPM <= 0:
        raise ValueError("回転速度 RPM は正の値である必要があります。")
    if r_outer_m <= r_inner_m:
        raise ValueError("外半径は内半径より大きい必要があります。")
    if rho_g_mL <= 0:
        raise ValueError("密度は正の値である必要があります。")

    rho_kg_m3 = rho_g_mL * 1000.0
    omega = 2.0 * math.pi * RPM / 60.0
    dP_eq_Pa = rho_kg_m3 * omega ** 2 * (r_outer_m ** 2 - r_inner_m ** 2) / 2.0

    result = calc_filtration_time_pressure(
        delta_P_MPaG=dP_eq_Pa / 1e6,
        mu_mPas=mu_mPas,
        alpha_m_per_kg=alpha_m_per_kg,
        Rm_m_inv=Rm_m_inv,
        A_m2=A_m2,
        m_cake_g=m_cake_g,
        V_total_L=V_total_L,
        n_points=n_points,
    )

    return FiltrationTimeResult(
        mode="遠心",
        t_s=result.t_s,
        V_m3=result.V_m3,
        total_time_s=result.total_time_s,
        delta_P_Pa=dP_eq_Pa,
        notes=[f"等価差圧 ΔP_eq = {dP_eq_Pa/1e6:.4f} MPaG (RPM={RPM:.0f}, "
               f"r_inner={r_inner_m:.3f} m, r_outer={r_outer_m:.3f} m)"],
    )
