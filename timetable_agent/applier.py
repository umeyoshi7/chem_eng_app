"""提案の検証と session_state への適用

LLM の提案（AgentReply.proposals）をホワイトリスト照合・数値検証したうえで、
ui/timetable.py の編集ウィジェットが読む session_state キーに書き込む。

state は MutableMapping として受け取るため、テストでは plain dict を渡せる。
検証に落ちた値は書き込まず、警告メッセージとして返す（既存 UI の値が保持される）。
"""

from __future__ import annotations

from typing import MutableMapping

from .schemas import StepProposal

_FILTER_OPS = {"FILTER", "WASH"}

# schemas のフィールド名 → session_state キーのプレフィックス
_HEAT_KEYMAP = {
    "initial_temp_C":     "ht_t0_",
    "target_temp_C":      "ht_tt_",
    "liquid_volume_L":    "ht_vl_",
    "density_g_per_mL":   "ht_dn_",
    "heat_capacity_J_gK": "ht_cp_",
    "dT_offset_K":        "ht_dto_",
}
_FILTER_KEYMAP = {
    "pressure_MPa":             "fi_dP_",
    "viscosity_mPas":           "fi_mu_",
    "cake_resistance_m_per_kg": "fi_al_",
    "medium_resistance_m_inv":  "fi_rm_",
    "dry_cake_mass_g":          "fi_mc_",
    "filtrate_volume_L":        "fi_vt_",
}

# 下限値（ui/timetable.py の number_input の min_value に合わせる。
# 下回る値を書き込むとウィジェット描画時に例外になるため、ここで弾く）
_FIELD_MIN = {
    "liquid_volume_L": 0.1, "density_g_per_mL": 0.1, "heat_capacity_J_gK": 0.1,
    "pressure_MPa": 0.001, "viscosity_mPas": 0.01,
    "dry_cake_mass_g": 0.0, "filtrate_volume_L": 0.1,
}
# ウィジェット制約はないが物理的に正の値が必須のフィールド
_STRICT_POSITIVE = {"cake_resistance_m_per_kg", "medium_resistance_m_inv"}


def apply_proposals(
    proposals: list[StepProposal],
    rows: list[dict],
    equipment: list[dict],
    state: MutableMapping,
) -> tuple[int, list[str]]:
    """検証済みの提案値を state に書き込む。

    Parameters
    ----------
    proposals : LLM の提案リスト
    rows      : st.session_state["timetable_edit_rows"]
    equipment : 機器リスト [{tag_no, type, display}]
    state     : st.session_state（テスト時は dict）

    Returns
    -------
    (適用したフィールド数, 警告メッセージリスト)
    """
    known_steps = {r["step_no"]: r for r in rows}
    tag_info = {e["tag_no"]: e for e in equipment}

    applied = 0
    warnings: list[str] = []

    for p in proposals:
        row = known_steps.get(p.step_no)
        if row is None:
            warnings.append(f"操作{p.step_no}: 存在しない操作番号のため提案を無視しました")
            continue

        sno = p.step_no
        op_type = state.get(f"edit_op_{sno}", row.get("op_type", "OTHER"))

        # ── 機器 Tag No.（ホワイトリスト照合 + 操作タイプとの整合チェック）──
        if p.equipment_tag is not None:
            info = tag_info.get(p.equipment_tag)
            expected = "フィルター" if op_type in _FILTER_OPS else "反応槽"
            if info is None:
                warnings.append(f"操作{sno}: 機器 '{p.equipment_tag}' はDBに存在しないため無視しました")
            elif info["type"] != expected:
                warnings.append(
                    f"操作{sno}: {op_type} には{expected}が必要ですが "
                    f"'{p.equipment_tag}' は{info['type']}のため無視しました"
                )
            else:
                state[f"eq_{sno}"] = info["display"]  # selectbox は display 文字列で保持
                applied += 1

        # ── 時間決定方法 ──
        if p.time_method is not None:  # 値の妥当性は Pydantic の Literal が保証
            state[f"edit_method_{sno}"] = p.time_method
            applied += 1

        # ── 手動所要時間 ──
        if p.duration_min is not None:
            if p.duration_min >= 0:
                state[f"dur_{sno}"] = float(p.duration_min)
                applied += 1
            else:
                warnings.append(f"操作{sno}: 所要時間 {p.duration_min} 分は負値のため無視しました")

        # ── 計算パラメータ ──
        applied_n, warns = _apply_params(p, sno, state)
        applied += applied_n
        warnings.extend(warns)

    return applied, warnings


def _apply_params(p: StepProposal, sno: int, state: MutableMapping) -> tuple[int, list[str]]:
    applied = 0
    warnings: list[str] = []
    for params, keymap in ((p.heat, _HEAT_KEYMAP), (p.filter, _FILTER_KEYMAP)):
        if params is None:
            continue
        for field, prefix in keymap.items():
            value = getattr(params, field)
            if value is None:
                continue
            if field in _FIELD_MIN and value < _FIELD_MIN[field]:
                warnings.append(
                    f"操作{sno}: {field}={value} は下限 {_FIELD_MIN[field]} を"
                    f"下回るため無視しました"
                )
                continue
            if field in _STRICT_POSITIVE and value <= 0:
                warnings.append(f"操作{sno}: {field}={value} は正の値が必要なため無視しました")
                continue
            state[f"{prefix}{sno}"] = float(value)
            applied += 1
    return applied, warnings
