"""
タイムテーブル自動作成 UI

フロー:
  1. テンプレート Excel をダウンロード
  2. フローシート・パラメータシートを記入してアップロード
  3. 計算が必要な工程は各計算モジュールで推算（未実装分は手動入力 or デフォルト値）
  4. タイムテーブル Excel をダウンロード
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from timetable.flow_reader import (
    ManufacturingFlow,
    ProcessStep,
    OPERATION_TYPES,
    TIME_METHOD_CALC,
    TIME_METHOD_MANUAL,
    read_flow_excel,
    resolve_schedule,
)
from timetable.timetable_writer import OP_COLORS, write_timetable_excel

TEMPLATE_PATH = Path(__file__).parent / "timetable" / "templates" / "flow_template.xlsx"


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _minutes_to_hhmm(minutes: float) -> str:
    h = int(minutes) // 60
    m = int(minutes) % 60
    return f"{h:02d}:{m:02d}"


def _op_badge(op_type: str, label: str) -> str:
    color = OP_COLORS.get(op_type, "D5D8DC")
    r = int(color[0:2], 16)
    g = int(color[2:4], 16)
    b = int(color[4:6], 16)
    brightness = (r * 299 + g * 587 + b * 114) / 1000
    text_color = "#222" if brightness > 128 else "#fff"
    return (
        f'<span style="background:#{color};color:{text_color};'
        f'padding:2px 8px;border-radius:4px;font-size:0.85em;'
        f'font-weight:600;">{label}</span>'
    )


# ---------------------------------------------------------------------------
# 計算モジュール呼び出し（将来拡張ポイント）
# ---------------------------------------------------------------------------

def _calc_heat_duration(params: dict) -> float | None:
    """伝熱計算で所要時間（分）を返す。engine_heat が未実装なら None。"""
    try:
        import engine_heat
        return engine_heat.calc_duration_min(params)
    except (ImportError, Exception):
        return None


def _calc_filtration_duration(params: dict) -> float | None:
    """ろ過時間推算。engine_filtration が未実装なら None。"""
    try:
        import engine_filtration
        return engine_filtration.calc_duration_min(params)
    except (ImportError, Exception):
        return None


def _calc_concentrate_duration(params: dict) -> float | None:
    """濃縮時間推算。engine_conc_time が未実装なら None。"""
    try:
        import engine_conc_time
        return engine_conc_time.calc_duration_min(params)
    except (ImportError, Exception):
        return None


def _calc_reaction_duration(params: dict) -> float | None:
    """反応時間推算。react_analysis 統合後に実装。"""
    try:
        import sys
        import os
        sys.path.insert(0, "/home/umeyoshi7/react_analysis")
        import react_analysis  # noqa: F401
        return react_analysis.calc_duration_min(params)
    except (ImportError, Exception):
        return None


CALC_DISPATCH = {
    "HEAT":        _calc_heat_duration,
    "COOL":        _calc_heat_duration,
    "FILTER":      _calc_filtration_duration,
    "CONCENTRATE": _calc_concentrate_duration,
    "REACTION":    _calc_reaction_duration,
}


def resolve_durations(flow: ManufacturingFlow) -> list[str]:
    """
    各工程の所要時間を解決する。
    計算モジュールが利用可能なら呼び出し、不可の場合は手動入力値をそのまま使用。

    Returns
    -------
    list[str] : 計算失敗した工程の警告メッセージ
    """
    warnings: list[str] = []
    for step in flow.steps:
        if step.time_method == TIME_METHOD_MANUAL:
            continue  # 手動時間はそのまま使用
        # 計算モジュール呼び出し
        calc_fn = CALC_DISPATCH.get(step.op_type)
        if calc_fn is not None:
            result = calc_fn(step.params)
            if result is not None:
                step.duration_min = result
            else:
                if step.manual_duration_min is not None:
                    step.duration_min = step.manual_duration_min
                    warnings.append(
                        f"工程{step.step_no}「{step.name}」: 計算モジュール未実装のため"
                        f"手動入力値 {step.manual_duration_min:.0f} 分を使用"
                    )
                else:
                    step.duration_min = 0.0
                    warnings.append(
                        f"工程{step.step_no}「{step.name}」: 計算モジュール未実装・手動値なし → 0分で仮置き"
                    )
        else:
            if step.manual_duration_min is not None:
                step.duration_min = step.manual_duration_min
            else:
                step.duration_min = 0.0
                warnings.append(
                    f"工程{step.step_no}「{step.name}」: 対応計算なし・手動値なし → 0分で仮置き"
                )
    return warnings


# ---------------------------------------------------------------------------
# プレビュー表示
# ---------------------------------------------------------------------------

def _render_preview(flow: ManufacturingFlow, schedule: dict, start_hour: float):
    """Streamlit 上でタイムテーブルプレビューを表示"""

    # ─ 集計情報 ─
    total_min = max((v["end"] for v in schedule.values()), default=0.0)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("総工程数", f"{len(flow.steps)} 工程")
    with col2:
        st.metric("総所要時間", f"{total_min:.0f} 分 ({total_min/60:.1f} h)")
    with col3:
        end_abs = start_hour * 60 + total_min
        st.metric("終了予定時刻", _minutes_to_hhmm(end_abs))

    st.divider()

    # ─ テーブル表示 ─
    st.subheader("工程一覧")
    rows = []
    for step in flow.steps:
        sch = schedule.get(step.step_no, {})
        start_abs = start_hour * 60 + sch.get("start", 0.0)
        end_abs   = start_hour * 60 + sch.get("end",   0.0)
        dur        = sch.get("duration", 0.0)
        rows.append({
            "工程番号": step.step_no,
            "工程名": step.name,
            "操作タイプ": step.op_label,
            "時間決定": step.time_method,
            "開始時刻": _minutes_to_hhmm(start_abs),
            "終了時刻": _minutes_to_hhmm(end_abs),
            "所要時間(分)": round(dur, 1),
            "所要時間(h)": round(dur / 60, 2),
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # ─ 簡易 Gantt（Plotly） ─
    st.subheader("Ganttチャート（プレビュー）")
    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        y_labels = []
        for step in reversed(flow.steps):
            sch = schedule.get(step.step_no, {})
            start_h = (start_hour * 60 + sch.get("start", 0.0)) / 60
            dur_h   = sch.get("duration", 0.0) / 60
            color = f"#{OP_COLORS.get(step.op_type, 'D5D8DC')}"
            y_label = f"[{step.step_no}] {step.name}"
            y_labels.append(y_label)

            fig.add_trace(go.Bar(
                name=step.op_label,
                x=[dur_h],
                y=[y_label],
                base=[start_h],
                orientation="h",
                marker_color=color,
                marker_line_color="#666",
                marker_line_width=0.5,
                hovertemplate=(
                    f"<b>{step.name}</b><br>"
                    f"操作: {step.op_label}<br>"
                    f"開始: {_minutes_to_hhmm(start_hour*60 + sch.get('start',0))}<br>"
                    f"終了: {_minutes_to_hhmm(start_hour*60 + sch.get('end',0))}<br>"
                    f"所要: {sch.get('duration',0):.0f} 分<extra></extra>"
                ),
                showlegend=False,
            ))

        # 操作タイプ凡例（ダミートレース）
        shown_types: set[str] = set()
        for step in flow.steps:
            if step.op_type not in shown_types:
                shown_types.add(step.op_type)
                color = f"#{OP_COLORS.get(step.op_type, 'D5D8DC')}"
                fig.add_trace(go.Bar(
                    name=step.op_label,
                    x=[0], y=[y_labels[0]],
                    orientation="h",
                    marker_color=color,
                    showlegend=True,
                    hoverinfo="skip",
                ))

        total_h = total_min / 60
        tick_step = 1.0 if total_h <= 24 else 2.0
        fig.update_layout(
            barmode="overlay",
            xaxis=dict(
                title="時刻 (h)",
                tickmode="linear",
                tick0=start_hour,
                dtick=tick_step,
                tickformat=".1f",
                range=[start_hour, start_hour + total_h + 0.5],
            ),
            yaxis=dict(title=""),
            height=max(300, len(flow.steps) * 36 + 80),
            margin=dict(l=0, r=20, t=20, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.warning(f"Ganttチャートの描画に失敗: {e}")


# ---------------------------------------------------------------------------
# メイン描画
# ---------------------------------------------------------------------------

def render():
    st.header("タイムテーブル自動作成")

    # ─ ステップ説明 ─
    with st.expander("使い方", expanded=False):
        st.markdown("""
