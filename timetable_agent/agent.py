"""プロンプト組立と LLM 呼び出し

Streamlit に依存しない純粋なロジック層。
UI 層（ui/timetable_agent_panel.py）が工程・機器・現在値を dict で渡し、
AgentReply を受け取る。
"""

from __future__ import annotations

import json

from .client import get_client
from .schemas import AgentReply

SYSTEM_PROMPT = """\
あなたはバッチ製造プロセスのタイムテーブル作成を支援する化学工学エンジニアです。
ユーザーがアップロードした製造フロー（工程リスト）に対して、タイムテーブル生成に
必要なパラメータを推定し、JSON で提案します。

# 提案するパラメータ
- equipment_tag: 使用機器の Tag No.（必ず提示された機器リストから選ぶ）
  - FILTER / WASH の工程 → フィルター（種別「フィルター」）のみ
  - それ以外の工程 → 反応槽（種別「反応槽」）のみ
  - 同一バッチの連続する工程は同じ反応槽を使うのが自然
- time_method: "計算" または "手動"
  - HEAT / COOL / FILTER → "計算"（機器スペックから所要時間を自動計算）
  - それ以外 → "手動"（duration_min に目安時間［分］を提案）
- heat（HEAT / COOL のみ）: 初期温度[℃]・目標温度[℃]・仕込み液量[L]・
  液密度[g/mL]・比熱容量[J/(g·K)]・ジャケット温度オフセット dT_offset_K[K]
  - 初期温度は直前工程の到達温度を引き継ぐ
  - 仕込み液量は割り当てた反応槽の容量（display 表記の "○○L"）を超えてはいけない
    （不明なら容量の 60% を目安にする）
  - dT_offset_K は昇温なら正（目安 +20）、冷却なら負（目安 -20）
  - 操作名に温度が書かれていれば（例「80℃まで昇温」）それを目標温度にする
- filter（FILTER のみ）: 差圧[MPaG]・ろ液粘度[mPa·s]・ケーク比抵抗[m/kg]・
  ろ材抵抗[m⁻¹]・乾燥ケーキ質量[g]・総ろ液量[L]

# ルール
- 提案しないフィールドは null のままにする（現在値を尊重した差分提案）
- ユーザー指示（instruction）がある場合は、指示に関係する工程・フィールドだけを変更する
- 各提案には reason に短い日本語の根拠を書く
- message にはユーザーへの説明を日本語で書く
- 操作名から反応時間・仕込み時間などが読み取れる場合はそれを優先する
"""


def build_user_prompt(
    steps: list[dict],
    equipment: list[dict],
    instruction: str | None = None,
    history: list[dict] | None = None,
) -> str:
    """コンテキスト JSON を埋め込んだユーザープロンプトを組み立てる。

    Parameters
    ----------
    steps : 工程リスト
        [{step_no, name, op_type, prev_steps, current: {設定済みの現在値}}]
    equipment : 機器リスト [{tag_no, type, display}]
    instruction : チャットでの追加指示（初回補完時は None）
    history : 会話履歴 [{role, content}]（直近数件）
    """
    context = {
        "instruction": instruction,
        "steps": steps,
        "equipment": equipment,
    }
    if instruction:
        task = "以下の指示に従い、関係する工程のパラメータだけを変更してください。"
    else:
        task = "全工程を確認し、未設定のパラメータを補完してください。"

    parts = [task]
    if history:
        hist_lines = [f"- {h['role']}: {h['content']}" for h in history[-6:]]
        parts.append("これまでの会話:\n" + "\n".join(hist_lines))
    parts.append("```json\n" + json.dumps(context, ensure_ascii=False, indent=2) + "\n```")
    return "\n\n".join(parts)


def run_agent(
    steps: list[dict],
    equipment: list[dict],
    instruction: str | None = None,
    history: list[dict] | None = None,
) -> AgentReply:
    """LLM（または モック）にパラメータ補完を依頼し AgentReply を返す。"""
    client = get_client()
    user_prompt = build_user_prompt(steps, equipment, instruction, history)
    return client.complete(SYSTEM_PROMPT, user_prompt)
