from __future__ import annotations

import plotly.graph_objects as go

from .models import SimResult


def plot_temperature_profile(
    result: SimResult,
    title: str = "温度プロファイル",
    show_jacket: bool = True,
    T_target_C: float | None = None,
) -> go.Figure:
    """
    温度-時間プロットを生成する。

    Parameters
    ----------
    result : SimResult
    title : str
    show_jacket : bool
        True のときジャケット温度曲線も描画。
    T_target_C : float | None
        内温制御の場合、目標温度の水平線を描画。
    """
    t_min = [t / 60.0 for t in result.t_s]  # s → min

    fig = go.Figure()

    # 内温
    fig.add_trace(go.Scatter(
        x=t_min,
        y=result.T_inner,
        mode="lines",
        name="内温",
        line=dict(color="royalblue", width=2),
    ))

    # ジャケット温
    if show_jacket:
        fig.add_trace(go.Scatter(
            x=t_min,
            y=result.T_jacket,
            mode="lines",
            name="ジャケット温",
            line=dict(color="tomato", width=2, dash="dash"),
        ))

    # 目標温度ライン（内温制御）
    if T_target_C is not None:
        fig.add_hline(
            y=T_target_C,
            line_dash="dot",
            line_color="gray",
            annotation_text=f"目標: {T_target_C:.1f} °C",
            annotation_position="top right",
        )

    # τ, 3τ マーカー（外温制御）
    if result.mode == "外温制御" and result.tau_s is not None:
        tau_min = result.tau_s / 60.0
        for mult, label in [(1, "τ (63.2%)"), (3, "3τ (95%)")]:
            x_val = tau_min * mult
            if x_val <= max(t_min):
                fig.add_vline(
                    x=x_val,
                    line_dash="dot",
                    line_color="green",
                    annotation_text=label,
                    annotation_position="top right",
                )

    # 添加終了ライン（添加モード）
    if result.mode == "添加" and len(t_min) > 1:
        # t_addition は SimResult に持たせていないため、
        # T_jacketが変化しない点では内温に添加終了の影響が出る。
        # 代わりに目視判断できるよう凡例のみ。
        pass

    fig.update_layout(
        title=title,
        xaxis_title="時間 [min]",
        yaxis_title="温度 [°C]",
        template="plotly_white",
        height=450,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )

    return fig
