"""Top-level analysis pipeline for reaction kinetics."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from src.arrhenius import run_arrhenius
from src.data_loader import auto_detect_reaction_type
from src.fitting import run_fit
from src.models import AnalysisResult, ArrheniusResult, FitResult


def run_analysis(
    df: pd.DataFrame,
    reaction_type: str,
    temp_groups: Optional[dict] = None,
) -> AnalysisResult:
    """
    Run complete kinetic analysis pipeline.

    Steps
    -----
    1. auto_detect_reaction_type(df)
    2. run_fit(df, reaction_type) → FitResult (all data combined)
    3. If multi-temp: run_fit() for each temperature → per_temp_fits
    4. optimal_order = R²-weighted mean of per_temp_fits[i].order
    5. Re-fit each temperature with the order fixed to optimal_order
       → run_arrhenius() → arrhenius (and arrhenius_k2 if sequential/parallel)

    Parameters
    ----------
    df            : experiment DataFrame
    reaction_type : "simple" | "sequential" | "parallel"
    temp_groups   : {T_celsius: sub_df} or None
    """
    warnings: list[str] = []

    # 1. Auto-detect
    detected_type, detected_reason = auto_detect_reaction_type(df)
    if detected_type != reaction_type:
        warnings.append(
            f"自動判定では「{_type_label(detected_type)}」を推奨しますが、"
            f"「{_type_label(reaction_type)}」で解析します。"
        )

    # 2. Fit all data
    fit: FitResult = run_fit(df, reaction_type)
    if not fit.success:
        warnings.append(f"フィッティング収束エラー: {fit.message}")

    is_multi_temp = temp_groups is not None and len(temp_groups) >= 2

    if is_multi_temp:
        warnings.append(
            "多温度データが検出されました。温度別解析・Arrheniusパラメータは"
            "「Arrheniusパラメータ」タブで確認できます。"
        )

    # 3. Per-temperature fits
    per_temp_fits: list[tuple[float, FitResult]] = []
    if is_multi_temp:
        for T_c, sub_df in temp_groups.items():
            try:
                sub_fit = run_fit(sub_df, reaction_type)
                per_temp_fits.append((T_c, sub_fit))
            except Exception as e:
                warnings.append(f"{T_c:.1f}°C の解析でエラー: {e}")

    # 4. Optimal order (R²-weighted mean over per-temp fits)
    optimal_order = _weighted_order(per_temp_fits, lambda f: f.order)
    optimal_order2: Optional[float] = None
    if reaction_type == "sequential":
        optimal_order2 = _weighted_order(per_temp_fits, lambda f: f.order2)

    # 5. 共通次数で温度別に再フィット → アレニウス解析
    #    次数 n が温度ごとに異なると k の単位 ((濃度)^(1-n)/時間) がそろわず、
    #    ln k vs 1/T の回帰が物理的に意味を持たないため、次数を固定して k を求め直す。
    per_temp_fits_common: list[tuple[float, FitResult]] = []
    if is_multi_temp and optimal_order is not None:
        for T_c, sub_df in temp_groups.items():
            try:
                per_temp_fits_common.append(
                    (T_c, run_fit(sub_df, reaction_type,
                                  fixed_order=optimal_order, fixed_order2=optimal_order2))
                )
            except Exception as e:
                warnings.append(f"{T_c:.1f}°C の共通次数フィットでエラー: {e}")
        order_txt = f"n = {optimal_order:.3f}"
        if optimal_order2 is not None:
            order_txt += f", n2 = {optimal_order2:.3f}"
        warnings.append(
            f"アレニウス解析には共通反応次数（{order_txt}、温度別推定値の R² 加重平均）で"
            "各温度を再フィットした速度定数を使用しています。"
        )

    arrhenius: Optional[ArrheniusResult] = None
    arrhenius_k2: Optional[ArrheniusResult] = None
    if is_multi_temp:
        fits_for_arrhenius = per_temp_fits_common or per_temp_fits or None
        try:
            arrhenius = run_arrhenius(
                temp_groups, reaction_type, k_index=1,
                per_temp_fits=fits_for_arrhenius,
            )
            if arrhenius is None:
                warnings.append(
                    "アレニウス解析: 有効な温度グループが2点未満のためスキップしました。"
                )
        except Exception as e:
            warnings.append(f"アレニウス解析 (k1) でエラー: {e}")

        if reaction_type in ("sequential", "parallel"):
            try:
                arrhenius_k2 = run_arrhenius(
                    temp_groups, reaction_type, k_index=2,
                    per_temp_fits=fits_for_arrhenius,
                )
            except Exception as e:
                warnings.append(f"アレニウス解析 (k2) でエラー: {e}")

    return AnalysisResult(
        fit=fit,
        reaction_type=reaction_type,
        detected_reaction_type=detected_type,
        detected_reaction_reason=detected_reason,
        is_multi_temp=is_multi_temp,
        per_temp_fits=per_temp_fits,
        arrhenius=arrhenius,
        arrhenius_k2=arrhenius_k2,
        optimal_order=optimal_order,
        warnings=warnings,
        optimal_order2=optimal_order2,
        per_temp_fits_common=per_temp_fits_common,
    )


def _weighted_order(per_temp_fits: list, getter) -> Optional[float]:
    """R²-weighted mean of a fitted order over successful per-temp fits, clamped to [0, 5]."""
    valid = []
    for _, fit_i in per_temp_fits:
        v = getter(fit_i)
        if fit_i.success and v is not None and np.isfinite(v) and np.isfinite(fit_i.r2):
            valid.append((v, fit_i.r2))
    if not valid:
        return None
    orders, weights = zip(*valid)
    w = np.clip(np.array(weights, dtype=float), 0.0, None)
    order = float(np.average(orders, weights=w)) if w.sum() > 0 else float(np.mean(orders))
    return float(np.clip(order, 0.0, 5.0))


def _type_label(t: str) -> str:
    return {
        "simple": "単純反応",
        "sequential": "逐次反応",
        "parallel": "並列反応",
    }.get(t, t)
