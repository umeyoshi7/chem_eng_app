"""エージェントが呼び出せるツール群

各ツールは func(ws: Workspace, **kwargs) -> dict。dict は JSON として LLM に返され、
グラフ・ファイルは ws.add_figure / ws.add_file で成果物として別途収集される。
"""

from __future__ import annotations

import json
import traceback

from .common import REGISTRY, ToolError
from . import properties, process, reaction, timetable  # noqa: F401  (ツール登録)


def tool_schemas() -> list[dict]:
    return [t.schema for t in REGISTRY.values()]


def run_tool(ws, name: str, arguments: str | dict) -> dict:
    """ツールを実行し、例外も含めて JSON 化可能な dict を返す。"""
    if name not in REGISTRY:
        return {"error": f"未知のツール: {name}"}
    try:
        args = json.loads(arguments or "{}") if isinstance(arguments, str) else dict(arguments)
    except json.JSONDecodeError as e:
        return {"error": f"引数の JSON が不正です: {e}"}
    try:
        return REGISTRY[name].func(ws, **args)
    except ToolError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"引数エラー: {e}"}
    except Exception as e:  # 計算エンジン内部の想定外エラー
        return {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc(limit=3)[-1500:]}


__all__ = ["REGISTRY", "ToolError", "run_tool", "tool_schemas"]
