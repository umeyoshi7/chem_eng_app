"""AI エージェント チャットページ（Streamlit）

app.py のナビゲーションから render() を呼び出す。
"""

from __future__ import annotations

import streamlit as st

from .agent import AgentConfigError, ChemEngAgent
from .tools.common import REGISTRY

_EXAMPLES = [
    "トルエンの 20 kPa での沸点と、50°C の蒸気圧は？",
    "エタノール/水の常圧 VLE を計算して。共沸点はある？",
    "R-102 にトルエン 120 L を仕込んで 20°C から 80°C まで昇温する時間は？",
    "水 100 g・エタノール 20 g・酢酸エチル 100 g を混ぜたときの各層の組成は？",
    "R-102 で原料仕込み30分→80°Cまで昇温（トルエン120L）→3時間反応→20°Cまで冷却→"
    "F-102 でろ過（ケーキ5kg, ろ液120L）のタイムテーブルを作って",
]

_TOOL_LABEL = {
    "list_solvents": "溶媒一覧", "vapor_pressure": "蒸気圧計算", "vle_binary": "VLE 計算",
    "lle_layer_split": "LLE 層分離計算", "concentration_simulation": "濃縮シミュレーション",
    "list_equipment": "機器DB参照", "heat_transfer": "伝熱計算", "concentration_time": "濃縮時間推算",
    "filtration_cake_resistance": "ケーキ比抵抗算出", "filtration_time": "ろ過時間推算",
    "get_template": "テンプレート出力", "reaction_analysis": "反応速度解析",
    "reaction_simulation": "反応シミュレーション", "read_flow_excel": "フロー Excel 読込",
    "create_timetable": "タイムテーブル作成",
}


def _get_agent() -> ChemEngAgent | None:
    if "aiag_agent" not in st.session_state:
        try:
            st.session_state["aiag_agent"] = ChemEngAgent()
            st.session_state["aiag_error"] = None
        except AgentConfigError as e:
            st.session_state["aiag_error"] = str(e)
            return None
    return st.session_state.get("aiag_agent")


def _render_artifacts(artifacts: list, key_prefix: str) -> None:
    for i, art in enumerate(artifacts):
        if art.kind == "figure":
            st.plotly_chart(art.figure, width="stretch", key=f"{key_prefix}_fig_{i}")
        else:
            st.download_button(f"📥 {art.title}（{art.name}）", data=art.data, file_name=art.name,
                               mime=art.mime, key=f"{key_prefix}_dl_{i}")


def _render_tool_log(calls: list) -> None:
    if not calls:
        return
    with st.expander(f"実行した計算（{len(calls)} 件）"):
        for c in calls:
            st.markdown(f"**{_TOOL_LABEL.get(c.name, c.name)}** `{c.name}`")
            st.json({"引数": c.arguments, "結果": c.result}, expanded=False)


def render() -> None:
    st.session_state.setdefault("aiag_history", [])
    col_t, col_r = st.columns([9, 1])
    with col_t:
        st.title("AI エージェント")
    with col_r:
        st.write("")
        if st.button("リセット", key="aiag_reset"):
            for k in [k for k in st.session_state if k.startswith("aiag_")]:
                del st.session_state[k]
            st.rerun()
    st.caption("自然言語で質問すると、本アプリの計算モジュールを AI が呼び出して回答します"
               "（数値はすべて既存の計算エンジンで算出）。")

    agent = _get_agent()
    if agent is None:
        st.error("Azure OpenAI が未設定のため AI エージェントを利用できません。")
        st.code(st.session_state.get("aiag_error", ""), language="text")
        return

    with st.expander("📎 ファイル添付（製造フロー Excel・反応実験データ）"):
        ups = st.file_uploader("ファイルを選択", type=["xlsx", "csv"], accept_multiple_files=True,
                               key="aiag_uploader")
        for up in ups or []:
            if up.name not in agent.ws.files:
                agent.ws.add_input_file(up.name, up.getvalue())
        if agent.ws.files:
            st.caption("添付済み: " + ", ".join(agent.ws.files) + "（次の質問で AI に伝わります）")

    with st.expander("💡 質問例・できること"):
        st.markdown("\n".join(f"- {e}" for e in _EXAMPLES))
        st.caption("対応機能: " + "、".join(_TOOL_LABEL[n] for n in REGISTRY if n in _TOOL_LABEL))

    for i, h in enumerate(st.session_state["aiag_history"]):
        with st.chat_message(h["role"]):
            st.markdown(h["text"])
            if h["role"] == "assistant":
                _render_artifacts(h["artifacts"], f"aiag_h{i}")
                _render_tool_log(h["tool_calls"])

    prompt = st.chat_input("質問を入力（例: トルエンの 20 kPa での沸点は？）")
    if not prompt:
        return

    st.session_state["aiag_history"].append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.status("考え中…", expanded=False) as status:
            def _on_tool(name: str, args: dict) -> None:
                status.update(label=f"{_TOOL_LABEL.get(name, name)} を実行中…")
                status.write(f"🔧 {_TOOL_LABEL.get(name, name)}")
            try:
                turn = agent.chat(prompt, on_tool=_on_tool)
            except Exception as e:
                status.update(label="エラー", state="error")
                st.error(f"LLM 呼び出しでエラーが発生しました: {e}")
                return
            status.update(label="完了", state="complete")
        st.markdown(turn.text)
        idx = len(st.session_state["aiag_history"])
        _render_artifacts(turn.artifacts, f"aiag_h{idx}")
        _render_tool_log(turn.tool_calls)

    st.session_state["aiag_history"].append({
        "role": "assistant", "text": turn.text,
        "artifacts": turn.artifacts, "tool_calls": turn.tool_calls,
    })
