"""
タイムテーブル自動作成 UI

フロー:
  1. テンプレート Excel をダウンロード
  2. フローシート・パラメータシートを記入してアップロード
  3. 工程ごとに機器 Tag No. を選択（反応槽 / フィルター）
  4. 計算が必要な工程は各計算モジュールで所要時間を推算
  5. Gantt チャートで機器別に並列可視化
  6. タイムテーブル Excel をダウンロード
"""

from __future__ import annotations

import io
import os
import sys
from collections import defaultdict
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from timetable.flow_reader import (
    ManufacturingFlow,
    OPERATION_TYPES,
    TIME_METHOD_CALC,
    TIME_METHOD_MANUAL,
    read_flow_excel,
    resolve_schedule,
)
from timetable.timetable_writer import OP_COLORS, write_timetable_excel

TEMPLATE_PATH = Path(__file__).parent / "timetable" / "templates" / "flow_template.xlsx"

# 機器選択が必要な操作タイプ
_REACTOR_OPS = {"HEAT", "COOL", "REACTION", "CONCENTRATE", "CHARGE"}
_FILTER_OPS  = {"FILTER"}

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
# 機器リスト（キャッシュ）
# ---------------------------------------------------------------------------

@st.cache_resource
def _load_equipment_items():
    """全機器（反応槽＋フィルター）を EquipmentItem リストで返す。"""
    try:
        from heat_transfer.src.equipment_repo import get_equipment_repo
        repo = get_equipment_repo()
        return repo.list_all()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 計算モジュール呼び出し（実装済み）
# ---------------------------------------------------------------------------

def _calc_heat_duration(params: dict) -> float | None:
    """伝熱計算（内温制御）で昇降温所要時間 [分] を返す。

    必要な params キー (params[key] は {"value": ..., "unit": ...} dict か直接数値):
        tag_no         : 反応槽 Tag No.
        初期温度       : T0 [°C]
        目標温度       : T_target [°C]
        仕込み液量     : V_liq_L [L]
        液密度         : density [g/mL]  デフォルト 1.0
        比熱容量       : cp [J/(g·K)]    デフォルト 2.0
        ΔT_offset      : ジャケット温度オフセット [K]  デフォルト ±20
    """
    def _get_str(key: str, default: str = "") -> str:
        """文字列パラメータを取得する。"""
        p = params.get(key, default)
        if isinstance(p, dict):
            return str(p.get("value", default)).strip()
        return str(p).strip() if p else default

    def _get_float(key: str, default: float) -> float:
        """数値パラメータを取得する。"""
        p = params.get(key, default)
        if isinstance(p, dict):
            try:
                return float(p.get("value", default))
            except (TypeError, ValueError):
                return default
        try:
            return float(p)
        except (TypeError, ValueError):
            return default

    try:
        tag_no   = _get_str("tag_no")
        if not tag_no:
            return None

        T0       = _get_float("初期温度",   20.0)
        T_target = _get_float("目標温度",   60.0)
        V_liq_L  = _get_float("仕込み液量", 100.0)
        density  = _get_float("液密度",     1.0)
        cp       = _get_float("比熱容量",   2.0)
        dT_sign  = 1.0 if T_target >= T0 else -1.0
        dT_offset= _get_float("ΔT_offset",  20.0 * dT_sign)

        _HT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "heat_transfer")
        if _HT_DIR not in sys.path:
            sys.path.insert(0, _HT_DIR)
        # src モジュールのキャッシュをクリア（heat_transfer/src を優先）
        for _key in list(sys.modules.keys()):
            if _key == "src" or _key.startswith("src."):
                del sys.modules[_key]

        from src.reactor_db import get_reactor_spec  # type: ignore[import]
        from src.geometry import calc_geometry        # type: ignore[import]
        from src.heat_calc import simulate_inner_control  # type: ignore[import]

        spec   = get_reactor_spec(tag_no)
        geo    = calc_geometry(spec, V_liq_L)
        mass_g = V_liq_L * density * 1000.0
        result = simulate_inner_control(spec, geo, T0, T_target, dT_offset, mass_g, cp)
        if result.t_target_s is not None:
            return result.t_target_s / 60.0
    except Exception:
        pass
    return None


