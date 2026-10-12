"""化学工学計算 AI エージェント

自然言語の質問を Azure OpenAI（function calling）で解釈し、
本リポジトリの計算モジュール（core.thermo_engine / heat_transfer / filtration /
reaction / timetable）をツールとして呼び出して結果を返す。

    from AI_agent import ChemEngAgent
    agent = ChemEngAgent()
    turn = agent.chat("トルエンの 50kPa での沸点は？")
    print(turn.text)

コンセプト: **LLM は解釈と説明のみ、数値は必ず既存エンジンで計算する**。
"""

from .agent import AgentConfigError, AgentTurn, ChemEngAgent
from .workspace import Artifact, Workspace

__all__ = ["AgentConfigError", "AgentTurn", "Artifact", "ChemEngAgent", "Workspace"]
