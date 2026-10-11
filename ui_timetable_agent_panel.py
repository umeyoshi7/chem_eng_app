"""AI パラメータ補完パネル（タイムテーブル作成ページ ②と③の間に併設）

役割は「AI の提案値を既存編集 UI の session_state に書き込む」ことだけ。
書き込み後は既存の③画面に提案値が入力済みで表示され、ユーザーが自由に手修正できる。
タイムテーブルの計算・生成は既存エンジンのまま（本パネルは一切関与しない）。

※ ui_timetable.py の③のウィジェット描画より前に呼ぶこと
   （Streamlit は描画済みウィジェットのキーへの書き込みを許さないため）。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from timetable_agent.agent import run_agent
from timetable_agent.applier import apply_proposals
from timetable_agent.config import load_config
from timetable_agent.schemas import AgentReply, StepProposal

_HISTORY_KEY = "timetable_agent_history"      # チャット履歴 [{role, content}]
_LAST_REPLY_KEY = "timetable_agent_last"      # 直近の提案（表表示用）

# 提案サマリー表示用の日本語ラベル
_HEAT_LABELS = {
    "initial_temp_C": "初期温度℃", "target_temp_C": "目標温度℃",
    "liquid_volume_L": "液量L", "density_g_per_mL": "密度g/mL",
    "heat_capacity_J_gK": "比熱J/(g·K)", "dT_offset_K": "ΔT_offset K",
}
_FILTER_LABELS = {
    "pressure_MPa": "差圧MPa", "viscosity_mPas": "粘度mPa·s",
    "cake_resistance_m_per_kg": "比抵抗m/kg", "medium_resistance_m_inv": "ろ材抵抗m⁻¹",
    "dry_cake_mass_g": "ケーキg", "filtrate_volume_L": "ろ液L",
}


def _collect_steps_context(rows: list[dict]) -> list[dict]:
    """工程リスト + 現在の設定値スナップショットを LLM 用 dict に変換する。"""
    ss = st.session_state
    steps = []
    for row in rows:
        sno = row["step_no"]
        current: dict = {
            "equipment_display": ss.get(f"eq_{sno}", "（未選択）"),
            "time_method": ss.get(f"edit_method_{sno}", row["time_method"]),
            "duration_min": ss.get(f"dur_{sno}", 0.0),
        }
        for label_map, keymap in (
            (_HEAT_LABELS, {"initial_temp_C": "ht_t0_", "target_temp_C": "ht_tt_",
                            "liquid_volume_L": "ht_vl_", "density_g_per_mL": "ht_dn_",
                            "heat_capacity_J_gK": "ht_cp_", "dT_offset_K": "ht_dto_"}),
            (_FILTER_LABELS, {"pressure_MPa": "fi_dP_", "viscosity_mPas": "fi_mu_",
                              "cake_resistance_m_per_kg": "fi_al_", "medium_resistance_m_inv": "fi_rm_",
                              "dry_cake_mass_g": "fi_mc_", "filtrate_volume_L": "fi_vt_"}),
        ):
            for field, prefix in keymap.items():
                v = ss.get(f"{prefix}{sno}")
                if v is not None:
                    current[field] = v
        steps.append({
            "step_no": sno,
            "name": ss.get(f"edit_name_{sno}", row["name"]),
            "op_type": ss.get(f"edit_op_{sno}", row["op_type"]),
            "prev_steps": row["prev_steps"],
            "current": current,
        })
    return steps


def _proposal_summary(p: StepProposal) -> str:
    """提案内容を1行のサマリー文字列にする。"""
    parts = []
    if p.equipment_tag is not None:
        parts.append(f"機器={p.equipment_tag}")
    if p.time_method is not None:
        parts.append(f"方法={p.time_method}")
    if p.duration_min is not None:
        parts.append(f"時間={p.duration_min:g}分")
    for params, labels in ((p.heat, _HEAT_LABELS), (p.filter, _FILTER_LABELS)):
        if params is None:
            continue
        for field, label in labels.items():
            v = getattr(params, field)
            if v is not None:
                parts.append(f"{label}={v:g}")
    return ", ".join(parts) if parts else "（変更なし）"


def _run_and_apply(
    rows: list[dict],
    equipment: list[dict],
    instruction: str | None,
) -> None:
    """エージェント実行 → 提案適用 → 履歴更新。"""
    history = st.session_state.setdefault(_HISTORY_KEY, [])
    steps = _collect_steps_context(rows)

    try:
        with st.spinner("AI がパラメータを検討中..."):
            reply: AgentReply = run_agent(steps, equipment, instruction, history)
    except Exception as e:
        st.error(f"AI 呼び出しに失敗しました: {e}")
        return

    applied, warnings = apply_proposals(reply.proposals, rows, equipment, st.session_state)

    if instruction:
        history.append({"role": "user", "content": instruction})
    history.append({"role": "assistant", "content": reply.message})
    st.session_state[_LAST_REPLY_KEY] = {
        "reply": reply,
        "applied": applied,
        "warnings": warnings,
    }
    # 適用済みパラメータで再生成を促すため既存の結果をクリア
    st.session_state["timetable_result"] = None


def render_agent_panel(rows: list[dict], equipment_items: list) -> None:
    """AI 補完パネルを描画する。

    Parameters
    ----------
    rows : st.session_state["timetable_edit_rows"]
    equipment_items : EquipmentItem のリスト（heat_transfer.src.equipment_repo）
    """
    equipment = [
        {"tag_no": e.tag_no, "type": e.equip_type, "display": e.display}
        for e in equipment_items
    ]
    is_azure = load_config().is_configured

    with st.expander("🤖 ②' AI パラメータ補完", expanded=True):
        col_info, col_btn = st.columns([2.2, 1])
        with col_info:
            if is_azure:
                st.caption("接続先: **Azure OpenAI** — 補完後は③で自由に手修正できます。")
            else:
                st.caption(
                    "接続先: **モック（デモ）モード** — Azure OpenAI の接続情報（環境変数 / Secret Manager）を"
                    "設定すると LLM 補完に切り替わります。補完後は③で自由に手修正できます。"
                )
        with col_btn:
            if st.button("🤖 AI で自動補完", type="primary", use_container_width=True,
                         key="tt_agent_autofill_btn"):
                _run_and_apply(rows, equipment, instruction=None)

        # ── チャット指示（clear_on_submit で送信後に入力欄をクリア）──
        with st.form("tt_agent_chat_form", clear_on_submit=True, border=False):
            c_in, c_send = st.columns([4, 1])
            instruction = c_in.text_input(
                "AI への指示", key="tt_agent_instruction",
                placeholder="例: 反応は R-102 を使って ／ 操作3 を 45分 に",
                label_visibility="collapsed",
            )
            submitted = c_send.form_submit_button("送信", use_container_width=True)
        if submitted and instruction.strip():
            _run_and_apply(rows, equipment, instruction=instruction.strip())

        # ── 会話履歴 ──
        for h in st.session_state.get(_HISTORY_KEY, []):
            with st.chat_message(h["role"]):
                st.write(h["content"])

        # ── 直近の提案内容と適用結果 ──
        last = st.session_state.get(_LAST_REPLY_KEY)
        if last is not None:
            reply: AgentReply = last["reply"]
            for w in last["warnings"]:
                st.warning(w)
            if reply.proposals:
                st.caption(f"適用: {last['applied']} 項目（③の各欄に反映済み。手修正できます）")
                df = pd.DataFrame([
                    {
                        "操作番号": p.step_no,
                        "提案内容": _proposal_summary(p),
                        "根拠": p.reason,
                    }
                    for p in reply.proposals
                ])
                st.dataframe(df, use_container_width=True, hide_index=True)
