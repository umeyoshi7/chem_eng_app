"""LLM クライアント

get_client() が接続設定の有無で自動的に切り替える:
    - Azure OpenAI 設定あり → AzureLLMClient（Structured Outputs で AgentReply を取得）
    - 設定なし             → MockLLMClient（ルールベースのデモ実装）

両者とも complete(system, user) -> AgentReply の同一インターフェース。
モックはプロンプト中の ```json フェンス（agent.py が必ず埋め込むコンテキスト）を
読んで動くため、API キーなしでもパイプライン全体を実際に動かして検証できる。
"""

from __future__ import annotations

import json
import re

from .config import AzureOpenAIConfig, load_config
from .schemas import AgentReply, FilterParams, HeatParams, StepProposal


# ---------------------------------------------------------------------------
# Azure OpenAI クライアント
# ---------------------------------------------------------------------------

class AzureLLMClient:
    mode = "azure"

    def __init__(self, cfg: AzureOpenAIConfig):
        from openai import AzureOpenAI  # 遅延 import（モック動作時は openai 不要）
        self._cfg = cfg
        self._client = AzureOpenAI(
            azure_endpoint=cfg.endpoint,
            api_key=cfg.api_key,
            api_version=cfg.api_version,
        )

    def complete(self, system: str, user: str) -> AgentReply:
        rsp = self._client.beta.chat.completions.parse(
            model=self._cfg.deployment,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=AgentReply,
            temperature=0.0,
        )
        msg = rsp.choices[0].message
        if msg.parsed is None:
            raise RuntimeError(f"LLM 応答を解析できませんでした: {msg.refusal or '不明'}")
        return msg.parsed


# ---------------------------------------------------------------------------
# モッククライアント（API キーなしのデモ・検証用）
# ---------------------------------------------------------------------------

# 手動モード操作タイプの目安時間（分）
_MOCK_MANUAL_DURATIONS = {
    "CHARGE": 30.0, "REACTION": 180.0, "TRANSFER": 30.0, "WASH": 30.0,
    "SEPARATION": 60.0, "CRYSTALLIZATION": 120.0, "CONCENTRATE": 120.0,
    "OTHER": 30.0,
}

_CALC_OPS = {"HEAT", "COOL", "FILTER"}
_FILTER_OPS = {"FILTER", "WASH"}

_TEMP_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:℃|°C|度)")
_VOLUME_RE = re.compile(r"\((\d+(?:\.\d+)?)\s*L")  # display 例: "R-101 (50L 反応槽)"
_CHAT_DUR_RE = re.compile(r"操作\s*(\d+)\D*?(\d+(?:\.\d+)?)\s*分")


