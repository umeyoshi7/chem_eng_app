"""エージェントのセッション状態

- 添付ファイル（フロー Excel・反応実験データなど）の保持
- ツールが生成した成果物（グラフ・Excel 等）の収集
- ツール間で引き継ぐ状態（直近の反応速度解析結果など）

output_dir を指定すると成果物を即座にディスクへ保存する（CLI 用）。
Streamlit UI では output_dir=None のままメモリ上の bytes / Figure を使う。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Artifact:
    kind: str                      # "figure" | "file"
    title: str
    name: str                      # ファイル名（figure は保存時の .html 名）
    data: bytes | None = None      # kind="file" の中身
    figure: Any = None             # kind="figure" の plotly Figure
    mime: str = "application/octet-stream"
    path: Path | None = None       # output_dir 保存時のパス


_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')

_MIME = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".html": "text/html",
}


@dataclass
class Workspace:
    output_dir: Path | None = None
    files: dict[str, bytes] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    _pending_files: list[str] = field(default_factory=list)
    _turn_start: int = 0
    _seq: int = 0

    # ── 添付ファイル ───────────────────────────────────────────────────────
    def add_input_file(self, name: str, data: bytes) -> str:
        self.files[name] = data
        if name not in self._pending_files:
            self._pending_files.append(name)
        return name

    def pop_new_input_files(self) -> list[str]:
        names, self._pending_files = self._pending_files, []
        return names

    def get_input_file(self, name: str) -> bytes:
        if name in self.files:
            return self.files[name]
        # 拡張子違い・大文字小文字違いを許容
        low = {k.lower(): k for k in self.files}
        if name.lower() in low:
            return self.files[low[name.lower()]]
        avail = ", ".join(self.files) or "（なし）"
        raise ValueError(f"添付ファイル '{name}' が見つかりません。利用可能: {avail}")

    # ── 成果物 ─────────────────────────────────────────────────────────────
    def _safe_name(self, name: str) -> str:
        self._seq += 1
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{stamp}_{self._seq:02d}_{_UNSAFE.sub('_', name)}"

    def add_file(self, title: str, name: str, data: bytes) -> str:
        fname = self._safe_name(name)
        art = Artifact("file", title, fname, data=data,
                       mime=_MIME.get(Path(name).suffix.lower(), "application/octet-stream"))
        self._save(art)
        self.artifacts.append(art)
        return fname

    def add_figure(self, title: str, fig) -> str:
        fname = self._safe_name(f"{title}.html")
        art = Artifact("figure", title, fname, figure=fig, mime="text/html")
        self._save(art)
        self.artifacts.append(art)
        return fname

    def _save(self, art: Artifact) -> None:
        if self.output_dir is None:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / art.name
        if art.kind == "file":
            path.write_bytes(art.data or b"")
        else:
            art.figure.write_html(str(path), include_plotlyjs="cdn")
        art.path = path

    def begin_turn(self) -> None:
        self._turn_start = len(self.artifacts)

    def turn_artifacts(self) -> list[Artifact]:
        return self.artifacts[self._turn_start:]