**操作手順:**
1. **テンプレートをダウンロード** → 製造フローを記入
2. **フローファイルをアップロード** → 工程を確認
3. **計算が必要な工程のパラメータを確認**（または手動で時間を上書き）
4. **タイムテーブル生成** → Excel をダウンロード

**時間決定方法:**
- `手動`: 「手動時間(分)」列の値をそのまま使用
- `計算`: 対応する計算モジュール（伝熱計算・ろ過時間推算・濃縮時間推算・反応速度解析）で推算
  - 計算モジュールが利用不可の場合は手動入力値にフォールバック

**操作タイプ対応状況:**
| タイプ | 計算モジュール | 状況 |
|--------|---------------|------|
| HEAT / COOL | 伝熱計算 | 実装予定 |
| FILTER | ろ過時間推算 | 実装予定 |
| CONCENTRATE | 濃縮時間推算 | 実装予定 |
| REACTION | 反応速度解析 | react_analysis 統合後 |
| その他 | - | 手動のみ |
        """)

    st.divider()

    # ─ テンプレートダウンロード ─
    col_dl, col_up = st.columns([1, 2])
    with col_dl:
        st.subheader("① テンプレート")
        if TEMPLATE_PATH.exists():
            with open(TEMPLATE_PATH, "rb") as f:
                st.download_button(
                    label="📥 製造フローテンプレートをダウンロード",
                    data=f.read(),
                    file_name="flow_template.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
        else:
            st.warning("テンプレートファイルが見つかりません。`python -m timetable.create_templates` を実行してください。")

    # ─ ファイルアップロード ─
    with col_up:
        st.subheader("② フローファイルアップロード")
        uploaded = st.file_uploader(
            "記入済みの製造フロー Excel をアップロード",
            type=["xlsx"],
            key="timetable_upload",
        )

    if uploaded is None:
        st.info("製造フロー Excel をアップロードしてください。")
        return

    # ─ ファイル読み込み ─
    try:
        flow = read_flow_excel(io.BytesIO(uploaded.read()))
    except Exception as e:
        st.error(f"ファイルの読み込みに失敗しました: {e}")
        return

    if not flow.steps:
        st.warning("フローシートに工程が見つかりません。")
        return

    st.success(f"{len(flow.steps)} 工程を読み込みました。")
    st.divider()

    # ─ 工程確認・手動時間上書き ─
    st.subheader("③ 工程確認・時間調整")

    with st.expander("工程ごとの時間を確認・上書き", expanded=True):
        st.caption("計算モジュール未実装の工程は手動入力値が使われます。ここで直接上書きも可能です。")

        override_vals: dict[int, float | None] = {}
        cols_header = st.columns([1, 3, 2, 2, 2])
        for c, h in zip(cols_header, ["#", "工程名", "操作タイプ", "時間決定", "所要時間(分)"]):
            c.markdown(f"**{h}**")

        for step in flow.steps:
            c1, c2, c3, c4, c5 = st.columns([1, 3, 2, 2, 2])
            c1.markdown(f"**{step.step_no}**")
            c2.markdown(step.name)
            c3.markdown(_op_badge(step.op_type, step.op_label), unsafe_allow_html=True)
            c4.markdown(step.time_method)
            default_val = step.manual_duration_min if step.manual_duration_min is not None else 0.0
            val = c5.number_input(
                "分",
                min_value=0.0,
                value=float(default_val),
                step=1.0,
                key=f"dur_{step.step_no}",
                label_visibility="collapsed",
            )
            override_vals[step.step_no] = val

        # 上書き適用
        for step in flow.steps:
            if step.step_no in override_vals:
                step._duration_min = override_vals[step.step_no]
                step.manual_duration_min = override_vals[step.step_no]

    # ─ 製造開始時刻 ─
    st.subheader("④ 製造開始時刻・設定")
    c_start, c_blank = st.columns([1, 3])
    with c_start:
        start_hour = st.number_input(
            "製造開始時刻 (時)",
            min_value=0.0,
            max_value=23.5,
            value=8.0,
            step=0.5,
            format="%.1f",
        )

    # ─ タイムテーブル生成 ─
    st.divider()
    st.subheader("⑤ タイムテーブル生成")

    # 計算モジュールで所要時間を解決（手動入力既に適用済みなので警告のみ）
    warnings = resolve_durations(flow)
    for w in warnings:
        st.warning(w)

    schedule = resolve_schedule(flow)

    # プレビュー
    _render_preview(flow, schedule, start_hour)

    # Excel ダウンロード
    st.divider()
    try:
        excel_bytes = write_timetable_excel(flow, start_hour=start_hour)
        st.download_button(
            label="📊 タイムテーブル Excel をダウンロード",
            data=excel_bytes,
            file_name="timetable.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )
    except Exception as e:
        st.error(f"Excel 出力に失敗しました: {e}")