def _calc_filtration_duration(params: dict) -> float | None:
    """Ruth のろ過方程式でろ過所要時間 [分] を返す。

    必要な params キー:
        差圧ΔP      : [MPaG]   デフォルト 0.2
        ろ液粘度μ   : [mPa·s]  デフォルト 1.0
        ケーク比抵抗α: [m/kg]  デフォルト 5e11
        ろ材抵抗Rm  : [m⁻¹]   デフォルト 1e10
        ろ過面積A   : [m²]     デフォルト 1.0
        乾燥ケーキ質量: [g]    デフォルト 1000.0
        総ろ液量    : [L]      デフォルト 100.0
    """
    def _v(key, default):
        p = params.get(key, default)
        if isinstance(p, dict):
            try:
                return float(p.get("value", default))
            except (TypeError, ValueError):
                return float(default)
        try:
            return float(p)
        except (TypeError, ValueError):
            return float(default)

    try:
        dP_MPa   = _v("差圧ΔP",        0.2)
        mu       = _v("ろ液粘度μ",      1.0)
        alpha    = _v("ケーク比抵抗α",  5e11)
        Rm       = _v("ろ材抵抗Rm",     1e10)
        A_m2     = _v("ろ過面積A",       1.0)
        m_cake_g = _v("乾燥ケーキ質量", 1000.0)
        V_total  = _v("総ろ液量",        100.0)

        _FI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "filtration")
        if _FI_DIR not in sys.path:
            sys.path.insert(0, _FI_DIR)
        for _key in list(sys.modules.keys()):
            if _key == "src" or _key.startswith("src."):
                del sys.modules[_key]

        from src.calc import calc_filtration_time_pressure  # type: ignore[import]

        result = calc_filtration_time_pressure(
            delta_P_MPaG=dP_MPa,
            mu_mPas=mu,
            alpha_m_per_kg=alpha,
            Rm_m_inv=Rm,
            A_m2=A_m2,
            m_cake_g=m_cake_g,
            V_total_L=V_total,
        )
        return result.total_time_s / 60.0
    except Exception:
        pass
    return None


def _calc_concentrate_duration(params: dict) -> float | None:
    """濃縮時間推算（手動パラメータ入力ベース）。現時点は手動値にフォールバック。"""
    return None


def _calc_reaction_duration(params: dict) -> float | None:
    """反応時間推算。react_analysis 統合後に実装。"""
    return None


CALC_DISPATCH = {
    "HEAT":        _calc_heat_duration,
    "COOL":        _calc_heat_duration,
    "FILTER":      _calc_filtration_duration,
    "CONCENTRATE": _calc_concentrate_duration,
    "REACTION":    _calc_reaction_duration,
}