def _extract_context(user_prompt: str) -> dict:
    """プロンプト中の ```json フェンスからコンテキストを取り出す。"""
    m = re.search(r"```json\s*(.*?)```", user_prompt, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


class MockLLMClient:
    """ルールベースのデモ実装。

    - 操作タイプごとの定型値でパラメータを補完
    - 操作名中の温度表記（例: "80℃まで昇温"）を目標温度として拾う
    - 直前の HEAT/COOL の目標温度を次の初期温度として引き継ぐ
    - チャット指示は「操作N を M 分」の形式のみ解釈（それ以外は案内メッセージ）
    """
    mode = "mock"

    def complete(self, system: str, user: str) -> AgentReply:
        ctx = _extract_context(user)
        steps = ctx.get("steps", [])
        equipment = ctx.get("equipment", [])
        instruction = ctx.get("instruction")

        if instruction:
            return self._handle_instruction(instruction, steps)

        reactors = [e["tag_no"] for e in equipment if e.get("type") == "反応槽"]
        filters = [e["tag_no"] for e in equipment if e.get("type") == "フィルター"]

        # 割り当てる反応槽の容量から仕込み液量を決める（容量の60%、既定100L）
        liquid_volume = 100.0
        if reactors:
            disp = next((e["display"] for e in equipment if e["tag_no"] == reactors[0]), "")
            m = _VOLUME_RE.search(disp)
            if m:
                liquid_volume = round(float(m.group(1)) * 0.6, 1)

        proposals: list[StepProposal] = []
        running_temp = 20.0  # 直前工程の到達温度（初期は室温想定）

        for s in steps:
            op = s.get("op_type", "OTHER")
            name = s.get("name", "")
            p = StepProposal(step_no=int(s["step_no"]))
            reasons: list[str] = []

            # 機器割当: FILTER/WASH はフィルター、その他は反応槽の先頭を提案
            pool = filters if op in _FILTER_OPS else reactors
            if pool:
                p.equipment_tag = pool[0]
                reasons.append(f"{'フィルター' if op in _FILTER_OPS else '反応槽'}の先頭 {pool[0]} を仮割当")

            if op in _CALC_OPS:
                p.time_method = "計算"
                if op in ("HEAT", "COOL"):
                    m = _TEMP_RE.search(name)
                    if m:
                        target = float(m.group(1))
                        reasons.append(f"操作名から目標温度 {target:g}℃ を抽出")
                    else:
                        target = 80.0 if op == "HEAT" else 25.0
                        reasons.append(f"目標温度は定型値 {target:g}℃")
                    offset = 20.0 if target >= running_temp else -20.0
                    p.heat = HeatParams(
                        initial_temp_C=running_temp,
                        target_temp_C=target,
                        liquid_volume_L=liquid_volume,
                        density_g_per_mL=1.0,
                        heat_capacity_J_gK=2.0,
                        dT_offset_K=offset,
                    )
                    reasons.append(
                        f"初期温度は前工程の到達温度 {running_temp:g}℃、"
                        f"液量は槽容量の60% ({liquid_volume:g}L)"
                    )
                    running_temp = target
                else:  # FILTER
                    p.filter = FilterParams(
                        pressure_MPa=0.2,
                        viscosity_mPas=1.0,
                        cake_resistance_m_per_kg=5e11,
                        medium_resistance_m_inv=1e10,
                        dry_cake_mass_g=1000.0,
                        filtrate_volume_L=100.0,
                    )
                    reasons.append("ろ過パラメータは定型値")
            else:
                p.time_method = "手動"
                p.duration_min = _MOCK_MANUAL_DURATIONS.get(op, 30.0)
                reasons.append(f"{op} の目安時間 {p.duration_min:g} 分")

            p.reason = "／".join(reasons)
            proposals.append(p)

        return AgentReply(
            message=(
                f"【モックモード】{len(proposals)} 操作のパラメータを定型ルールで補完しました。"
                "Azure OpenAI の接続情報（環境変数 / Cloud Run は Secret Manager）を設定すると LLM による補完に切り替わります。"
            ),
            proposals=proposals,
        )

    def _handle_instruction(self, instruction: str, steps: list[dict]) -> AgentReply:
        known = {int(s["step_no"]) for s in steps}
        proposals = []
        for m in _CHAT_DUR_RE.finditer(instruction):
            sno, minutes = int(m.group(1)), float(m.group(2))
            if sno in known:
                proposals.append(StepProposal(
                    step_no=sno,
                    time_method="手動",
                    duration_min=minutes,
                    reason=f"指示により {minutes:g} 分に変更",
                ))
        if proposals:
            msg = f"【モックモード】{len(proposals)} 件の所要時間を変更しました。"
        else:
            msg = (
                "【モックモード】指示を解釈できませんでした。"
                "モックでは「操作3 を 45分」の形式のみ対応しています。"
                "Azure OpenAI を設定すると自由な指示が使えます。"
            )
        return AgentReply(message=msg, proposals=proposals)


# ---------------------------------------------------------------------------
# ファクトリ
# ---------------------------------------------------------------------------

def get_client() -> AzureLLMClient | MockLLMClient:
    cfg = load_config()
    if cfg.is_configured:
        return AzureLLMClient(cfg)
    return MockLLMClient()
