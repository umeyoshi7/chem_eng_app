from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class CakeResistanceResult:
    """ケーキ比抵抗算出結果。"""
    delta_P_Pa: float           # 差圧 [Pa]
    Q_m3_s: float               # ろ液流量 [m³/s]
    alpha_m_per_kg: float       # ケーキ比抵抗 α [m/kg]
    Rm_m_inv: float             # ろ材抵抗 Rm [m⁻¹]（入力値）
    notes: list = field(default_factory=list)


@dataclass
class CompressibilityResult:
    """圧縮性指数算出結果。"""
    alpha0: float               # 基準比抵抗 α₀ [m/kg]（ΔP=1 Pa 時）
    n_compress: float           # 圧縮性指数 n [-]
    r_squared: float            # 決定係数 R²
    log_dP: list                # log10(ΔP [Pa]) データ点
    log_alpha: list             # log10(α [m/kg]) データ点
    fit_log_dP: list            # フィット線 x
    fit_log_alpha: list         # フィット線 y


@dataclass
class FiltrationTimeResult:
    """ろ過時間推算結果。"""
    mode: str                   # "加圧" | "遠心"
    t_s: list                   # 時間配列 [s]
    V_m3: list                  # 累積ろ液量配列 [m³]
    total_time_s: float         # 合計ろ過時間 [s]
    delta_P_Pa: float           # 実効差圧 [Pa]（遠心の場合は ΔP_eq）
    notes: list = field(default_factory=list)
