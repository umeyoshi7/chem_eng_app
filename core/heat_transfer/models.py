from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ReactorSpec:
    tag_no: str
    U: float                          # 総括伝熱係数 [W/(m²·K)]
    volume_L: float                   # 機器容量 [L]
    diameter_m: float                 # 内径 [m]
    mirror_type: Literal["ED", "SD"]  # 鏡形状


@dataclass
class GeometryResult:
    A_mirror_full: float   # 鏡部全伝熱面積 [m²]
    V_mirror_m3: float     # 鏡容積 [m³]
    A_total: float         # 総伝熱面積（液面下） [m²]
    h_liquid_m: float      # 液高さ（底面から） [m]
    h_cylinder_m: float    # 胴体部液高さ [m]


@dataclass
class SimResult:
    mode: str                            # "内温制御" | "外温制御" | "添加"
    t_s: list                            # 時間配列 [s]
    T_inner: list                        # 内温配列 [°C]
    T_jacket: list                       # ジャケット温配列 [°C]
    tau_s: float | None = None           # 時定数 [s]（外温制御のみ）
    t_target_s: float | None = None      # 到達時間 [s]（内温制御のみ）
    heating_rate_K_per_min: float | None = None  # 昇降温速度 [K/min]
    notes: list = field(default_factory=list)
