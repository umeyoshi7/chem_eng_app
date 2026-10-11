"""タイムテーブルツール: フロー Excel 読込 / 工程リスト → 時間推算 → タイムテーブル Excel・Gantt"""

from __future__ import annotations

import io

import plotly.graph_objects as go

from .common import (
    AMOUNT_UNIT, SOLVENT_AMOUNTS, ToolError, equipment_repo, fmt_min, get_filter_area,
    get_reactor, num, parse_solvent_amounts, r, string, tool,
)

from timetable.flow_reader import (
    OPERATION_TYPES, TIME_METHOD_CALC, TIME_METHOD_MANUAL, ManufacturingFlow, ProcessStep,
    read_flow_excel, resolve_schedule,
)
from timetable.timetable_writer import OP_COLORS, write_timetable_excel


@tool(
    "read_flow_excel",
    "添付された製造フロー Excel（flow テンプレート形式）を読み込み、工程リスト"
    "（操作番号・操作名・操作タイプ・前操作番号）を返す。返した工程に機器・時間・計算パラメータを"
    "補って create_timetable に渡す。",
    {"file_name": string("添付ファイル名")},
    ["file_name"],
)
def read_flow_excel_tool(ws, file_name: str) -> dict:
    data = ws.get_input_file(file_name)
    try:
        flow = read_flow_excel(io.BytesIO(data))
    except Exception as e:
        raise ToolError(f"フロー Excel を読み込めません: {e}") from e
    if not flow.steps:
        raise ToolError("フローシートに工程がありません。")
    return {"steps": [{"step_no": s.step_no, "name": s.name, "op_type": s.op_type,
                       "prev_steps": s.prev_steps} for s in flow.steps]}


# ---------------------------------------------------------------------------
# 工程ごとの時間推算
# ---------------------------------------------------------------------------

_CALC_KEY = {"HEAT": "heat", "COOL": "heat", "FILTER": "filter", "CONCENTRATE": "concentrate"}


def _calc_heat(step: dict, running_temp: float) -> tuple[float, str, float]:
    from .process import _liquid_props, heat_time
    p = step["heat"]
    if not step.get("equipment_tag"):
        raise ToolError("昇降温の計算には equipment_tag（反応槽）が必要です。")
    reactor = get_reactor(step["equipment_tag"])
    if p.get("liquid_volume_L") is None or p.get("target_temp_C") is None:
        raise ToolError("heat には liquid_volume_L と target_temp_C が必要です。")
    T0 = p.get("initial_temp_C", running_temp)
    T1 = p["target_temp_C"]
    dens, cp, _ = _liquid_props(p.get("solvent"), p.get("density_g_mL"), p.get("cp_J_gK"), T0)
    _, res = heat_time(reactor, p["liquid_volume_L"], dens, cp, T0, T1, p.get("dT_offset_K"))
    if not res.t_target_s:
        raise ToolError("; ".join(res.notes) or "昇降温時間を計算できません。")
    basis = (f"伝熱計算 {reactor.tag_no} {p['liquid_volume_L']:g}L {T0:g}→{T1:g}°C "
             f"(ρ={dens:.3g}, cp={cp:.3g}, ΔT={res.T_jacket[0] - res.T_inner[0]:+g}K)")
    return res.t_target_s / 60.0, basis, T1


def _calc_filter(step: dict) -> tuple[float, str]:
    from filtration.src.calc import calc_filtration_time_pressure
    p = step["filter"]
    if step.get("equipment_tag"):
        A, _ = get_filter_area(step["equipment_tag"])
    elif p.get("area_m2"):
        A = float(p["area_m2"])
    else:
        raise ToolError("ろ過の計算には equipment_tag（フィルター）か area_m2 が必要です。")
    dP = p.get("pressure_MPa", 0.2)
    alpha = p.get("alpha_m_kg", 5e11)
    try:
        res = calc_filtration_time_pressure(
            dP, p.get("viscosity_mPas", 1.0), alpha, p.get("medium_resistance_m_inv", 1e10),
            A, p["dry_cake_mass_g"], p["filtrate_volume_L"])
    except KeyError as e:
        raise ToolError(f"filter に {e} が必要です。") from e
    except ValueError as e:
        raise ToolError(str(e)) from e
    basis = (f"Ruth式 A={A:g}m² ΔP={dP:g}MPa α={alpha:.2e} "
             f"ケーキ{p['dry_cake_mass_g']:g}g ろ液{p['filtrate_volume_L']:g}L")
    return res.total_time_s / 60.0, basis


