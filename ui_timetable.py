"""
タイムテーブル自動作成 UI

フロー:
  1. テンプレート Excel をダウンロード（操作番号/操作名/前操作番号/操作タイプ の4列）
  2. フローシートを記入してアップロード
  3. 工程ごとに機器 Tag No.・時間・操作内容を調整（追加・削除・編集可）
  4. 「タイムテーブル生成」ボタンで結果を生成
  5. Gantt チャートで機器別に並列可視化 / Excel ダウンロード
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from timetable.flow_reader import (
    ManufacturingFlow,
    OPERATION_TYPES,
    ProcessStep,
    TIME_METHOD_MANUAL,
    TIME_METHOD_CALC,
    read_flow_excel,
    resolve_schedule,
)
from timetable.timetable_writer import OP_COLORS, write_timetable_excel

# ── Capture calculation functions at import time (avoid repeated sys.modules churn) ──
_APP_DIR = os.path.dirname(os.path.abspath(__file__))

_HT_DIR = os.path.join(_APP_DIR, "heat_transfer")
if _HT_DIR in sys.path:
    sys.path.remove(_HT_DIR)
sys.path.insert(0, _HT_DIR)
for _key in list(sys.modules.keys()):
    if _key == "src" or _key.startswith("src."):
        del sys.modules[_key]
try:
    from src.reactor_db import get_reactor_spec as _ht_get_reactor_spec  # type: ignore[import]
    from src.geometry import calc_geometry as _ht_calc_geometry            # type: ignore[import]
    from src.heat_calc import simulate_inner_control as _ht_simulate       # type: ignore[import]
    _HT_AVAILABLE = True
except Exception:
    _HT_AVAILABLE = False

_FI_DIR = os.path.join(_APP_DIR, "filtration")
if _FI_DIR in sys.path:
    sys.path.remove(_FI_DIR)
sys.path.insert(0, _FI_DIR)
for _key in list(sys.modules.keys()):
    if _key == "src" or _key.startswith("src."):
        del sys.modules[_key]
try:
    from src.calc import calc_filtration_time_pressure as _fi_calc  # type: ignore[import]
    _FI_AVAILABLE = True
except Exception:
    _FI_AVAILABLE = False

TEMPLATE_PATH = Path(__file__).parent / "timetable" / "templates" / "flow_template.xlsx"

# FILTER/WASH のみ Filters（フィルター）を選択可能。それ以外は Reactors のみ。
_FILTER_OPS = {"FILTER", "WASH"}

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _minutes_to_day_hhmm(total_abs_min: float) -> str:
    """絶対時刻（分）を Day1/Day2 HH:MM 形式に変換。24 時間超えは Day2+ を表示。"""
    total = int(total_abs_min)
    day = total // (24 * 60) + 1
    hh = (total % (24 * 60)) // 60
    mm = total % 60
    if day > 1:
        return f"Day{day} {hh:02d}:{mm:02d}"
    return f"{hh:02d}:{mm:02d}"


def _get_param_float(skey: str, default: float) -> float:
    """session_state からフロート値を取得する。"""
    v = st.session_state.get(skey)
    if v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    return default


_HOURS = list(range(24))


def _check_equipment_warnings(rows: list[dict]) -> list[str]:
    """計算モードで機器が必要なのに未選択の操作を収集する。"""
    msgs = []
    for row in rows:
        sno = row["step_no"]
        time_method = st.session_state.get(f"edit_method_{sno}", row["time_method"])
        op_type = st.session_state.get(f"edit_op_{sno}", row["op_type"])
        if time_method == TIME_METHOD_CALC and op_type in ("HEAT", "COOL", "FILTER"):
            eq_val = st.session_state.get(f"eq_{sno}", "（未選択）")
            if not eq_val or eq_val == "（未選択）":
                name = st.session_state.get(f"edit_name_{sno}", row["name"])
                msgs.append(
                    f"操作{sno}「{name}」({OPERATION_TYPES.get(op_type, op_type)}) "
                    f"— 機器 Tag No. を選択してください"
                )
    return msgs


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


def _get_filter_area(tag_no: str | None) -> float | None:
    """フィルター Tag No. に対応するろ過面積 [m²] を機器DBから取得する。"""
    if not tag_no:
        return None
    try:
        from heat_transfer.src.equipment_repo import get_equipment_repo
        return get_equipment_repo().get_filter_spec(tag_no).area_m2
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 計算モジュール呼び出し（実装済み）
# ---------------------------------------------------------------------------

def _calc_heat_duration(params: dict) -> float | None:
    """伝熱計算（内温制御）で昇降温所要時間 [分] を返す。

    必要な params キー:
        tag_no         : 反応槽 Tag No.
        初期温度       : T0 [°C]
        目標温度       : T_target [°C]
        仕込み液量     : V_liq_L [L]
        液密度         : density [g/mL]  デフォルト 1.0
        比熱容量       : cp [J/(g·K)]    デフォルト 2.0
        ΔT_offset      : ジャケット温度オフセット [K]  デフォルト ±20
    """
    def _get_str(key: str, default: str = "") -> str:
        p = params.get(key, default)
        if isinstance(p, dict):
            return str(p.get("value", default)).strip()
        return str(p).strip() if p else default

    def _get_float(key: str, default: float) -> float:
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
        if not _HT_AVAILABLE:
            return None
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

        spec   = _ht_get_reactor_spec(tag_no)
        geo    = _ht_calc_geometry(spec, V_liq_L)
        mass_g = V_liq_L * density * 1000.0
        result = _ht_simulate(spec, geo, T0, T_target, dT_offset, mass_g, cp)
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
        if not _FI_AVAILABLE:
            return None
        dP_MPa   = _v("差圧ΔP",        0.2)
        mu       = _v("ろ液粘度μ",      1.0)
        alpha    = _v("ケーク比抵抗α",  5e11)
        Rm       = _v("ろ材抵抗Rm",     1e10)
        A_m2     = _v("ろ過面積A",       1.0)
        m_cake_g = _v("乾燥ケーキ質量", 1000.0)
        V_total  = _v("総ろ液量",        100.0)

        result = _fi_calc(
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
                        f"操作{step.step_no}「{step.name}」: 計算未完了のため"
                        f"手動入力値 {step.manual_duration_min:.0f} 分を使用"
                    )
                else:
                    step.duration_min = 0.0
                    warnings.append(
                        f"操作{step.step_no}「{step.name}」: 計算未完了・手動値なし → 0分で仮置き"
                    )
        else:
            if step.manual_duration_min is not None:
                step.duration_min = step.manual_duration_min
            else:
                step.duration_min = 0.0
                warnings.append(
                    f"操作{step.step_no}「{step.name}」: 対応計算なし・手動値なし → 0分で仮置き"
                )
    return warnings


# ---------------------------------------------------------------------------
# プレビュー表示
# ---------------------------------------------------------------------------

def _render_step_gantt(flow: ManufacturingFlow, schedule: dict, start_hour: float):
    """操作単位 Gantt チャート。"""
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
                f"開始: {_minutes_to_day_hhmm(start_hour*60 + sch.get('start',0))}<br>"
                f"終了: {_minutes_to_day_hhmm(start_hour*60 + sch.get('end',0))}<br>"
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

    total_h = max((v["end"] for v in schedule.values()), default=0.0) / 60

    # 24h 境界の縦線とラベル
    vlines: list[dict] = []
    vlabels: list[dict] = []
    day_h = 24.0
    while day_h <= start_hour + total_h + 0.5:
        if day_h > start_hour:
            vlines.append(dict(
                type="line", x0=day_h, x1=day_h,
                y0=0, y1=1, yref="paper",
                line=dict(color="rgba(50,50,50,0.45)", width=1.5, dash="dash"),
            ))
            day_n = int(day_h // 24) + 1
            vlabels.append(dict(
                x=day_h, y=1.02, xref="x", yref="paper",
                text=f"<b>Day{day_n}</b>", showarrow=False,
                xanchor="left", font=dict(size=9, color="rgba(50,50,50,0.7)"),
            ))
        day_h += 24.0

    fig.update_layout(
        barmode="overlay",
        xaxis=_build_xaxis(start_hour, total_h),
        yaxis=dict(title=""),
        height=max(300, len(flow.steps) * 36 + 80),
        margin=dict(l=0, r=20, t=20, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
        shapes=vlines,
        annotations=vlabels,
    )
    st.plotly_chart(fig, use_container_width=True)


def _build_xaxis(start_hour: float, total_h: float) -> dict:
    """Plotly x 軸設定。24 時間超えは Day1/Day2 ラベル付きのカスタム軸を返す。"""
    xrange = [start_hour, start_hour + total_h + 0.5]
    if total_h > 24:
        tick_vals = []
        tick_text = []
        h = start_hour
        while h <= start_hour + total_h + 0.5:
            tick_vals.append(h)
            day = int(h // 24) + 1
            hour_of_day = int(h % 24)
            tick_text.append(f"Day{day} {hour_of_day:02d}:00")
            h += 2.0
        return dict(
            title="時刻", tickmode="array",
            tickvals=tick_vals, ticktext=tick_text,
            range=xrange,
        )
    tick_step = 1.0 if total_h <= 12 else 2.0
    return dict(
        title="時刻 (h)", tickmode="linear",
        tick0=start_hour, dtick=tick_step, tickformat=".1f",
        range=xrange,
    )


def _render_equipment_gantt(flow: ManufacturingFlow, schedule: dict, start_hour: float):
    """機器別並列 Gantt チャート。機器ごとにレーンを設け、各操作バーを描画。"""
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
                    f"開始: {_minutes_to_day_hhmm(start_hour*60 + sch.get('start',0))}<br>"
                    f"終了: {_minutes_to_day_hhmm(start_hour*60 + sch.get('end',0))}<br>"
                    f"所要: {sch.get('duration',0):.0f} 分<extra></extra>"
                ),
                showlegend=(step.op_type not in shown_types),
            ))
            shown_types.add(step.op_type)

    total_h = max((v["end"] for v in schedule.values()), default=0.0) / 60

    # 24h 境界の縦線とラベル
    eq_vlines: list[dict] = []
    eq_vlabels: list[dict] = []
    day_h = 24.0
    while day_h <= start_hour + total_h + 0.5:
        if day_h > start_hour:
            eq_vlines.append(dict(
                type="line", x0=day_h, x1=day_h,
                y0=0, y1=1, yref="paper",
                line=dict(color="rgba(50,50,50,0.45)", width=1.5, dash="dash"),
            ))
            day_n = int(day_h // 24) + 1
            eq_vlabels.append(dict(
                x=day_h, y=1.02, xref="x", yref="paper",
                text=f"<b>Day{day_n}</b>", showarrow=False,
                xanchor="left", font=dict(size=9, color="rgba(50,50,50,0.7)"),
            ))
        day_h += 24.0

    fig.update_layout(
        barmode="overlay",
        xaxis=_build_xaxis(start_hour, total_h),
        yaxis=dict(title="機器", categoryorder="array", categoryarray=list(reversed(lanes))),
        height=max(250, len(lanes) * 60 + 100),
        margin=dict(l=10, r=20, t=20, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
        shapes=eq_vlines,
        annotations=eq_vlabels,
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_preview(flow: ManufacturingFlow, schedule: dict, start_hour: float):
    """プレビュー表示（集計 + テーブル + Gantt）。"""
    total_min = max((v["end"] for v in schedule.values()), default=0.0)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("総操作数", f"{len(flow.steps)} 操作")
    with col2:
        st.metric("総所要時間", f"{total_min:.0f} 分 ({total_min/60:.1f} h)")
    with col3:
        end_abs = start_hour * 60 + total_min
        st.metric("終了予定時刻", _minutes_to_day_hhmm(end_abs))

    st.divider()

    # 操作一覧テーブル
    st.subheader("操作一覧")
    rows = []
    for step in flow.steps:
        sch = schedule.get(step.step_no, {})
        start_abs = start_hour * 60 + sch.get("start", 0.0)
        end_abs   = start_hour * 60 + sch.get("end",   0.0)
        dur       = sch.get("duration", 0.0)
        rows.append({
            "操作番号":      step.step_no,
            "操作名":        step.name,
            "操作タイプ":    step.op_label,
            "機器Tag No.":   step.equipment_tag or "-",
            "時間決定":      step.time_method,
            "開始時刻":      _minutes_to_day_hhmm(start_abs),
            "終了時刻":      _minutes_to_day_hhmm(end_abs),
            "所要時間(分)":  round(dur, 1),
            "所要時間(h)":   round(dur / 60, 2),
        })
    import pandas as pd
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Gantt チャート
    st.subheader("Gantt チャート")
    tab_step, tab_equip = st.tabs(["操作別", "機器別（並列）"])
    with tab_step:
        try:
            _render_step_gantt(flow, schedule, start_hour)
        except Exception as e:
            st.warning(f"操作別Ganttの描画に失敗: {e}")
    with tab_equip:
        try:
            _render_equipment_gantt(flow, schedule, start_hour)
        except Exception as e:
            st.warning(f"機器別Ganttの描画に失敗: {e}")


# ---------------------------------------------------------------------------
# 行操作ヘルパー
# ---------------------------------------------------------------------------

_ALL_EDIT_PREFIXES = [
    "edit_name_", "edit_prev_", "edit_op_", "edit_method_",
    "dur_", "eq_",
    "ht_t0_", "ht_tt_", "ht_vl_", "ht_dn_", "ht_cp_", "ht_dto_",
    "fi_dP_", "fi_mu_", "fi_al_", "fi_rm_", "fi_mc_", "fi_vt_",
]


def _remap_session_state(old_to_new: dict[int, int], extra_delete_snos: list[int]) -> None:
    """old_to_new に従って _ALL_EDIT_PREFIXES のセッション状態キーを一括で書き換える。
    extra_delete_snos に含まれる sno のキーは削除する（再採番対象外の削除行など）。
    """
    # 変更が必要な行の値を退避（pop で古いキーを消しつつ保存）
    saved: dict[int, dict[str, object]] = {}
    for old_sno, new_sno in old_to_new.items():
        if old_sno != new_sno:
            saved[new_sno] = {}
            for pfx in _ALL_EDIT_PREFIXES:
                k = f"{pfx}{old_sno}"
                if k in st.session_state:
                    saved[new_sno][pfx] = st.session_state.pop(k)

    # extra_delete_snos のキーを削除
    for sno in extra_delete_snos:
        for pfx in _ALL_EDIT_PREFIXES:
            st.session_state.pop(f"{pfx}{sno}", None)

    # 新キーに書き直す
    for new_sno, vals in saved.items():
        for pfx, val in vals.items():
            st.session_state[f"{pfx}{new_sno}"] = val


def _remap_edit_prev(rows: list[dict], old_to_new: dict[int, int]) -> None:
    """全行の edit_prev_{sno} キーを old_to_new に従って更新する。
    old_to_new に含まれない番号（削除済み）への参照は削除する。
    """
    for r in rows:
        sno = r["step_no"]
        key = f"edit_prev_{sno}"
        if key in st.session_state:
            new_prevs = []
            for p in str(st.session_state[key]).split(","):
                p = p.strip()
                try:
                    pn = int(p)
                    if pn in old_to_new:
                        new_prevs.append(str(old_to_new[pn]))
                except ValueError:
                    pass
            new_value = ", ".join(new_prevs)
            st.session_state.pop(key)
            st.session_state[key] = new_value


def _handle_delete_row(idx: int) -> None:
    """idx 番目の行を削除し、以降の操作番号を再採番する。前操作番号参照も更新。"""
    rows = st.session_state["timetable_edit_rows"]
    if idx >= len(rows):
        return

    deleted_sno = rows[idx]["step_no"]

    # 削除後の旧番号→新番号マッピング（削除行は含まない）
    old_to_new: dict[int, int] = {}
    new_no = 1
    for j, r in enumerate(rows):
        if j == idx:
            continue
        old_to_new[r["step_no"]] = new_no
        new_no += 1

    _remap_session_state(old_to_new, extra_delete_snos=[deleted_sno])

    # rows リストを更新
    del rows[idx]
    for r in rows:
        r["step_no"] = old_to_new[r["step_no"]]
        r["prev_steps"] = [old_to_new[p] for p in r["prev_steps"] if p in old_to_new]

    _remap_edit_prev(rows, old_to_new)
    st.session_state["timetable_result"] = None


def _handle_insert_row(after_idx: int) -> None:
    """after_idx 番目の後に新しい操作行を挿入する。以降の操作番号を+1シフト。"""
    rows = st.session_state["timetable_edit_rows"]
    if after_idx >= len(rows):
        # 末尾追加にフォールバック
        after_idx = len(rows) - 1

    base_sno = rows[after_idx]["step_no"]
    new_step_no = base_sno + 1

    # after_idx より後の行を +1 シフト
    old_to_new: dict[int, int] = {}
    for j, r in enumerate(rows):
        old_to_new[r["step_no"]] = r["step_no"] if j <= after_idx else r["step_no"] + 1

    _remap_session_state(old_to_new, extra_delete_snos=[])

    # rows リストを更新（挿入前にシフト）
    for r in rows:
        r["step_no"] = old_to_new[r["step_no"]]
        r["prev_steps"] = [old_to_new.get(p, p) for p in r["prev_steps"]]

    _remap_edit_prev(rows, old_to_new)

    # 新行を挿入（after_idx の後）
    new_row = {
        "step_no":    new_step_no,
        "name":       f"操作{new_step_no}",
        "prev_steps": [base_sno],
        "op_type":    "CHARGE",
        "time_method": TIME_METHOD_MANUAL,
    }
    rows.insert(after_idx + 1, new_row)

    # 新行のセッション状態キーを初期化
    st.session_state[f"edit_name_{new_step_no}"]   = new_row["name"]
    st.session_state[f"edit_prev_{new_step_no}"]   = str(base_sno)
    st.session_state[f"edit_op_{new_step_no}"]     = new_row["op_type"]
    st.session_state[f"edit_method_{new_step_no}"] = new_row["time_method"]
    st.session_state.setdefault(f"dur_{new_step_no}", 0.0)
    st.session_state["timetable_result"] = None


def _handle_add_row() -> None:
    """新しい操作行を末尾に追加する。"""
    rows = st.session_state["timetable_edit_rows"]
    new_sno = len(rows) + 1
    new_row = {
        "step_no":    new_sno,
        "name":       f"操作{new_sno}",
        "prev_steps": [len(rows)] if rows else [],
        "op_type":    "CHARGE",
        "time_method": TIME_METHOD_MANUAL,
    }
    rows.append(new_row)
    # セッション状態キーを初期化
    st.session_state[f"edit_name_{new_sno}"]   = new_row["name"]
    st.session_state[f"edit_prev_{new_sno}"]   = ", ".join(str(p) for p in new_row["prev_steps"])
    st.session_state[f"edit_op_{new_sno}"]     = new_row["op_type"]
    st.session_state[f"edit_method_{new_sno}"] = new_row["time_method"]
    st.session_state.setdefault(f"dur_{new_sno}", 0.0)
    st.session_state["timetable_result"] = None


def _build_flow_from_state(display_to_tag: dict) -> ManufacturingFlow:
    """セッション状態の編集情報から ManufacturingFlow を構築する。"""
    rows = st.session_state["timetable_edit_rows"]
    steps = []
    for row in rows:
        sno = row["step_no"]
        name = str(st.session_state.get(f"edit_name_{sno}", row["name"])).strip() or f"操作{sno}"
        prev_raw = st.session_state.get(f"edit_prev_{sno}", "")
        prev_steps: list[int] = []
        for p in str(prev_raw).split(","):
            p = p.strip()
            try:
                prev_steps.append(int(p))
            except ValueError:
                pass

        op_type = st.session_state.get(f"edit_op_{sno}", row["op_type"])
        if op_type not in OPERATION_TYPES:
            op_type = "OTHER"
        time_method = st.session_state.get(f"edit_method_{sno}", row["time_method"])
        if time_method not in (TIME_METHOD_MANUAL, TIME_METHOD_CALC):
            time_method = TIME_METHOD_MANUAL

        manual_dur_raw = st.session_state.get(f"dur_{sno}", None)
        try:
            manual_dur: float | None = float(manual_dur_raw) if manual_dur_raw is not None else None
        except (TypeError, ValueError):
            manual_dur = None

        step = ProcessStep(
            step_no=sno,
            name=name,
            op_type=op_type,
            prev_steps=prev_steps,
            time_method=time_method,
            manual_duration_min=manual_dur,
            params={},
            note="",
            equipment_tag=None,
        )

        # 機器 Tag No. を設定
        eq_display = st.session_state.get(f"eq_{sno}", "（未選択）")
        step.equipment_tag = display_to_tag.get(eq_display)

        # 計算パラメータを設定
        if time_method == TIME_METHOD_CALC and op_type in ("HEAT", "COOL"):
            step.params = {
                "tag_no":    step.equipment_tag,
                "初期温度":  _get_param_float(f"ht_t0_{sno}", 20.0),
                "目標温度":  _get_param_float(f"ht_tt_{sno}", 80.0),
                "仕込み液量": _get_param_float(f"ht_vl_{sno}", 100.0),
                "液密度":    _get_param_float(f"ht_dn_{sno}", 1.0),
                "比熱容量":  _get_param_float(f"ht_cp_{sno}", 2.0),
                "ΔT_offset": _get_param_float(f"ht_dto_{sno}", 20.0),
            }
        elif time_method == TIME_METHOD_CALC and op_type == "FILTER":
            _fi_eq_display = st.session_state.get(f"eq_{sno}", "（未選択）")
            _fi_tag = display_to_tag.get(_fi_eq_display)
            _fi_area = _get_filter_area(_fi_tag) or 1.0
            step.params = {
                "差圧ΔP":         _get_param_float(f"fi_dP_{sno}",   0.2),
                "ろ液粘度μ":      _get_param_float(f"fi_mu_{sno}",   1.0),
                "ケーク比抵抗α":  _get_param_float(f"fi_al_{sno}",   5e11),
                "ろ材抵抗Rm":     _get_param_float(f"fi_rm_{sno}",   1e10),
                "ろ過面積A":      _fi_area,
                "乾燥ケーキ質量": _get_param_float(f"fi_mc_{sno}",   1000.0),
                "総ろ液量":       _get_param_float(f"fi_vt_{sno}",   100.0),
            }

        steps.append(step)

    return ManufacturingFlow(steps=steps)


# ---------------------------------------------------------------------------
# メイン描画
# ---------------------------------------------------------------------------

def render():
    _render_inner()


_TIMETABLE_RESET_PREFIXES = (
    "timetable_", "dur_", "eq_",
    "edit_name_", "edit_prev_", "edit_op_", "edit_method_",
    "ht_t0_", "ht_tt_", "ht_vl_", "ht_dn_", "ht_cp_", "ht_dto_",
    "fi_dP_", "fi_mu_", "fi_al_", "fi_rm_", "fi_area_", "fi_mc_", "fi_vt_",
)


def _init_timetable_state() -> None:
    defaults = {
        "timetable_file_key":  None,   # "filename+size" 文字列
        "timetable_edit_rows": [],     # 編集可能な操作行リスト
        "timetable_result":    None,   # 生成済みタイムテーブルデータ
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _render_inner():
    _init_timetable_state()
    _col_hdr, _col_rst = st.columns([9, 1])
    with _col_hdr:
        st.header("タイムテーブル自動作成")
    with _col_rst:
        if st.button("リセット", key="tt_reset_btn"):
            for _k in list(st.session_state.keys()):
                if any(_k.startswith(_p) for _p in _TIMETABLE_RESET_PREFIXES):
                    del st.session_state[_k]
            st.rerun()

    with st.expander("使い方", expanded=False):
        st.markdown("""
