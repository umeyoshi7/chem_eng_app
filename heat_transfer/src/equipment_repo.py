"""
機器リポジトリ (Equipment Repository)

データアクセス層を抽象化し、バックエンド（Excel / BigQuery）を環境変数で切り替え可能にする。

使い方:
    from heat_transfer.src.equipment_repo import get_equipment_repo

    repo = get_equipment_repo()
    items = repo.list_all()          # 全機器リスト
    spec  = repo.get_reactor_spec("R-102")
    fspec = repo.get_filter_spec("F-101")

バックエンド切り替え（将来のBigQuery移行時）:
    export EQUIPMENT_DB_BACKEND=bigquery
    export BIGQUERY_PROJECT_ID=my-gcp-project
    export BIGQUERY_DATASET=plant_master
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from .models import ReactorSpec


# ─────────────────────────────────────────────────────────────────────────────
# データクラス
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FilterSpec:
    """フィルター設備のスペック。"""
    tag_no: str
    area_m2: float
    filter_type: str  # "加圧ろ過" | "遠心ろ過"


@dataclass
class EquipmentItem:
    """機器リスト用の軽量DTO（UI表示・Tag No. 変換に使用）。"""
    tag_no: str
    equip_type: str  # "反応槽" | "フィルター"
    display: str     # UI表示用ラベル（例: "R-102 (200L 反応槽)"）


# ─────────────────────────────────────────────────────────────────────────────
# 抽象基底クラス
# ─────────────────────────────────────────────────────────────────────────────

class EquipmentRepository(ABC):
    """機器DBへのアクセスインターフェース。

    バックエンドを変更する場合はこのクラスを継承して実装し、
    get_equipment_repo() ファクトリ関数で切り替える。
    """

    @abstractmethod
    def list_all(self) -> list[EquipmentItem]:
        """全機器（反応槽＋フィルター）を EquipmentItem リストで返す。"""

    @abstractmethod
    def get_reactor_spec(self, tag_no: str) -> ReactorSpec:
        """反応槽スペックを返す。存在しなければ ValueError。"""

    @abstractmethod
    def get_filter_spec(self, tag_no: str) -> FilterSpec:
        """フィルタースペックを返す。存在しなければ ValueError。"""


# ─────────────────────────────────────────────────────────────────────────────
# Excel バックエンド（現行実装）
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_DB = Path(__file__).parent.parent / "reactor_db.xlsx"


class ExcelEquipmentRepository(EquipmentRepository):
    """reactor_db.xlsx を読み込む実装。

    シート構成:
        Reactors : Tag No., U (kJ/m2hK), 容量(L), 直径(m), 鏡形状
        Filters  : Tag No., 面積(m2),    種別,    備考
    """

    def __init__(self, path: Path | str | None = None):
        self._path = Path(path) if path else _DEFAULT_DB
        self._df_reactors = None
        self._df_filters = None

    def _reactors(self):
        if self._df_reactors is None:
            import pandas as pd
            self._df_reactors = pd.read_excel(self._path, sheet_name="Reactors")
        return self._df_reactors

    def _filters(self):
        if self._df_filters is None:
            import pandas as pd
            try:
                self._df_filters = pd.read_excel(self._path, sheet_name="Filters")
            except Exception:
                import pandas as pd
                self._df_filters = pd.DataFrame()
        return self._df_filters

    def list_all(self) -> list[EquipmentItem]:
        items: list[EquipmentItem] = []
        for _, r in self._reactors().iterrows():
            items.append(EquipmentItem(
                tag_no=str(r["Tag No."]),
                equip_type="反応槽",
                display=f"{r['Tag No.']} ({int(r['容量(L)'])}L 反応槽)",
            ))
        df_f = self._filters()
        if not df_f.empty:
            for _, f in df_f.iterrows():
                items.append(EquipmentItem(
                    tag_no=str(f["Tag No."]),
                    equip_type="フィルター",
                    display=f"{f['Tag No.']} ({f['面積(m2)']}m² {f['種別']})",
                ))
        return items

    def get_reactor_spec(self, tag_no: str) -> ReactorSpec:
        row = self._reactors()[self._reactors()["Tag No."] == tag_no]
        if row.empty:
            raise ValueError(f"反応槽 Tag No. '{tag_no}' はDBに存在しません。")
        r = row.iloc[0]
        return ReactorSpec(
            tag_no=str(r["Tag No."]),
            U=float(r["U (kJ/m2hK)"]) / 3.6,  # kJ/(m²·h·K) → W/(m²·K) for internal calc
            volume_L=float(r["容量(L)"]),
            diameter_m=float(r["直径(m)"]),
            mirror_type=str(r["鏡形状"]),
        )

    def get_filter_spec(self, tag_no: str) -> FilterSpec:
        df_f = self._filters()
        row = df_f[df_f["Tag No."] == tag_no] if not df_f.empty else df_f
        if row.empty:
            raise ValueError(f"フィルター Tag No. '{tag_no}' はDBに存在しません。")
        f = row.iloc[0]
        return FilterSpec(
            tag_no=str(f["Tag No."]),
            area_m2=float(f["面積(m2)"]),
            filter_type=str(f["種別"]),
        )


# ─────────────────────────────────────────────────────────────────────────────
# BigQuery バックエンド（将来対応）
# ─────────────────────────────────────────────────────────────────────────────

class BigQueryEquipmentRepository(EquipmentRepository):
    """Google BigQuery ベースの実装（将来対応）。

    必要な環境変数:
        GOOGLE_APPLICATION_CREDENTIALS : サービスアカウントキーのパス
        BIGQUERY_PROJECT_ID            : GCPプロジェクトID
        BIGQUERY_DATASET               : データセット名（デフォルト: plant_master）

    BigQuery側テーブル構成:
        {dataset}.reactors : tag_no STRING, U_W_m2K FLOAT64, volume_L FLOAT64,
                             diameter_m FLOAT64, mirror_type STRING
        {dataset}.filters  : tag_no STRING, area_m2 FLOAT64, filter_type STRING
    """

    def __init__(self):
        self._project = os.environ["BIGQUERY_PROJECT_ID"]
        self._dataset = os.environ.get("BIGQUERY_DATASET", "plant_master")
        self._bq_client = None

    def _client(self):
        if self._bq_client is None:
            from google.cloud import bigquery  # type: ignore[import]
            self._bq_client = bigquery.Client(project=self._project)
        return self._bq_client

    def _query_one(self, sql: str, tag_no: str):
        from google.cloud import bigquery  # type: ignore[import]
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("tag_no", "STRING", tag_no),
        ])
        rows = list(self._client().query(sql, job_config=job_config))
        return rows[0] if rows else None

    def list_all(self) -> list[EquipmentItem]:
        client = self._client()
        items: list[EquipmentItem] = []

        for row in client.query(
            f"SELECT tag_no, volume_L FROM `{self._project}.{self._dataset}.reactors`"
            " ORDER BY tag_no"
        ):
            items.append(EquipmentItem(
                tag_no=row.tag_no,
                equip_type="反応槽",
                display=f"{row.tag_no} ({int(row.volume_L)}L 反応槽)",
            ))

        for row in client.query(
            f"SELECT tag_no, area_m2, filter_type "
            f"FROM `{self._project}.{self._dataset}.filters`"
            " ORDER BY tag_no"
        ):
            items.append(EquipmentItem(
                tag_no=row.tag_no,
                equip_type="フィルター",
                display=f"{row.tag_no} ({row.area_m2}m² {row.filter_type})",
            ))

        return items

    def get_reactor_spec(self, tag_no: str) -> ReactorSpec:
        row = self._query_one(
            f"SELECT * FROM `{self._project}.{self._dataset}.reactors`"
            " WHERE tag_no = @tag_no LIMIT 1",
            tag_no,
        )
        if row is None:
            raise ValueError(f"反応槽 Tag No. '{tag_no}' はBigQueryに存在しません。")
        return ReactorSpec(
            tag_no=row.tag_no,
            U=row.U_W_m2K,
            volume_L=row.volume_L,
            diameter_m=row.diameter_m,
            mirror_type=row.mirror_type,
        )

    def get_filter_spec(self, tag_no: str) -> FilterSpec:
        row = self._query_one(
            f"SELECT * FROM `{self._project}.{self._dataset}.filters`"
            " WHERE tag_no = @tag_no LIMIT 1",
            tag_no,
        )
        if row is None:
            raise ValueError(f"フィルター Tag No. '{tag_no}' はBigQueryに存在しません。")
        return FilterSpec(
            tag_no=row.tag_no,
            area_m2=row.area_m2,
            filter_type=row.filter_type,
        )


# ─────────────────────────────────────────────────────────────────────────────
# ファクトリ関数
# ─────────────────────────────────────────────────────────────────────────────

def get_equipment_repo(path: Path | str | None = None) -> EquipmentRepository:
    """環境変数 EQUIPMENT_DB_BACKEND に基づいてリポジトリを返す。

    EQUIPMENT_DB_BACKEND=excel    → ExcelEquipmentRepository（デフォルト）
    EQUIPMENT_DB_BACKEND=bigquery → BigQueryEquipmentRepository
    """
    backend = os.environ.get("EQUIPMENT_DB_BACKEND", "excel").lower()
    if backend == "bigquery":
        return BigQueryEquipmentRepository()
    return ExcelEquipmentRepository(path)
