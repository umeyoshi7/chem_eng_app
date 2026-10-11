"""AI エージェント CLI

    python -m AI_agent                 # 対話モード
    python -m AI_agent "質問文"         # 1 問だけ実行
    python -m AI_agent --attach flow.xlsx "このフローでタイムテーブルを作って"

対話中のコマンド:
    /attach <path>  ファイルを添付    /files  添付一覧
    /reset          会話をリセット    /quit   終了

グラフ（.html）・Excel は --out（既定 AI_agent/outputs/）に保存される。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

DEFAULT_OUT = Path(__file__).parent / "outputs"


def _quiet_streamlit() -> None:
    # engine.py の st.cache_resource が Streamlit 外で出す警告を抑制
    for name in ("streamlit", "streamlit.runtime", "streamlit.runtime.caching"):
        logging.getLogger(name).setLevel(logging.ERROR)


def _attach(agent, path_str: str) -> None:
    p = Path(path_str.strip().strip('"'))
    if not p.is_file():
        print(f"  ファイルが見つかりません: {p}")
        return
    agent.ws.add_input_file(p.name, p.read_bytes())
    print(f"  添付しました: {p.name}")


def _ask(agent, text: str) -> None:
    def on_tool(name, args):
        print(f"  🔧 {name}", flush=True)
    turn = agent.chat(text, on_tool=on_tool)
    print()
    print(turn.text)
    for a in turn.artifacts:
        print(f"  📄 {a.title}: {a.path}")
    print()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m AI_agent", description="化学工学計算 AI エージェント")
    ap.add_argument("question", nargs="*", help="質問（省略で対話モード）")
    ap.add_argument("--attach", action="append", default=[], help="添付ファイル（複数可）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="成果物の保存先")
    args = ap.parse_args(argv)

    _quiet_streamlit()
    from .agent import AgentConfigError, ChemEngAgent
    from .workspace import Workspace

    try:
        agent = ChemEngAgent(workspace=Workspace(output_dir=Path(args.out)))
    except AgentConfigError as e:
        print(e, file=sys.stderr)
        return 2
    for f in args.attach:
        _attach(agent, f)

    if args.question:
        _ask(agent, " ".join(args.question))
        return 0

    print("化学工学計算 AI エージェント（/attach <path>, /files, /reset, /quit）")
    while True:
        try:
            text = input("あなた> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text in ("/quit", "/exit"):
            return 0
        if text == "/reset":
            agent.reset()
            print("  会話をリセットしました。")
        elif text == "/files":
            print("  " + (", ".join(agent.ws.files) or "（添付なし）"))
        elif text.startswith("/attach "):
            _attach(agent, text[len("/attach "):])
        else:
            try:
                _ask(agent, text)
            except Exception as e:
                print(f"  エラー: {e}")
