"""
製造フロー Excel 読み込みモジュール

Excel フォーマット:
  シート "フロー": 工程一覧（工程番号, 工程名, 操作タイプ, 前工程, 時間決定, 手動時間(分), 備考）
  シート "パラメータ": 各工程の計算用パラメータ（工程番号, パラメータ名, 値, 単位）
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

# 操作タイプの定義
OPERATION_TYPES = {
    "CHARGE":       "仕込み",
    "HEAT":         "加熱",
    "COOL":         "冷却",
    "REACTION":     "反応",
    "CONCENTRATE":  "濃縮",
    "FILTER":       "ろ過",
    "TRANSFER":     "移液",
    "WASH":         "洗浄",
    "OTHER":        "その他",
}

# 時間決定方法
TIME_METHOD_MANUAL = "手動"
TIME_METHOD_CALC = "計算"


@dataclass
class ProcessStep:
    """製造工程の1ステップ"""
    step_no: int
    name: str
    op_type: str                       # OPERATION_TYPES のキー
    prev_steps: list[int]              # 前工程番号リスト（並列対応）
    time_method: str                   # "手動" or "計算"
    manual_duration_min: float | None  # 手動入力時間（分）
    params: dict[str, Any]             # 計算用パラメータ
    note: str = ""

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
    df_flow = xl.parse("フロー", dtype=str).fillna("")

    # 必須列チェック
    required_cols = ["工程番号", "工程名", "操作タイプ", "前工程番号", "時間決定", "手動時間(分)"]
    missing = [c for c in required_cols if c not in df_flow.columns]
    if missing:
        raise ValueError(f"フローシートに必要な列がありません: {missing}")

    # ── パラメータシート読み込み ─────────────────────────────────────────
    params_by_step: dict[int, dict[str, Any]] = {}
    if "パラメータ" in xl.sheet_names:
        df_param = xl.parse("パラメータ", dtype=str).fillna("")
        req_p = ["工程番号", "パラメータ名", "値"]
        missing_p = [c for c in req_p if c not in df_param.columns]
        if missing_p:
            raise ValueError(f"パラメータシートに必要な列がありません: {missing_p}")
        for _, row in df_param.iterrows():
            try:
                sno = int(row["工程番号"])
            except (ValueError, TypeError):
                continue
            key = str(row["パラメータ名"]).strip()
            val_str = str(row["値"]).strip()
            unit = str(row.get("単位", "")).strip()
            # 数値変換を試みる
            try:
                val: Any = float(val_str)
            except ValueError:
                val = val_str
            if sno not in params_by_step:
                params_by_step[sno] = {}
            params_by_step[sno][key] = {"value": val, "unit": unit}

    # ── ProcessStep 組み立て ─────────────────────────────────────────────
    steps: list[ProcessStep] = []
    for _, row in df_flow.iterrows():
        try:
            step_no = int(row["工程番号"])
        except (ValueError, TypeError):
            continue  # 空行スキップ

        name = str(row["工程名"]).strip()
        if not name:
            continue

        op_type = str(row["操作タイプ"]).strip().upper()
        if op_type not in OPERATION_TYPES:
            op_type = "OTHER"

        # 前工程番号（カンマ区切りで複数可）
        prev_raw = str(row["前工程番号"]).strip()
        prev_steps: list[int] = []
        if prev_raw:
            for p in prev_raw.split(","):
                p = p.strip()
                if p:
                    try:
                        prev_steps.append(int(float(p)))
                    except ValueError:
                        pass

        time_method = str(row["時間決定"]).strip()
        if time_method not in (TIME_METHOD_MANUAL, TIME_METHOD_CALC):
            time_method = TIME_METHOD_MANUAL

        # 手動時間
        manual_min_str = str(row["手動時間(分)"]).strip()
        try:
            manual_min: float | None = float(manual_min_str)
        except ValueError:
            manual_min = None

        note = str(row.get("備考", "")).strip()
        params = params_by_step.get(step_no, {})

        step = ProcessStep(
            step_no=step_no,
            name=name,
            op_type=op_type,
            prev_steps=prev_steps,
            time_method=time_method,
            manual_duration_min=manual_min,
            params=params,
            note=note,
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