def resolve_durations(flow: ManufacturingFlow) -> list[str]:
    """各工程の所要時間を解決する。

    Returns
    -------
    list[str] : 計算失敗 / フォールバック時の警告メッセージ
    """
    warnings: list[str] = []
    for step in flow.steps:
        if step.time_method == TIME_METHOD_MANUAL:
            continue
        calc_fn = CALC_DISPATCH.get(step.op_type)
        if calc_fn is not None:
            result = calc_fn(step.params)
            if result is not None:
                step.duration_min = result
            else:
                if step.manual_duration_min is not None:
                    step.duration_min = step.manual_duration_min
                    warnings.append(
                        f"工程{step.step_no}「{step.name}」: 計算未完了のため"
                        f"手動入力値 {step.manual_duration_min:.0f} 分を使用"
                    )
                else:
                    step.duration_min = 0.0
                    warnings.append(
                        f"工程{step.step_no}「{step.name}」: 計算未完了・手動値なし → 0分で仮置き"
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

def _render_step_gantt(flow: ManufacturingFlow, schedule: dict, start_hour: float):
    """工程単位 Gantt チャート（従来表示）。"""
    fig = go.Figure()
    y_labels = []
    for step in reversed(flow.steps):
        sch = schedule.get(step.step_no, {})
        start_h = (start_hour * 60 + sch.get("start", 0.0)) / 60
        dur_h   = sch.get("duration", 0.0) / 60
        color   = f"#{OP_COLORS.get(step.op_type, 'D5D8DC')}"
        y_label = f"[{step.step_no}] {step.name}"
        y_labels.append(y_label)

        fig.add_trace(go.Bar(
            name=step.op_label,
            x=[dur_h], y=[y_label],
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

    # 凡例ダミートレース
    shown_types: set[str] = set()
    for step in flow.steps:
        if step.op_type not in shown_types:
            shown_types.add(step.op_type)
            fig.add_trace(go.Bar(
                name=step.op_label,
                x=[0], y=[y_labels[0]] if y_labels else [""],
                orientation="h",
                marker_color=f"#{OP_COLORS.get(step.op_type, 'D5D8DC')}",
                showlegend=True, hoverinfo="skip",
            ))

    total_h   = max((v["end"] for v in schedule.values()), default=0.0) / 60
    tick_step = 1.0 if total_h <= 24 else 2.0
    fig.update_layout(
        barmode="overlay",
        xaxis=dict(
            title="時刻 (h)", tickmode="linear",
            tick0=start_hour, dtick=tick_step, tickformat=".1f",
            range=[start_hour, start_hour + total_h + 0.5],
        ),
        yaxis=dict(title=""),
        height=max(300, len(flow.steps) * 36 + 80),
        margin=dict(l=0, r=20, t=20, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_equipment_gantt(flow: ManufacturingFlow, schedule: dict, start_hour: float):
    """機器別並列 Gantt チャート。機器ごとにレーンを設け、各工程バーを描画。"""
    # 機器ごとにステップを集約（挿入順を保持）
    eq_to_steps: dict[str, list] = {}
    for step in flow.steps:
        lane = step.equipment_tag if step.equipment_tag else f"[{step.step_no}] {step.name}"
        eq_to_steps.setdefault(lane, []).append(step)

    if not eq_to_steps:
        st.info("機器 Tag No. が割り当てられていません。")
        return

    fig = go.Figure()
    shown_types: set[str] = set()
    lanes = list(eq_to_steps.keys())

    for lane, steps in eq_to_steps.items():
        for step in steps:
            sch     = schedule.get(step.step_no, {})
            start_h = (start_hour * 60 + sch.get("start", 0.0)) / 60
            dur_h   = sch.get("duration", 0.0) / 60
            color   = f"#{OP_COLORS.get(step.op_type, 'D5D8DC')}"

            fig.add_trace(go.Bar(
                name=step.op_label,
                x=[dur_h], y=[lane],
                base=[start_h],
                orientation="h",
                marker_color=color,
                marker_line_color="#555",
                marker_line_width=0.8,
                text=f"[{step.step_no}] {step.name}" if dur_h >= 0.25 else "",
                textposition="inside",
                insidetextanchor="middle",
                hovertemplate=(
                    f"<b>[{step.step_no}] {step.name}</b><br>"
                    f"操作: {step.op_label}<br>"
                    f"機器: {lane}<br>"
                    f"開始: {_minutes_to_hhmm(start_hour*60 + sch.get('start',0))}<br>"
                    f"終了: {_minutes_to_hhmm(start_hour*60 + sch.get('end',0))}<br>"
                    f"所要: {sch.get('duration',0):.0f} 分<extra></extra>"
                ),
                showlegend=(step.op_type not in shown_types),
            ))
            shown_types.add(step.op_type)

    total_h   = max((v["end"] for v in schedule.values()), default=0.0) / 60
    tick_step = 1.0 if total_h <= 24 else 2.0
    fig.update_layout(
        barmode="overlay",
        xaxis=dict(
            title="時刻 (h)", tickmode="linear",
            tick0=start_hour, dtick=tick_step, tickformat=".1f",
            range=[start_hour, start_hour + total_h + 0.5],
        ),
        yaxis=dict(title="機器", categoryorder="array", categoryarray=list(reversed(lanes))),
        height=max(250, len(lanes) * 60 + 100),
        margin=dict(l=10, r=20, t=20, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_preview(flow: ManufacturingFlow, schedule: dict, start_hour: float):
    """プレビュー表示（集計 + テーブル + Gantt）。"""
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

    # 工程一覧テーブル
    st.subheader("工程一覧")
    rows = []
    for step in flow.steps:
        sch = schedule.get(step.step_no, {})
        start_abs = start_hour * 60 + sch.get("start", 0.0)
        end_abs   = start_hour * 60 + sch.get("end",   0.0)
        dur       = sch.get("duration", 0.0)
        rows.append({
            "工程番号":      step.step_no,
            "工程名":        step.name,
            "操作タイプ":    step.op_label,
            "機器Tag No.":   step.equipment_tag or "-",
            "時間決定":      step.time_method,
            "開始時刻":      _minutes_to_hhmm(start_abs),
            "終了時刻":      _minutes_to_hhmm(end_abs),
            "所要時間(分)":  round(dur, 1),
            "所要時間(h)":   round(dur / 60, 2),
        })
    import pandas as pd
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Gantt チャート
    st.subheader("Gantt チャート")
    tab_step, tab_equip = st.tabs(["工程別", "機器別（並列）"])
    with tab_step:
        try:
            _render_step_gantt(flow, schedule, start_hour)
        except Exception as e:
            st.warning(f"工程別Ganttの描画に失敗: {e}")
    with tab_equip:
        try:
            _render_equipment_gantt(flow, schedule, start_hour)
        except Exception as e:
            st.warning(f"機器別Ganttの描画に失敗: {e}")


# ---------------------------------------------------------------------------
# メイン描画
# ---------------------------------------------------------------------------

def render(tab=None):
    if tab is not None:
        with tab:
            _render_inner()
    else:
        _render_inner()


def _render_inner():
    st.header("タイムテーブル自動作成")

    with st.expander("使い方", expanded=False):
        st.markdown("""
**操作手順:**
1. **テンプレートをダウンロード** → 製造フローと機器 Tag No. を記入
2. **フローファイルをアップロード** → 工程を確認
3. **工程ごとに機器・所要時間を調整**（計算工程は自動推算）
4. **タイムテーブル生成** → Excel をダウンロード

**機器選択について:**
- 加熱・冷却・反応・濃縮工程 → 反応槽（R-xxx）を選択
- ろ過工程 → フィルター/遠心機（F-xxx / C-xxx）を選択
- 伝熱計算・ろ過計算は選択した機器のスペックを使用して自動推算

**時間決定方法:**
- `手動`: 手動時間(分)の値をそのまま使用
- `計算`: HEAT/COOL → 伝熱計算、FILTER → ろ過方程式で推算
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
            st.warning("テンプレートが見つかりません。`python -m timetable.create_templates` を実行してください。")

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

    # ─ 機器リスト取得 ─
    eq_items  = _load_equipment_items()
    reactor_display = [e.display for e in eq_items if e.equip_type == "反応槽"]
    filter_display  = [e.display for e in eq_items if e.equip_type == "フィルター"]
    all_display     = [e.display for e in eq_items]
    display_to_tag  = {e.display: e.tag_no for e in eq_items}
    tag_to_display  = {e.tag_no: e.display for e in eq_items}

    def _tag_to_choices(op_type: str) -> list[str]:
        if op_type in _REACTOR_OPS:
            return ["（未選択）"] + reactor_display
        elif op_type in _FILTER_OPS:
            return ["（未選択）"] + filter_display
        else:
            return ["（未選択）"] + all_display

    # ─ 工程確認・時間調整・機器選択 ─
    st.subheader("③ 工程確認・機器選択・時間調整")

    with st.expander("工程ごとの設定", expanded=True):
        st.caption("計算モジュール未実装の工程は手動入力値が使われます。機器 Tag No. を選択すると伝熱計算・ろ過計算に反映されます。")

        cols_h = st.columns([1, 3, 2, 2, 2, 3])
        for c, h in zip(cols_h, ["#", "工程名", "操作タイプ", "時間決定", "所要時間(分)", "機器 Tag No."]):
            c.markdown(f"**{h}**")

        override_vals: dict[int, float | None] = {}

        for step in flow.steps:
            c1, c2, c3, c4, c5, c6 = st.columns([1, 3, 2, 2, 2, 3])
            c1.markdown(f"**{step.step_no}**")
            c2.markdown(step.name)
            c3.markdown(_op_badge(step.op_type, step.op_label), unsafe_allow_html=True)
            c4.markdown(step.time_method)

            default_val = step.manual_duration_min if step.manual_duration_min is not None else 0.0
            val = c5.number_input(
                "分", min_value=0.0, value=float(default_val), step=1.0,
                key=f"dur_{step.step_no}", label_visibility="collapsed",
            )
            override_vals[step.step_no] = val

            # 機器選択
            choices = _tag_to_choices(step.op_type)
            # デフォルト: Excelから読み込んだ equipment_tag があれば対応するdisplayに
            default_idx = 0
            if step.equipment_tag and step.equipment_tag in tag_to_display:
                disp = tag_to_display[step.equipment_tag]
                if disp in choices:
                    default_idx = choices.index(disp)

            sel_display = c6.selectbox(
                "機器", choices, index=default_idx,
                key=f"eq_{step.step_no}", label_visibility="collapsed",
            )
            # 選択結果を equipment_tag に書き戻す
            step.equipment_tag = display_to_tag.get(sel_display)  # "（未選択）" → None

        # 手動時間の上書きを適用
        for step in flow.steps:
            ov = override_vals.get(step.step_no)
            if ov is not None:
                step._duration_min     = ov
                step.manual_duration_min = ov
            # equipment_tag を params["tag_no"] に注入して計算モジュールから参照できるようにする
            if step.equipment_tag:
                step.params["tag_no"] = step.equipment_tag

    # ─ 製造開始時刻 ─
    st.subheader("④ 製造開始時刻")
    c_start, _ = st.columns([1, 3])
    with c_start:
        start_hour = st.number_input(
            "製造開始時刻 (時)", min_value=0.0, max_value=23.5,
            value=8.0, step=0.5, format="%.1f",
        )

    # ─ タイムテーブル生成 ─
    st.divider()
    st.subheader("⑤ タイムテーブル生成")

    # 計算工程の所要時間を解決
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