def _calc_concentrate(step: dict) -> tuple[float, str]:
    from .process import concentration_time_core
    p = step["concentrate"]
    if not step.get("equipment_tag"):
        raise ToolError("濃縮の計算には equipment_tag（反応槽）が必要です。")
    reactor = get_reactor(step["equipment_tag"])
    try:
        sols, moles = parse_solvent_amounts(p["components"], p["unit"], 25.0, n_min=2, n_max=4)
        core = concentration_time_core(sols, moles, p["pressure_kPa"], reactor,
                                       p["jacket_temp_C"], p["target_evap_fraction"])
    except KeyError as e:
        raise ToolError(f"concentrate に {e} が必要です。") from e
    if core["t_target_min"] is None:
        raise ToolError("目標蒸発率まで計算が到達しませんでした（平衡計算の収束限界、またはジャケット温度不足）。")
    basis = (f"濃縮時間推算 {reactor.tag_no} {p['pressure_kPa']:g}kPa "
             f"ジャケット{p['jacket_temp_C']:g}°C 蒸発率{p['target_evap_fraction']:g}")
    return core["t_target_min"], basis


def _day_hhmm(abs_min: float) -> str:
    total = int(round(abs_min))
    day, rem = divmod(total, 24 * 60)
    s = f"{rem // 60:02d}:{rem % 60:02d}"
    return f"Day{day + 1} {s}" if day else s


def _gantt(flow: ManufacturingFlow, schedule: dict, start_hour: float) -> go.Figure:
    fig = go.Figure()
    seen = set()
    labels = [f"{s.step_no}. {s.name}" for s in flow.steps]
    for s, label in zip(flow.steps, labels):
        sc = schedule[s.step_no]
        start_h = start_hour + sc["start"] / 60.0
        fig.add_trace(go.Bar(
            y=[label], x=[sc["duration"] / 60.0], base=[start_h], orientation="h",
            marker_color="#" + OP_COLORS.get(s.op_type, "D5D8DC"),
            name=s.op_label, legendgroup=s.op_type, showlegend=s.op_type not in seen,
            hovertemplate=(f"{label}<br>{_day_hhmm(start_hour * 60 + sc['start'])} → "
                           f"{_day_hhmm(start_hour * 60 + sc['end'])}<br>{fmt_min(sc['duration'])}"
                           f"<br>機器: {s.equipment_tag or '-'}<extra></extra>"),
        ))
        seen.add(s.op_type)
    fig.update_yaxes(autorange="reversed", categoryorder="array", categoryarray=labels)
    fig.update_layout(title="タイムテーブル（Gantt）", xaxis_title="時刻 [h]（24 超は翌日）",
                      barmode="overlay", height=max(300, 40 * len(labels) + 120))
    return fig


_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "step_no": {"type": "integer", "description": "操作番号（1 から連番）"},
        "name": string("操作名"),
        "op_type": string("操作タイプ", list(OPERATION_TYPES)),
        "prev_steps": {"type": "array", "items": {"type": "integer"},
                       "description": "前操作番号（並列合流は複数、先頭工程は空）"},
        "equipment_tag": string("使用機器 Tag No.（反応槽 R-xxx / フィルター F-xxx, C-xxx）"),
        "duration_min": num("手動の所要時間 [min]（計算パラメータがない工程は必須、計算失敗時の代替値）"),
        "heat": {"type": "object", "description": "HEAT/COOL の伝熱計算パラメータ",
                 "properties": {
                     "initial_temp_C": num("初期温度（省略時は直前の昇降温工程の到達温度、なければ20°C）"),
                     "target_temp_C": num("目標温度 [°C]"),
                     "liquid_volume_L": num("液量 [L]"),
                     "solvent": string("溶媒名（密度・比熱を補完）"),
                     "density_g_mL": num("液密度"), "cp_J_gK": num("比熱"),
                     "dT_offset_K": num("ジャケット温度オフセット [K]")},
                 "required": ["target_temp_C", "liquid_volume_L"]},
        "filter": {"type": "object", "description": "FILTER の加圧ろ過計算パラメータ",
                   "properties": {
                       "pressure_MPa": num("差圧（既定0.2）"), "viscosity_mPas": num("粘度（既定1.0）"),
                       "alpha_m_kg": num("ケーキ比抵抗（既定5e11）"),
                       "medium_resistance_m_inv": num("ろ材抵抗（既定1e10）"),
                       "area_m2": num("ろ過面積（equipment_tag がフィルターなら不要）"),
                       "dry_cake_mass_g": num("乾燥ケーキ質量 [g]"),
                       "filtrate_volume_L": num("ろ液量 [L]")},
                   "required": ["dry_cake_mass_g", "filtrate_volume_L"]},
        "concentrate": {"type": "object", "description": "CONCENTRATE の濃縮時間計算パラメータ",
                        "properties": {
                            "components": SOLVENT_AMOUNTS, "unit": AMOUNT_UNIT,
                            "pressure_kPa": num("操作圧力 [kPa abs]"),
                            "jacket_temp_C": num("ジャケット温度 [°C]"),
                            "target_evap_fraction": num("目標蒸発率 0〜1")},
                        "required": ["components", "unit", "pressure_kPa", "jacket_temp_C",
                                     "target_evap_fraction"]},
        "note": string("備考"),
    },
    "required": ["step_no", "name", "op_type", "prev_steps"],
}


