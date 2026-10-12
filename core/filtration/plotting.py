from __future__ import annotations
import plotly.graph_objects as go
from .models import FiltrationTimeResult, CompressibilityResult


def plot_filtration_curve(
    result: FiltrationTimeResult,
    title: str | None = None,
) -> go.Figure:
    """V [L] vs t [min] のろ過時間曲線を Plotly で描画する。

    Parameters
    ----------
    result : FiltrationTimeResult
    title  : str | None  グラフタイトル（None の場合は自動生成）

    Returns
    -------
    go.Figure
    """
    V_L = [v * 1e3 for v in result.V_m3]
    t_min = [t / 60.0 for t in result.t_s]

    dP_MPa = result.delta_P_Pa / 1e6
    if title is None:
        title = f"{result.mode}ろ過時間曲線  (ΔP = {dP_MPa:.3f} MPaG)"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=t_min,
        y=V_L,
        mode="lines",
        name="累積ろ液量",
        line=dict(color="royalblue", width=2),
    ))

    if result.total_time_s > 0:
        t_total_min = result.total_time_s / 60.0
        V_total_L = result.V_m3[-1] * 1e3 if result.V_m3 else 0.0
        fig.add_trace(go.Scatter(
            x=[t_total_min],
            y=[V_total_L],
            mode="markers",
            marker=dict(size=10, color="tomato", symbol="circle"),
            name=f"完了: {t_total_min:.1f} min",
        ))

    fig.update_layout(
        title=title,
        xaxis_title="時間 [min]",
        yaxis_title="累積ろ液量 [L]",
        template="plotly_white",
        height=420,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def plot_compressibility(result: CompressibilityResult) -> go.Figure:
    """log10(ΔP [Pa]) vs log10(α [m/kg]) の散布図＋フィット線を描画する。

    Parameters
    ----------
    result : CompressibilityResult

    Returns
    -------
    go.Figure
    """
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=result.log_dP,
        y=result.log_alpha,
        mode="markers",
        marker=dict(size=10, color="royalblue"),
        name="測定値",
    ))

    fig.add_trace(go.Scatter(
        x=result.fit_log_dP,
        y=result.fit_log_alpha,
        mode="lines",
        line=dict(color="tomato", dash="dash", width=2),
        name=f"フィット (n={result.n_compress:.3f})",
    ))

    fig.update_layout(
        title=f"ケーキ比抵抗 vs ろ過圧力  (n = {result.n_compress:.3f}, R² = {result.r_squared:.4f})",
        xaxis_title="log₁₀(ΔP [Pa])",
        yaxis_title="log₁₀(α [m/kg])",
        template="plotly_white",
        height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig
