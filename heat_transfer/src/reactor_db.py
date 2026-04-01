from __future__ import annotations
from pathlib import Path

import pandas as pd

from .models import ReactorSpec

# Repository パターンの re-export（バックエンド切り替え時はこちらを使用）
from .equipment_repo import (  # noqa: F401
    get_equipment_repo,
    EquipmentRepository,
    EquipmentItem,
    FilterSpec,
)

_DEFAULT_DB = Path(__file__).parent.parent / "reactor_db.xlsx"


def load_reactor_db(path: Path | None = None) -> pd.DataFrame:
    """reactor_db.xlsx を読み込み DataFrame を返す。"""
    p = Path(path) if path else _DEFAULT_DB
    df = pd.read_excel(p, sheet_name="Reactors")
    return df


def list_tag_nos(path: Path | None = None) -> list[str]:
    """DB 内の反応槽 Tag No. を返す。"""
    df = load_reactor_db(path)
    return df["Tag No."].tolist()


def get_reactor_spec(tag_no: str, path: Path | None = None) -> ReactorSpec:
    """Tag No. から ReactorSpec を返す。見つからなければ ValueError。"""
    df = load_reactor_db(path)
    row = df[df["Tag No."] == tag_no]
    if row.empty:
        raise ValueError(f"Tag No. '{tag_no}' はデータベースに存在しません。")
    r = row.iloc[0]
    return ReactorSpec(
        tag_no=str(r["Tag No."]),
        U=float(r["U (kJ/m2hK)"]) / 3.6,  # kJ/(m²·h·K) → W/(m²·K) for internal calc
        volume_L=float(r["容量(L)"]),
        diameter_m=float(r["直径(m)"]),
        mirror_type=str(r["鏡形状"]),
    )
