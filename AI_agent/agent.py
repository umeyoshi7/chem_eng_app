"""Azure OpenAI function calling によるエージェントループ"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .prompts import build_system_prompt
from .tools import run_tool, tool_schemas
from .workspace import Artifact, Workspace

# ツール結果を LLM に返す際の最大文字数（グラフ点列などで膨らむのを防ぐ）
_MAX_RESULT_CHARS = 12000

CONFIG_HELP = (
    "Azure OpenAI の接続情報が設定されていません。以下の環境変数を設定してください:\n"
    "  AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/\n"
    "  AZURE_OPENAI_API_KEY=<APIキー>\n"
    "  AZURE_OPENAI_DEPLOYMENT=<デプロイ名（function calling 対応モデル, 例: gpt-4o）>\n"
    "  AZURE_OPENAI_API_VERSION=2024-10-21（省略可）\n"
    "\n"
    "■ Cloud Run: Secret Manager に登録し、gcloud run deploy --set-secrets で\n"
    "  上記の環境変数名にマウントしてください（README「Cloud Run へのデプロイ」参照）。\n"
    "■ ローカル: プロジェクトルートの .env.example を .env にコピーして記入しても可。"
)


class AgentConfigError(RuntimeError):
    pass


@dataclass
class ToolCallLog:
    name: str
    arguments: dict
    result: dict


@dataclass
class AgentTurn:
    text: str
    artifacts: list[Artifact] = field(default_factory=list)
    tool_calls: list[ToolCallLog] = field(default_factory=list)


def _make_azure_client():
    from timetable_agent.config import load_config  # 環境変数 / .env ローダーを共用
    cfg = load_config()
    if not cfg.is_configured:
        raise AgentConfigError(CONFIG_HELP)
    from openai import AzureOpenAI
    client = AzureOpenAI(azure_endpoint=cfg.endpoint, api_key=cfg.api_key,
                         api_version=cfg.api_version)
    return client, cfg.deployment


def _equipment_summary(ws: Workspace) -> dict | None:
    res = run_tool(ws, "list_equipment", {})
    return None if "error" in res else res


class ChemEngAgent:
    """自然言語 → ツール呼び出し → 回答 のエージェント。

    client を渡さなければ環境変数（Cloud Run では Secret Manager 経由）/ .env から Azure OpenAI クライアントを生成する
    （未設定なら AgentConfigError）。テストでは OpenAI 互換のフェイククライアントを渡せる。
    """

    def __init__(self, workspace: Workspace | None = None, client: Any = None,
                 model: str | None = None, max_rounds: int = 12):
        if client is None:
            client, model = _make_azure_client()
        self.client = client
        self.model = model or "gpt-4o"
        self.max_rounds = max_rounds
        self.ws = workspace or Workspace()
        self._tools = tool_schemas()
        self.messages: list[dict] = []
        self.reset()

    def reset(self) -> None:
        self.messages = [{"role": "system",
                          "content": build_system_prompt(_equipment_summary(self.ws))}]
        self.ws.state.clear()

    def chat(self, user_text: str,
             on_tool: Callable[[str, dict], None] | None = None) -> AgentTurn:
        """1 ターン分の対話。on_tool(name, args) はツール実行直前に呼ばれる（進捗表示用）。"""
        self.ws.begin_turn()
        new_files = self.ws.pop_new_input_files()
        content = user_text
        if new_files:
            content += "\n\n[添付ファイル: " + ", ".join(new_files) + "]"
        self.messages.append({"role": "user", "content": content})

        logs: list[ToolCallLog] = []
        for _ in range(self.max_rounds):
            rsp = self.client.chat.completions.create(
                model=self.model, messages=self.messages, tools=self._tools, tool_choice="auto",
            )
            msg = rsp.choices[0].message
            calls = list(msg.tool_calls or [])
            entry: dict = {"role": "assistant", "content": msg.content or ""}
            if calls:
                entry["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in calls
                ]
            self.messages.append(entry)
            if not calls:
                return AgentTurn(msg.content or "", self.ws.turn_artifacts(), logs)

            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if on_tool:
                    on_tool(c.function.name, args)
                result = run_tool(self.ws, c.function.name, c.function.arguments)
                logs.append(ToolCallLog(c.function.name, args, result))
                text = json.dumps(result, ensure_ascii=False, default=str)
                if len(text) > _MAX_RESULT_CHARS:
                    text = text[:_MAX_RESULT_CHARS] + "...(truncated)"
                self.messages.append({"role": "tool", "tool_call_id": c.id, "content": text})

        text = "ツール呼び出しの上限回数に達したため処理を中断しました。質問を分割してお試しください。"
        self.messages.append({"role": "assistant", "content": text})
        return AgentTurn(text, self.ws.turn_artifacts(), logs)
