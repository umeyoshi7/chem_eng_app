from __future__ import annotations
import math

from .models import ReactorSpec, GeometryResult


def mirror_area(D: float, mirror_type: str) -> float:
    """
    鏡部の伝熱面積 [m²]（内表面積）。
    ED (2:1 半楕円鏡): A = 1.380 × π × (D/2)²  (≈ 1.084 D²、回転楕円体の厳密値)
    SD (皿形鏡):       A = 1.200 × π × (D/2)²  (≈ 0.943 D²、Rc = D・容積 0.0847 D³ の皿形鏡)

    曲面の面積は投影円の面積 π(D/2)² より必ず大きい（係数 > 1）。
    """
    base = math.pi * (D / 2) ** 2
    if mirror_type == "ED":
        return 1.380 * base
    elif mirror_type == "SD":
        return 1.200 * base
    else:
        raise ValueError(f"未対応の鏡形状: {mirror_type}")


def mirror_volume(D: float, mirror_type: str) -> float:
    """
    鏡容積 [m³]。
    ED: V = π × D³ / 24
    SD: V ≈ 0.0847 × D³
    """
    if mirror_type == "ED":
        return math.pi * D ** 3 / 24.0
    elif mirror_type == "SD":
        return 0.0847 * D ** 3
    else:
        raise ValueError(f"未対応の鏡形状: {mirror_type}")


def calc_geometry(reactor: ReactorSpec, V_liquid_L: float) -> GeometryResult:
    """
    仕込み液量から伝熱面積・液高さを算出する。

    Parameters
    ----------
    reactor : ReactorSpec
    V_liquid_L : float
        仕込み液量 [L]

    Returns
    -------
    GeometryResult
        A_total : 液面下の総伝熱面積 [m²]
        h_liquid_m : 底面からの液高さ [m]

    Raises
    ------
    ValueError
        V_liquid_L が機器容量を超えている場合。
    """
    if V_liquid_L > reactor.volume_L:
        raise ValueError(
            f"仕込み量 {V_liquid_L:.1f} L が機器容量 {reactor.volume_L:.1f} L を超えています。"
        )

    D = reactor.diameter_m
    V_liq_m3 = V_liquid_L / 1000.0

    A_mir = mirror_area(D, reactor.mirror_type)
    V_mir = mirror_volume(D, reactor.mirror_type)

    r2 = math.pi * (D / 2) ** 2  # 断面積 [m²]

    if V_liq_m3 <= V_mir:
        # 鏡部のみに液が満たされている場合: 比例近似
        ratio = V_liq_m3 / V_mir if V_mir > 0 else 1.0
        A_total = A_mir * ratio
        h_cylinder_m = 0.0
        h_liquid_m = V_liq_m3 / r2  # 簡易液高さ（表示用）
    else:
        # 鏡全面 + 胴体側面
        V_cyl = V_liq_m3 - V_mir
        h_cylinder_m = V_cyl / r2
        A_cyl = math.pi * D * h_cylinder_m
        A_total = A_mir + A_cyl
        h_mirror_equiv = V_mir / r2
        h_liquid_m = h_mirror_equiv + h_cylinder_m

    return GeometryResult(
        A_mirror_full=A_mir,
        V_mirror_m3=V_mir,
        A_total=A_total,
        h_liquid_m=h_liquid_m,
        h_cylinder_m=h_cylinder_m,
    )
