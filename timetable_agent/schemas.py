"""LLM 出力の Pydantic スキーマ

初回補完・チャット修正の両方で同じ AgentReply を使う。
各フィールドは Optional で、「提案する値だけを埋める」差分形式。
None のフィールドは applier が無視するため、部分更新がそのまま表現できる。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HeatParams(BaseModel):
    """伝熱計算（HEAT / COOL）用パラメータ。ui/timetable.py の ht_* キーに対応。"""
    initial_temp_C: float | None = None       # ht_t0_
    target_temp_C: float | None = None        # ht_tt_
    liquid_volume_L: float | None = None      # ht_vl_
    density_g_per_mL: float | None = None     # ht_dn_
    heat_capacity_J_gK: float | None = None   # ht_cp_
    dT_offset_K: float | None = None          # ht_dto_


class FilterParams(BaseModel):
    """ろ過計算（FILTER）用パラメータ。ui/timetable.py の fi_* キーに対応。"""
    pressure_MPa: float | None = None             # fi_dP_
    viscosity_mPas: float | None = None           # fi_mu_
    cake_resistance_m_per_kg: float | None = None  # fi_al_
    medium_resistance_m_inv: float | None = None   # fi_rm_
    dry_cake_mass_g: float | None = None           # fi_mc_
    filtrate_volume_L: float | None = None         # fi_vt_


class StepProposal(BaseModel):
    """1 工程分の提案。値を提案しないフィールドは None のままにする。"""
    step_no: int
    equipment_tag: str | None = None
    time_method: Literal["手動", "計算"] | None = None
    duration_min: float | None = None
    heat: HeatParams | None = None
    filter: FilterParams | None = None
    reason: str = Field(default="", description="提案理由（UI に表示する短い説明）")


class AgentReply(BaseModel):
    """LLM からの応答全体。"""
    message: str = Field(description="ユーザーへの説明メッセージ（日本語）")
    proposals: list[StepProposal] = Field(default_factory=list)
