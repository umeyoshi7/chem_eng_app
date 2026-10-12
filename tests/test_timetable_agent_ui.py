"""AI 補完パネルの UI 統合テスト（Streamlit AppTest 使用）

実際にタイムテーブル作成ページを描画し、
  1. AI 補完パネルが表示される
  2. 「AI で自動補完」→ 既存③ウィジェットの session_state に提案値が入る
  3. チャット指示で値が上書きされる
  4. 「タイムテーブル生成」→ 既存エンジンでスケジュールが生成される
までを検証する。実行: .venv/Scripts/python tests/test_timetable_agent_ui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # Windows コンソール（cp932）対策

from streamlit.testing.v1 import AppTest

ROWS = [
    {"step_no": 1, "name": "原料仕込み", "prev_steps": [], "op_type": "CHARGE", "time_method": "手動"},
    {"step_no": 2, "name": "80℃まで昇温", "prev_steps": [1], "op_type": "HEAT", "time_method": "計算"},
    {"step_no": 3, "name": "反応", "prev_steps": [2], "op_type": "REACTION", "time_method": "手動"},
    {"step_no": 4, "name": "ろ過", "prev_steps": [3], "op_type": "FILTER", "time_method": "計算"},
]


def _app():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from ui import timetable as ui_timetable
    ui_timetable.render()


def _new_apptest() -> AppTest:
    at = AppTest.from_function(_app)
    at.session_state["timetable_file_key"] = "test-file"
    at.session_state["timetable_edit_rows"] = [dict(r) for r in ROWS]
    at.session_state["timetable_result"] = None
    return at


def main():
    # ── 1. 初期描画: パネルが存在し、例外なし ──
    at = _new_apptest()
    at.run(timeout=60)
    assert not at.exception, f"初期描画で例外: {at.exception}"
    keys = [b.key for b in at.button]
    assert "tt_agent_autofill_btn" in keys, f"AI補完ボタンが見つからない: {keys}"
    print("PASS: パネル描画（例外なし・ボタン存在）")

    # ── 2. AI 自動補完 → 既存ウィジェットに提案値が入る ──
    at.button(key="tt_agent_autofill_btn").click().run(timeout=60)
    assert not at.exception, f"AI補完で例外: {at.exception}"
    ss = at.session_state
    assert ss["dur_1"] == 30.0, f"dur_1={ss['dur_1']}"
    assert ss["edit_method_2"] == "計算"
    assert ss["ht_tt_2"] == 80.0
    assert str(ss["eq_1"]).startswith("R-"), f"eq_1={ss['eq_1']}"
    assert str(ss["eq_4"]).startswith(("F-", "C-")), f"eq_4={ss['eq_4']}"
    print(f"PASS: AI自動補完（eq_1={ss['eq_1']} / eq_4={ss['eq_4']} / ht_tt_2=80.0）")

    # ── 3. チャット指示 → 差分適用 ──
    at.text_input(key="tt_agent_instruction").set_value("操作3 を 45分 にして")
    at.button(key="FormSubmitter:tt_agent_chat_form-送信").click().run(timeout=60)
    assert not at.exception, f"チャット指示で例外: {at.exception}"
    assert at.session_state["dur_3"] == 45.0, f"dur_3={at.session_state['dur_3']}"
    print("PASS: チャット指示（操作3 → 45分）")

    # ── 4. タイムテーブル生成 → 既存エンジンでスケジュール算出 ──
    at.button(key="tt_generate_btn").click().run(timeout=120)
    assert not at.exception, f"タイムテーブル生成で例外: {at.exception}"
    result = at.session_state["timetable_result"]
    assert result is not None, "timetable_result が生成されていない"
    schedule = result["schedule"]
    assert len(schedule) == 4
    total_min = max(v["end"] for v in schedule.values())
    # 手動 30+45 分 + HEAT/FILTER の計算時間 > 75 分
    assert total_min > 75.0, f"総所要時間が不正: {total_min}"
    flow = result["flow"]
    heat_step = flow.get_step(2)
    assert heat_step.duration_min and heat_step.duration_min > 0, "伝熱計算が実行されていない"
    print(f"PASS: タイムテーブル生成（総所要 {total_min:.0f} 分 / 昇温 {heat_step.duration_min:.1f} 分[伝熱計算]）")

    print("\nUI 統合テスト: 全 4 ステップ成功")


if __name__ == "__main__":
    main()