@tool(
    "create_timetable",
    "工程リストから各工程の所要時間を決定（HEAT/COOL=伝熱計算, FILTER=ろ過計算, CONCENTRATE=濃縮時間"
    "推算, その他=duration_min）し、開始・終了時刻を求めてタイムテーブル Excel（一覧・工程Gantt・"
    "機器別Gantt）と Gantt グラフを出力する。修正時は全工程を再送する。",
    {
        "steps": {"type": "array", "items": _STEP_SCHEMA},
        "start_hour": num("製造開始時刻 [時]（既定 8）"),
        "title": string("タイムテーブル名（ファイル名に使用）"),
    },
    ["steps"],
)
def create_timetable(ws, steps: list, start_hour: float = 8.0, title: str = "timetable") -> dict:
    if not steps:
        raise ToolError("工程がありません。")
    steps = sorted(steps, key=lambda s: int(s["step_no"]))
    nos = [int(s["step_no"]) for s in steps]
    if len(set(nos)) != len(nos):
        raise ToolError("操作番号が重複しています。")

    known_tags = {it.tag_no for it in equipment_repo().list_all()}
    warnings: list[str] = []
    rows: list[dict] = []
    flow = ManufacturingFlow()
    running_temp = 20.0

    for st in steps:
        sno = int(st["step_no"])
        op = str(st["op_type"]).upper()
        if op not in OPERATION_TYPES:
            warnings.append(f"操作{sno}: 操作タイプ '{op}' は未対応のため OTHER として扱います。")
            op = "OTHER"
        prev = [int(p) for p in st.get("prev_steps") or []]
        bad_prev = [p for p in prev if p not in nos or p >= sno]
        if bad_prev:
            raise ToolError(f"操作{sno}: 前操作番号 {bad_prev} が不正です（存在し、かつ自分より前の番号であること）。")
        tag = st.get("equipment_tag") or None
        if tag and tag not in known_tags:
            warnings.append(f"操作{sno}: 機器 '{tag}' は機器DBに存在しません。")

        manual = st.get("duration_min")
        duration, method, basis = None, TIME_METHOD_MANUAL, ""
        key = _CALC_KEY.get(op)
        if key and st.get(key):
            try:
                if key == "heat":
                    duration, basis, running_temp = _calc_heat(st, running_temp)
                elif key == "filter":
                    duration, basis = _calc_filter(st)
                else:
                    duration, basis = _calc_concentrate(st)
                method = TIME_METHOD_CALC
            except ToolError as e:
                warnings.append(f"操作{sno}「{st['name']}」: 計算失敗（{e}）")
        if duration is None:
            if manual is not None:
                duration, basis = float(manual), basis or "手動入力"
                if method == TIME_METHOD_MANUAL and key and st.get(key):
                    basis = "計算失敗のため手動値"
            else:
                duration, basis = 0.0, "未設定（0分で仮置き）"
                warnings.append(f"操作{sno}「{st['name']}」: 所要時間が未設定のため 0 分で仮置きしました。")

        step = ProcessStep(step_no=sno, name=st["name"], op_type=op, prev_steps=prev,
                           time_method=method, manual_duration_min=manual, params={},
                           note=st.get("note", ""), equipment_tag=tag)
        step.duration_min = duration
        flow.steps.append(step)
        rows.append({"basis": basis, "method": method})

    schedule = resolve_schedule(flow)

    # 同一機器の時間重複チェック（並列工程での機器競合）
    by_tag: dict[str, list] = {}
    for s in flow.steps:
        if s.equipment_tag:
            by_tag.setdefault(s.equipment_tag, []).append(s)
    for tag, ss in by_tag.items():
        ss = sorted(ss, key=lambda s: schedule[s.step_no]["start"])
        for a, b in zip(ss, ss[1:]):
            if schedule[b.step_no]["start"] < schedule[a.step_no]["end"] - 1e-6:
                warnings.append(f"機器 {tag}: 操作{a.step_no} と 操作{b.step_no} の使用時間が重複しています。")

    offset = start_hour * 60.0
    table = []
    for s, extra in zip(flow.steps, rows):
        sc = schedule[s.step_no]
        table.append({
            "No": s.step_no, "操作名": s.name, "タイプ": s.op_label, "機器": s.equipment_tag or "-",
            "開始": _day_hhmm(offset + sc["start"]), "終了": _day_hhmm(offset + sc["end"]),
            "所要_min": r(sc["duration"], 3), "方法": extra["method"], "根拠": extra["basis"],
        })
    total = max(sc["end"] for sc in schedule.values())

    xlsx = write_timetable_excel(flow, start_hour=start_hour)
    out = {
        "timetable": table,
        "総所要時間": fmt_min(total),
        "総所要時間_min": r(total),
        "開始": _day_hhmm(offset), "終了": _day_hhmm(offset + total),
        "excel": ws.add_file("タイムテーブル", f"{title}.xlsx", xlsx),
        "graph": ws.add_figure("タイムテーブル Gantt", _gantt(flow, schedule, start_hour)),
    }
    if warnings:
        out["warnings"] = warnings
    ws.state["timetable_steps"] = steps
    return out