**操作手順:**
1. **テンプレートをダウンロード** → 操作番号・操作名・前操作番号・操作タイプを記入
2. **フローファイルをアップロード** → 操作一覧が読み込まれる
3. **③で操作を確認・編集**（追加・削除・機器選択・時間設定）
4. **④で製造開始時刻を設定し「タイムテーブル生成」をクリック**
5. **⑤でタイムテーブル・Ganttを確認** → Excel をダウンロード

**機器選択について:**
- ろ過・洗浄（FILTER / WASH）→ フィルター（F-xxx / C-xxx）を選択
- その他の操作 → 反応槽（R-xxx）を選択
- 伝熱計算・ろ過計算は選択した機器スペックを使用して自動推算

**時間決定方法:**
- `手動`: 所要時間(分)の値をそのまま使用
- `計算`: HEAT/COOL → 伝熱計算、FILTER → ろ過方程式で推算（デフォルト）
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

    # 新規ファイル検出 → 操作編集状態リセット & edit_rows を初期化
    if uploaded is not None:
        file_key = uploaded.name + str(uploaded.size)
        if file_key != st.session_state["timetable_file_key"]:
            for _k in list(st.session_state.keys()):
                if any(_k.startswith(_p) for _p in _TIMETABLE_RESET_PREFIXES):
                    del st.session_state[_k]
            try:
                flow_obj = read_flow_excel(io.BytesIO(uploaded.read()))
            except Exception as e:
                st.error(f"ファイルの読み込みに失敗しました: {e}")
                return
            st.session_state["timetable_file_key"] = file_key
            st.session_state["timetable_edit_rows"] = [
                {
                    "step_no":    s.step_no,
                    "name":       s.name,
                    "prev_steps": list(s.prev_steps),
                    "op_type":    s.op_type,
                    "time_method": s.time_method,
                }
                for s in flow_obj.steps
            ]
            st.session_state["timetable_result"] = None

    rows = st.session_state.get("timetable_edit_rows", [])
    if not rows:
        st.info("製造フロー Excel をアップロードしてください。")
        return

    st.success(f"{len(rows)} 操作を読み込みました。")
    st.divider()

    # ─ 機器リスト取得 ─
    eq_items        = _load_equipment_items()
    reactor_display = [e.display for e in eq_items if e.equip_type == "反応槽"]
    filter_display  = [e.display for e in eq_items if e.equip_type == "フィルター"]
    display_to_tag  = {e.display: e.tag_no for e in eq_items}

    def _tag_to_choices(op_type: str) -> list[str]:
        if op_type in _FILTER_OPS:
            return ["（未選択）"] + filter_display
        else:
            return ["（未選択）"] + reactor_display

    # ─ セッション状態キーの初期化（未設定のもののみ）─
    for row in rows:
        sno = row["step_no"]
        st.session_state.setdefault(f"edit_name_{sno}",   row["name"])
        st.session_state.setdefault(f"edit_prev_{sno}",   ", ".join(str(p) for p in row["prev_steps"]))
        st.session_state.setdefault(f"edit_op_{sno}",     row["op_type"])
        st.session_state.setdefault(f"edit_method_{sno}", row["time_method"])
        st.session_state.setdefault(f"dur_{sno}",         0.0)

    # ─ 操作確認・機器選択・時間調整 ─
    st.subheader("③ 操作確認・機器選択・時間調整")

    with st.expander("操作ごとの設定", expanded=True):
        st.caption(
            "各操作の情報を確認・編集し、④の「タイムテーブル生成」ボタンを押してください。"
            "「計算」操作は下の計算パラメータ欄に値を入力してください。"
        )

        # ヘッダー行
        cols_h = st.columns([0.7, 0.7, 0.4, 2.5, 1.5, 2.2, 1.5, 1.5, 2.2])
        for c, h in zip(cols_h, ["", "", "#", "操作名", "前操作番号", "操作タイプ", "状態", "時間(分)", "機器 Tag No."]):
            c.markdown(f"**{h}**")

        delete_idx: int | None = None
        insert_after_idx: int | None = None

        for idx, row in enumerate(rows):
            sno = row["step_no"]
            c_del, c_ins, c_no, c_name, c_prev, c_op, c_method, c_dur, c_eq = st.columns(
                [0.7, 0.7, 0.4, 2.5, 1.5, 2.2, 1.5, 1.5, 2.2]
            )

            # 削除ボタン
            if c_del.button("➖", key=f"del_{sno}", help="この操作を削除"):
                delete_idx = idx

            # 挿入ボタン
            if c_ins.button("➕", key=f"ins_{sno}", help="この操作の後に挿入"):
                insert_after_idx = idx

            # 操作番号（表示のみ）
            c_no.markdown(f"<div style='padding-top:8px;font-weight:600;text-align:center;'>{sno}</div>",
                          unsafe_allow_html=True)

            # 操作名
            c_name.text_input("操作名", key=f"edit_name_{sno}", label_visibility="collapsed")

            # 前操作番号
            c_prev.text_input("前操作番号", key=f"edit_prev_{sno}", label_visibility="collapsed",
                              placeholder="例: 1,2")

            # 操作タイプ
            op_types_list = list(OPERATION_TYPES.keys())
            current_op = st.session_state.get(f"edit_op_{sno}", row["op_type"])
            if current_op not in op_types_list:
                current_op = "OTHER"
                st.session_state[f"edit_op_{sno}"] = current_op
            c_op.selectbox(
                "操作タイプ", op_types_list,
                format_func=lambda x: f"{x} ({OPERATION_TYPES.get(x, x)})",
                key=f"edit_op_{sno}", label_visibility="collapsed",
            )

            # 状態（手動/計算）
            method_options = [TIME_METHOD_MANUAL, TIME_METHOD_CALC]
            current_method = st.session_state.get(f"edit_method_{sno}", row["time_method"])
            if current_method not in method_options:
                current_method = TIME_METHOD_MANUAL
                st.session_state[f"edit_method_{sno}"] = current_method
            c_method.selectbox(
                "状態", method_options,
                key=f"edit_method_{sno}", label_visibility="collapsed",
            )

            # 所要時間（分）
            c_dur.number_input(
                "時間(分)", min_value=0.0, step=1.0,
                key=f"dur_{sno}", label_visibility="collapsed",
            )

            # 機器 Tag No. — op_type が変わっても有効な選択肢か確認
            current_op_val = st.session_state.get(f"edit_op_{sno}", row["op_type"])
            choices = _tag_to_choices(current_op_val)
            current_eq = st.session_state.get(f"eq_{sno}", "（未選択）")
            if current_eq not in choices:
                st.session_state[f"eq_{sno}"] = "（未選択）"
            c_eq.selectbox(
                "機器", choices,
                key=f"eq_{sno}", label_visibility="collapsed",
            )

            # ── 計算パラメータ（HEAT / COOL） ──
            current_method_val = st.session_state.get(f"edit_method_{sno}", row["time_method"])
            if current_method_val == TIME_METHOD_CALC and current_op_val in ("HEAT", "COOL"):
                eq_display = st.session_state.get(f"eq_{sno}", "（未選択）")
                eq_tag = display_to_tag.get(eq_display)
                _ht_params = {
                    "tag_no":    eq_tag,
                    "初期温度":  _get_param_float(f"ht_t0_{sno}", 20.0),
                    "目標温度":  _get_param_float(f"ht_tt_{sno}", 80.0),
                    "仕込み液量": _get_param_float(f"ht_vl_{sno}", 100.0),
                    "液密度":    _get_param_float(f"ht_dn_{sno}", 1.0),
                    "比熱容量":  _get_param_float(f"ht_cp_{sno}", 2.0),
                    "ΔT_offset": _get_param_float(f"ht_dto_{sno}", 20.0),
                }
                _ht_preview = _calc_heat_duration(_ht_params)
                _step_name = st.session_state.get(f"edit_name_{sno}", row["name"])
                if _ht_preview is not None:
                    _exp_label = f"操作{sno}「{_step_name}」 計算パラメータ  ✅ {_ht_preview:.1f} 分"
                else:
                    _exp_label = f"操作{sno}「{_step_name}」 計算パラメータ  ⚠️ 未計算"
                with st.expander(_exp_label, expanded=False):
                    p1, p2, p3 = st.columns(3)
                    st.session_state.setdefault(f"ht_t0_{sno}", 20.0)
                    st.session_state.setdefault(f"ht_tt_{sno}", 80.0)
                    st.session_state.setdefault(f"ht_vl_{sno}", 100.0)
                    st.session_state.setdefault(f"ht_dn_{sno}", 1.0)
                    st.session_state.setdefault(f"ht_cp_{sno}", 2.0)
                    t0_cur = float(st.session_state.get(f"ht_t0_{sno}", 20.0))
                    t1_cur = float(st.session_state.get(f"ht_tt_{sno}", 80.0))
                    dT_sign = 1.0 if t1_cur >= t0_cur else -1.0
                    st.session_state.setdefault(f"ht_dto_{sno}", 20.0 * dT_sign)
                    p1.number_input("初期温度 [°C]",          key=f"ht_t0_{sno}")
                    p2.number_input("目標温度 [°C]",          key=f"ht_tt_{sno}")
                    p3.number_input("仕込み液量 [L]", min_value=0.1, key=f"ht_vl_{sno}")
                    p4, p5, p6 = st.columns(3)
                    p4.number_input("液密度 [g/mL]",   min_value=0.1, key=f"ht_dn_{sno}")
                    p5.number_input("比熱容量 [J/(g·K)]", min_value=0.1, key=f"ht_cp_{sno}")
                    p6.number_input("ΔT_offset [K]",       key=f"ht_dto_{sno}")

            # ── 計算パラメータ（FILTER） ──
            elif current_method_val == TIME_METHOD_CALC and current_op_val == "FILTER":
                _fi_eq_display = st.session_state.get(f"eq_{sno}", "（未選択）")
                _fi_tag = display_to_tag.get(_fi_eq_display)
                _fi_area_db = _get_filter_area(_fi_tag)
                _fi_area = _fi_area_db if _fi_area_db is not None else 1.0
                _fi_params = {
                    "差圧ΔP":         _get_param_float(f"fi_dP_{sno}",   0.2),
                    "ろ液粘度μ":      _get_param_float(f"fi_mu_{sno}",   1.0),
                    "ケーク比抵抗α":  _get_param_float(f"fi_al_{sno}",   5e11),
                    "ろ材抵抗Rm":     _get_param_float(f"fi_rm_{sno}",   1e10),
                    "ろ過面積A":      _fi_area,
                    "乾燥ケーキ質量": _get_param_float(f"fi_mc_{sno}",   1000.0),
                    "総ろ液量":       _get_param_float(f"fi_vt_{sno}",   100.0),
                }
                _fi_preview = _calc_filtration_duration(_fi_params)
                _step_name = st.session_state.get(f"edit_name_{sno}", row["name"])
                if _fi_preview is not None:
                    _fi_exp_label = f"操作{sno}「{_step_name}」 計算パラメータ  ✅ {_fi_preview:.1f} 分"
                else:
                    _fi_exp_label = f"操作{sno}「{_step_name}」 計算パラメータ  ⚠️ 未計算"
                with st.expander(_fi_exp_label, expanded=False):
                    if _fi_area_db is not None:
                        st.info(f"ろ過面積: {_fi_area_db} m²（機器DBより自動取得: {_fi_tag}）")
                    else:
                        st.warning("機器 Tag No. を選択するとろ過面積が自動設定されます。現在は 1.0 m² で計算しています。")
                    st.session_state.setdefault(f"fi_dP_{sno}",   0.2)
                    st.session_state.setdefault(f"fi_mu_{sno}",   1.0)
                    st.session_state.setdefault(f"fi_al_{sno}",   5e11)
                    st.session_state.setdefault(f"fi_rm_{sno}",   1e10)
                    st.session_state.setdefault(f"fi_mc_{sno}",   1000.0)
                    st.session_state.setdefault(f"fi_vt_{sno}",   100.0)
                    p1, p2, p3 = st.columns(3)
                    p1.number_input("差圧ΔP [MPaG]",      min_value=0.001, format="%.3f", key=f"fi_dP_{sno}")
                    p2.number_input("ろ液粘度μ [mPa·s]",  min_value=0.01,  format="%.3f", key=f"fi_mu_{sno}")
                    p3.number_input("ケーク比抵抗α [m/kg]",              format="%e",   key=f"fi_al_{sno}")
                    p4, p5, p6 = st.columns(3)
                    p4.number_input("ろ材抵抗Rm [m⁻¹]",                  format="%e",   key=f"fi_rm_{sno}")
                    p5.number_input("乾燥ケーキ質量 [g]", min_value=0.0,                  key=f"fi_mc_{sno}")
                    p6.number_input("総ろ液量 [L]",        min_value=0.1,                  key=f"fi_vt_{sno}")

        # 削除処理
        if delete_idx is not None:
            _handle_delete_row(delete_idx)
            st.rerun()

        # 挿入処理
        if insert_after_idx is not None:
            _handle_insert_row(insert_after_idx)
            st.rerun()

        # 末尾追加ボタン
        if st.button("＋ 操作を末尾に追加", key="tt_add_step"):
            _handle_add_row()
            st.rerun()

    # ─ 製造開始時刻 + タイムテーブル生成 ─
    st.subheader("④ 製造開始時刻・タイムテーブル生成")
    c_h, c_m, c_disp, c_btn = st.columns([1, 1, 1, 2])
    with c_h:
        _start_h = st.selectbox("時", _HOURS, index=8, key="tt_start_hour")
    with c_m:
        _start_m = st.selectbox("分", [0, 30], index=0, key="tt_start_min",
                                 format_func=lambda x: f"{x:02d}")
    start_hour = _start_h + _start_m / 60.0
    c_disp.markdown(
        f'<div style="padding-top:28px;font-size:1.2em;font-weight:600;">'
        f'🕐 {_start_h:02d}:{_start_m:02d}</div>',
        unsafe_allow_html=True,
    )
    with c_btn:
        st.markdown('<div style="padding-top:20px;">', unsafe_allow_html=True)
        generate_clicked = st.button(
            "📊 タイムテーブル生成", key="tt_generate_btn",
            type="primary", use_container_width=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

    if generate_clicked:
        # 機器未選択チェック
        eq_warns = _check_equipment_warnings(rows)
        if eq_warns:
            for w in eq_warns:
                st.warning(w)

        # フローを構築して計算
        try:
            flow = _build_flow_from_state(display_to_tag)
        except Exception as e:
            st.error(f"フロー構築に失敗しました: {e}")
            st.session_state["timetable_result"] = None
        else:
            calc_warnings = resolve_durations(flow)
            schedule = resolve_schedule(flow)
            st.session_state["timetable_result"] = {
                "flow":       flow,
                "schedule":   schedule,
                "start_hour": start_hour,
                "warnings":   calc_warnings,
            }

    # ─ タイムテーブル表示 ─
    st.divider()
    st.subheader("⑤ タイムテーブル")

    result = st.session_state.get("timetable_result")
    if result is None:
        st.info("④の「タイムテーブル生成」ボタンを押すと結果が表示されます。")
        return

    flow     = result["flow"]
    schedule = result["schedule"]
    s_hour   = result["start_hour"]
    for w in result.get("warnings", []):
        st.warning(w)

    # プレビュー
    _render_preview(flow, schedule, s_hour)

    # Excel ダウンロード
    st.divider()
    try:
        excel_bytes = write_timetable_excel(flow, start_hour=s_hour)
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
