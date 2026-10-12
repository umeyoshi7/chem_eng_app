"""
製造フロー Excel 読み込みモジュール

Excel フォーマット:
  シート "フロー": 工程一覧（操作番号, 操作名, 操作タイプ, 前操作番号）
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

# 操作タイプの定義
OPERATION_TYPES = {
    "CHARGE":          "仕込み",
    "HEAT":            "加熱",
    "COOL":            "冷却",
    "REACTION":        "反応",
    "CONCENTRATE":     "濃縮",
    "FILTER":          "ろ過",
    "TRANSFER":        "移液",
    "WASH":            "洗浄",
    "SEPARATION":      "分離",
    "CRYSTALLIZATION": "晶析",
    "OTHER":           "その他",
}

# デフォルトで「計算」モードとする操作タイプ
_CALC_DEFAULT_OPS = {"HEAT", "COOL", "FILTER"}

# 時間決定方法
TIME_METHOD_MANUAL = "手動"
TIME_METHOD_CALC = "計算"


@dataclass
class ProcessStep:
    """製造工程の1ステップ"""
    step_no: int
    name: str
    op_type: str                       # OPERATION_TYPES のキー
    prev_steps: list[int]              # 前操作番号リスト（並列対応）
    time_method: str                   # "手動" or "計算"
    manual_duration_min: float | None  # 手動入力時間（分）
    params: dict[str, Any]             # 計算用パラメータ
    note: str = ""
    equipment_tag: str | None = None   # 使用機器の Tag No.（反応槽・フィルター等）

    @property
    def op_label(self) -> str:
        return OPERATION_TYPES.get(self.op_type, self.op_type)

    @property
    def duration_min(self) -> float | None:
        """確定済み所要時間（分）。計算モジュールが設定するまで None。"""
        return self._duration_min

    @duration_min.setter
    def duration_min(self, value: float | None):
        self._duration_min = value

    def __post_init__(self):
        self._duration_min = self.manual_duration_min


@dataclass
class ManufacturingFlow:
    """製造フロー全体"""
    steps: list[ProcessStep] = field(default_factory=list)

    def get_step(self, step_no: int) -> ProcessStep | None:
        return next((s for s in self.steps if s.step_no == step_no), None)


# ---------------------------------------------------------------------------
# 読み込み関数
# ---------------------------------------------------------------------------

def read_flow_excel(file_obj: io.BytesIO | str) -> ManufacturingFlow:
    """
    製造フロー Excel ファイルを読み込み ManufacturingFlow を返す。

    Parameters
    ----------
    file_obj : BytesIO or str
        Streamlit の UploadedFile またはファイルパス

    Returns
    -------
    ManufacturingFlow
    """
    xl = pd.ExcelFile(file_obj, engine="openpyxl")

    # ── フローシート読み込み ──────────────────────────────────────────────
    # テンプレートはタイトル行・説明行の後にヘッダーがあるため、
    # 「操作番号」を含む行を自動検出してヘッダー行として使用する
    def _find_header_row(sheet_name: str, key_col: str) -> int:
        raw = xl.parse(sheet_name, header=None, dtype=str).fillna("")
        for i, row in raw.iterrows():
            if key_col in row.values:
                return i
        return 0

    flow_header_row = _find_header_row("フロー", "操作番号")
    df_flow = xl.parse("フロー", header=flow_header_row, dtype=str).fillna("")

    # 必須列チェック
    required_cols = ["操作番号", "操作名", "操作タイプ", "前操作番号"]
    missing = [c for c in required_cols if c not in df_flow.columns]
    if missing:
        raise ValueError(f"フローシートに必要な列がありません: {missing}")

    # ── ProcessStep 組み立て ─────────────────────────────────────────────
    steps: list[ProcessStep] = []
    for _, row in df_flow.iterrows():
        try:
            step_no = int(row["操作番号"])
        except (ValueError, TypeError):
            continue  # 空行スキップ

        name = str(row["操作名"]).strip()
        if not name:
            continue

        op_type = str(row["操作タイプ"]).strip().upper()
        if op_type not in OPERATION_TYPES:
            op_type = "OTHER"

        # 前操作番号（カンマ区切りで複数可）
        prev_raw = str(row["前操作番号"]).strip()
        prev_steps: list[int] = []
        if prev_raw:
            for p in prev_raw.split(","):
                p = p.strip()
                if p:
                    try:
                        prev_steps.append(int(float(p)))
                    except ValueError:
                        pass

        # 時間決定方法は操作タイプから自動設定
        time_method = TIME_METHOD_CALC if op_type in _CALC_DEFAULT_OPS else TIME_METHOD_MANUAL

        step = ProcessStep(
            step_no=step_no,
            name=name,
            op_type=op_type,
            prev_steps=prev_steps,
            time_method=time_method,
            manual_duration_min=None,
            params={},
            note="",
            equipment_tag=None,
        )
        steps.append(step)

    steps.sort(key=lambda s: s.step_no)
    return ManufacturingFlow(steps=steps)


# ---------------------------------------------------------------------------
# 工程順序解決（トポロジカルソート → 開始時刻計算）
# ---------------------------------------------------------------------------

def resolve_schedule(flow: ManufacturingFlow) -> dict[int, dict]:
    """
    各工程の開始・終了時刻（分）を計算する。
    並列工程は前工程の終了時刻の最大値を開始とする。

    Returns
    -------
    dict[step_no -> {"start": float, "end": float, "duration": float}]
    """
    schedule: dict[int, dict] = {}

    for step in flow.steps:
        duration = step.duration_min
        if duration is None:
            duration = 0.0  # 未確定は 0 で仮置き

        if not step.prev_steps:
            start = 0.0
        else:
            prev_ends = []
            for p in step.prev_steps:
                if p in schedule:
                    prev_ends.append(schedule[p]["end"])
                else:
                    prev_ends.append(0.0)
            start = max(prev_ends)

        schedule[step.step_no] = {
            "start": start,
            "end": start + duration,
            "duration": duration,
        }

    return schedule
