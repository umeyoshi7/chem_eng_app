"""AI_agent のテスト（Azure OpenAI キー不要）

実行: .venv/Scripts/python tests/test_ai_agent.py

- ツール単体: 既存計算エンジンを実際に呼び出して結果の妥当性を確認
- エージェントループ: OpenAI 互換のフェイククライアントで tool_calls → 最終回答 の流れを検証
- UI: Streamlit AppTest で未設定時のエラー表示とチャット 1 往復を検証
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.getLogger("streamlit").setLevel(logging.ERROR)

from AI_agent import AgentConfigError, ChemEngAgent, Workspace  # noqa: E402
from AI_agent.tools import run_tool, tool_schemas  # noqa: E402

from core.paths import FLOW_TEMPLATE, REACTION_SAMPLE_DIR as SAMPLES  # noqa: E402


# ---------------------------------------------------------------------------
# ツール単体
# ---------------------------------------------------------------------------

def test_schemas_valid():
    schemas = tool_schemas()
    assert len(schemas) >= 15
    for s in schemas:
        f = s["function"]
        assert f["parameters"]["type"] == "object"
        assert set(f["parameters"]["required"]) <= set(f["parameters"]["properties"])
        json.dumps(s, ensure_ascii=False)


def test_vapor_pressure_toluene():
    ws = Workspace()
    res = run_tool(ws, "vapor_pressure", {"solvent": "トルエン", "pressures_kPa": [101.325, 20]})
    assert "error" not in res, res
    assert abs(res["大気圧沸点_C"] - 110.6) < 1.5
    bp20 = res["沸点"][1]["T_bp_C"]
    assert 55 < bp20 < 70
    assert ws.artifacts[0].kind == "figure"


def test_unknown_solvent_returns_error():
    res = run_tool(Workspace(), "vapor_pressure", {"solvent": "unobtainium"})
    assert "error" in res and "unobtainium" in res["error"]


def test_vle_ethanol_water_azeotrope():
    res = run_tool(Workspace(), "vle_binary", {"solvent1": "EtOH", "solvent2": "水"})
    assert "error" not in res, res
    az = res["共沸点"]
    assert isinstance(az, list) and 0.8 < az[0]["x1"] < 0.97


def test_lle_split():
    res = run_tool(Workspace(), "lle_layer_split", {
        "water_amount": 100, "solvent1": "エタノール", "amount1": 20,
        "solvent2": "酢酸エチル", "amount2": 100, "unit": "g"})
    assert res["結果"] == "2液相に分離", res
    total = res["水層"]["合計_g"] + res["有機層"]["合計_g"]
    assert abs(total - 220) < 1.0  # 質量保存


def test_heat_transfer_inner_and_bad_tag():
    ws = Workspace()
    res = run_tool(ws, "heat_transfer", {"mode": "inner", "reactor_tag": "R-102",
                                          "liquid_volume_L": 120, "initial_temp_C": 20,
                                          "target_temp_C": 80})
    assert "error" not in res, res
    assert res["所要時間_min"] > 0 and "既定値" in res["物性の前提"]
    bad = run_tool(ws, "heat_transfer", {"mode": "inner", "reactor_tag": "R-999",
                                          "liquid_volume_L": 120, "initial_temp_C": 20,
                                          "target_temp_C": 80})
    assert "error" in bad and "R-999" in bad["error"]
    over = run_tool(ws, "heat_transfer", {"mode": "inner", "reactor_tag": "R-101",
                                           "liquid_volume_L": 120, "initial_temp_C": 20,
                                           "target_temp_C": 80})
    assert "error" in over and "容量" in over["error"]


def test_filtration_time_compressible():
    ws = Workspace()
    res = run_tool(ws, "filtration_time", {"mode": "pressure", "pressure_MPa": 0.2,
                                            "alpha0": 3.6e10, "n_compress": 0.32,
                                            "filter_tag": "F-102", "dry_cake_mass_g": 5000,
                                            "filtrate_volume_L": 150})
    assert "error" not in res, res
    expected_alpha = 3.6e10 * (0.2e6) ** 0.32
    assert abs(res["α_m_kg"] / expected_alpha - 1) < 1e-3
    assert res["所要時間_min"] > 0


def test_reaction_analysis_and_simulation():
    ws = Workspace()
    ws.add_input_file("s1.xlsx", (SAMPLES / "sample1_simple_1st_order.xlsx").read_bytes())
    res = run_tool(ws, "reaction_analysis", {"file_name": "s1.xlsx"})
    assert "error" not in res, res
    fit = res["全データフィット"]
    assert fit["収束"] and fit["R2"] > 0.95
    assert any(a.name.endswith("reaction_report.xlsx") for a in ws.artifacts)
    sim = run_tool(ws, "reaction_simulation", {"use_last_analysis": True, "t_end": 500,
                                                "target_conversion": 0.9})
    assert "error" not in sim, sim
    assert sim["結果"][0]["転化率0.9到達時間"] is not None


def test_reaction_arrhenius_extrapolation():
    ws = Workspace()
    ws.add_input_file("s4.xlsx", (SAMPLES / "sample4_multi_temp_arrhenius.xlsx").read_bytes())
    res = run_tool(ws, "reaction_analysis", {"file_name": "s4.xlsx"})
    assert abs(res["アレニウス(k1)"]["Ea_kJ_mol"] - 50) < 2, res
    no_temp = run_tool(ws, "reaction_simulation", {"use_last_analysis": True, "t_end": 100})
    assert "error" in no_temp
    sim = run_tool(ws, "reaction_simulation", {"use_last_analysis": True, "t_end": 300,
                                                "temperatures_C": [40, 60]})
    k40, k60 = (row["k"] for row in sim["結果"])
    assert k60 > k40


def test_timetable_from_steps():
    ws = Workspace()
    steps = [
        {"step_no": 1, "name": "仕込み", "op_type": "CHARGE", "prev_steps": [],
         "equipment_tag": "R-102", "duration_min": 30},
        {"step_no": 2, "name": "昇温", "op_type": "HEAT", "prev_steps": [1], "equipment_tag": "R-102",
         "heat": {"target_temp_C": 80, "liquid_volume_L": 120, "solvent": "Toluene"}},
        {"step_no": 3, "name": "反応", "op_type": "REACTION", "prev_steps": [2],
         "equipment_tag": "R-102", "duration_min": 180},
        {"step_no": 4, "name": "冷却", "op_type": "COOL", "prev_steps": [3], "equipment_tag": "R-102",
         "heat": {"target_temp_C": 20, "liquid_volume_L": 120, "solvent": "Toluene"}},
        {"step_no": 5, "name": "ろ過", "op_type": "FILTER", "prev_steps": [4], "equipment_tag": "F-102",
         "filter": {"dry_cake_mass_g": 5000, "filtrate_volume_L": 120}},
        {"step_no": 6, "name": "分析待ち", "op_type": "OTHER", "prev_steps": [5]},
    ]
    res = run_tool(ws, "create_timetable", {"steps": steps, "start_hour": 8})
    assert "error" not in res, res
    tt = res["timetable"]
    assert tt[0]["開始"] == "08:00" and tt[1]["開始"] == "08:30"
    assert tt[1]["方法"] == "計算" and tt[4]["方法"] == "計算"
    assert "冷却" in tt[3]["操作名"] and "80→20" in tt[3]["根拠"]  # 昇温の到達温度を引き継ぐ
    assert any("0 分で仮置き" in w for w in res["warnings"])
    xlsx = next(a for a in ws.artifacts if a.name.endswith(".xlsx"))
    assert xlsx.data[:2] == b"PK"


def test_timetable_validation():
    ws = Workspace()
    bad = run_tool(ws, "create_timetable", {"steps": [
        {"step_no": 1, "name": "A", "op_type": "CHARGE", "prev_steps": [2], "duration_min": 10},
        {"step_no": 2, "name": "B", "op_type": "OTHER", "prev_steps": [1], "duration_min": 10}]})
    assert "error" in bad
    par = run_tool(ws, "create_timetable", {"steps": [
        {"step_no": 1, "name": "A", "op_type": "CHARGE", "prev_steps": [], "equipment_tag": "R-101",
         "duration_min": 60},
        {"step_no": 2, "name": "B", "op_type": "CHARGE", "prev_steps": [], "equipment_tag": "R-101",
         "duration_min": 60},
        {"step_no": 3, "name": "C", "op_type": "TRANSFER", "prev_steps": [1, 2], "duration_min": 10}]})
    assert par["timetable"][2]["開始"] == "09:00"
    assert any("重複" in w for w in par["warnings"])


def test_read_flow_excel():
    ws = Workspace()
    ws.add_input_file("flow.xlsx", FLOW_TEMPLATE.read_bytes())
    res = run_tool(ws, "read_flow_excel", {"file_name": "flow.xlsx"})
    assert len(res["steps"]) >= 5 and res["steps"][1]["op_type"] == "HEAT"
    missing = run_tool(ws, "read_flow_excel", {"file_name": "none.xlsx"})
    assert "error" in missing


def test_output_dir_saves_files(tmp=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(output_dir=Path(d))
        run_tool(ws, "get_template", {"kind": "flow"})
        run_tool(ws, "vapor_pressure", {"solvent": "Water"})
        assert all(a.path and a.path.exists() for a in ws.artifacts)


# ---------------------------------------------------------------------------
# エージェントループ（フェイク LLM）
# ---------------------------------------------------------------------------

def _call(cid: str, name: str, args: dict):
    return SimpleNamespace(id=cid, type="function",
                           function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))


def _rsp(content: str | None = None, calls: list | None = None):
    msg = SimpleNamespace(content=content, tool_calls=calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class FakeClient:
    """chat.completions.create を台本どおりに返す OpenAI 互換フェイク。"""

    def __init__(self, script: list):
        self.script = list(script)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        return self.script.pop(0)


def test_agent_loop_tool_then_answer():
    client = FakeClient([
        _rsp(calls=[_call("c1", "vapor_pressure", {"solvent": "Toluene", "pressures_kPa": [20]}),
                    _call("c2", "list_equipment", {})]),
        _rsp("トルエンの 20 kPa での沸点は約 62°C です。"),
    ])
    seen = []
    agent = ChemEngAgent(client=client, model="test")
    turn = agent.chat("トルエンの20kPaの沸点", on_tool=lambda n, a: seen.append(n))
    assert turn.text.startswith("トルエン")
    assert seen == ["vapor_pressure", "list_equipment"]
    assert [c.name for c in turn.tool_calls] == seen
    assert len(turn.artifacts) == 1 and turn.artifacts[0].kind == "figure"
    # 2 回目のリクエストにツール結果が tool_call_id 付きで入っている
    msgs = client.requests[1]["messages"]
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
    assert "T_bp_C" in tool_msgs[0]["content"]
    assert client.requests[0]["tools"] and "R-102" in msgs[0]["content"]  # 機器DBがシステムプロンプトに


def test_agent_tool_error_is_returned_to_llm():
    client = FakeClient([
        _rsp(calls=[_call("c1", "heat_transfer", {"mode": "inner"})]),  # 必須引数不足
        _rsp("液量を教えてください。"),
    ])
    agent = ChemEngAgent(client=client, model="test")
    turn = agent.chat("昇温時間は？")
    assert "error" in turn.tool_calls[0].result
    assert turn.text == "液量を教えてください。"


def test_agent_attachment_note_and_round_limit():
    client = FakeClient([_rsp(calls=[_call(f"c{i}", "list_solvents", {})]) for i in range(3)])
    agent = ChemEngAgent(client=client, model="test", max_rounds=3)
    agent.ws.add_input_file("flow.xlsx", b"dummy")
    turn = agent.chat("これでタイムテーブル")
    assert "[添付ファイル: flow.xlsx]" in client.requests[0]["messages"][-1]["content"]
    assert "上限" in turn.text


def test_agent_config_error_without_env():
    keys = ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"]
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ[k] = ""
        try:
            ChemEngAgent()
        except AgentConfigError as e:
            assert ".env" in str(e)
        else:
            raise AssertionError("AgentConfigError が送出されませんでした")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# UI（Streamlit AppTest）
# ---------------------------------------------------------------------------

def _page():
    from AI_agent.ui import render
    render()


def test_ui_shows_config_error():
    from streamlit.testing.v1 import AppTest
    keys = ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"]
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ[k] = ""
        at = AppTest.from_function(_page, default_timeout=60).run()
        assert not at.exception
        assert any("未設定" in e.value for e in at.error)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_ui_chat_roundtrip():
    from streamlit.testing.v1 import AppTest
    client = FakeClient([
        _rsp(calls=[_call("c1", "vapor_pressure", {"solvent": "Water"})]),
        _rsp("水の大気圧沸点は約 100°C です。"),
    ])
    at = AppTest.from_function(_page, default_timeout=120)
    at.session_state["aiag_agent"] = ChemEngAgent(client=client, model="test")
    at.run()
    at.chat_input[0].set_value("水の沸点は？").run()
    assert not at.exception
    texts = [m.value for m in at.markdown]
    assert any("100°C" in t for t in texts)
    assert len(at.session_state["aiag_history"]) == 2


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"PASS: {fn.__name__}")
    print(f"\n{len(tests)} tests passed.")
