"""
テンプレート Excel ファイル生成スクリプト
実行: python timetable/create_templates.py
"""

import openpyxl
from openpyxl.styles import (
    Alignment, Border, Font, PatternFill, Side
)
from openpyxl.utils import get_column_letter
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_DIR.mkdir(exist_ok=True)

HEADER_FILL = PatternFill("solid", fgColor="2C3E50")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
NOTE_FONT   = Font(color="666666", italic=True, size=9)
THIN        = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"),  bottom=Side(style="thin"),
)
CENTER = Alignment(horizontal="center", vertical="center")
LEFT   = Alignment(horizontal="left", vertical="center")


def _h(ws, row, col, val, width=None):
    c = ws.cell(row=row, column=col, value=val)
    c.fill = HEADER_FILL
    c.font = HEADER_FONT
    c.alignment = CENTER
    c.border = THIN
    if width:
        ws.column_dimensions[get_column_letter(col)].width = width


def _note(ws, row, col, val, color="FFF9C4"):
    c = ws.cell(row=row, column=col, value=val)
    c.fill = PatternFill("solid", fgColor=color)
    c.font = NOTE_FONT
    c.alignment = LEFT
    c.border = THIN


def _load_tag_nos() -> str:
    """reactor_db.xlsx から全機器の Tag No. をカンマ区切りで返す。"""
    try:
        import sys, os
        app_dir = Path(__file__).parent.parent
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        from heat_transfer.src.equipment_repo import get_equipment_repo
        repo = get_equipment_repo()
        items = repo.list_all()
        return ",".join(item.tag_no for item in items)
    except Exception:
        # DB読み込み失敗時はフォールバック
        return "R-101,R-102,R-103,R-104,R-105,F-101,F-102,F-201,C-101,C-201"


def create_flow_template():
    wb = openpyxl.Workbook()

    # ── シート1: フロー ────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "フロー"
    ws.sheet_view.showGridLines = False

    # タイトル
    ws.merge_cells("A1:H1")
    t = ws["A1"]
    t.value = "製造フロー定義シート"
    t.font = Font(bold=True, size=14)
    t.alignment = CENTER
    ws.row_dimensions[1].height = 28

    # 説明行
    ws.merge_cells("A2:H2")
    d = ws["A2"]
    d.value = (
        "【記入方法】 工程番号は連番(1,2,3...)。前工程番号は複数の場合カンマ区切り(例: 1,2)。"
        "  時間決定: 「手動」or「計算」。機器Tag No.: DBに登録された反応槽/フィルターのTag No.を入力（省略可）。"
        "  計算工程のパラメータはアプリ画面で直接入力してください。"
    )
    d.font = NOTE_FONT
    d.fill = PatternFill("solid", fgColor="EBF5FB")
    d.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 40

    # ヘッダー（担当者を削除 → 8列構成）
    headers = [
        ("工程番号", 8), ("工程名", 24), ("操作タイプ", 14),
        ("前工程番号", 12), ("時間決定", 10), ("手動時間(分)", 12),
        ("機器Tag No.", 12), ("備考", 28),
    ]
    for col, (h, w) in enumerate(headers, 1):
        _h(ws, 3, col, h, w)
    ws.row_dimensions[3].height = 20

    # 操作タイプ選択肢（ドロップダウン）
    from openpyxl.worksheet.datavalidation import DataValidation
    dv_type = DataValidation(
        type="list",
        formula1='"CHARGE,HEAT,COOL,REACTION,CONCENTRATE,FILTER,TRANSFER,WASH,OTHER"',
        showDropDown=False,
        showErrorMessage=True,
        errorTitle="入力エラー",
        error="リストから選択してください",
    )
    dv_type.sqref = "C4:C100"
    ws.add_data_validation(dv_type)

    dv_time = DataValidation(
        type="list",
        formula1='"手動,計算"',
        showDropDown=False,
    )
    dv_time.sqref = "E4:E100"
    ws.add_data_validation(dv_time)

    # 機器Tag No. ドロップダウン: DBから動的取得
    tag_list = _load_tag_nos()
    dv_equip = DataValidation(
        type="list",
        formula1=f'"{tag_list}"',
        showDropDown=False,
    )
    dv_equip.sqref = "G4:G100"
    ws.add_data_validation(dv_equip)

    # サンプルデータ（担当者列を削除）
    # (工程番号, 工程名, 操作タイプ, 前工程, 時間決定, 手動時間, 機器Tag, 備考)
    sample_rows = [
        (1, "原料仕込み",    "CHARGE",      "",  "手動", 30,  "R-102",  "溶剤・原料を仕込む"),
        (2, "加熱昇温",      "HEAT",        "1", "計算", "",  "R-102",  "目標温度まで昇温"),
        (3, "反応",          "REACTION",    "2", "手動", 120, "R-102",  "撹拌反応"),
        (4, "冷却",          "COOL",        "3", "計算", "",  "R-102",  "冷却水で冷却"),
        (5, "晶析",          "OTHER",       "4", "手動", 60,  "R-102",  "自然冷却晶析"),
        (6, "ろ過",          "FILTER",      "5", "計算", "",  "F-102",  "加圧ろ過"),
        (7, "洗浄",          "WASH",        "6", "手動", 30,  "F-102",  "洗液3回"),
        (8, "濃縮",          "CONCENTRATE", "7", "手動", 90,  "R-103",  "溶媒回収"),
        (9, "移液・仕上げ",  "TRANSFER",    "8", "手動", 20,  "",       "製品タンクへ移液"),
    ]
    fill_colors = ["FDFEFE", "FEF9E7"]
    for r, row in enumerate(sample_rows, start=4):
        fill = PatternFill("solid", fgColor=fill_colors[r % 2])
        for c, val in enumerate(row, 1):
            cell = ws.cell(row=r, column=c, value=val)
            cell.fill = fill
            cell.font = Font(size=10)
            cell.alignment = CENTER if c not in (2,) else LEFT
            cell.border = THIN
        ws.row_dimensions[r].height = 18

    ws.freeze_panes = "A4"

    out_path = TEMPLATE_DIR / "flow_template.xlsx"
    wb.save(out_path)
    print(f"テンプレート生成完了: {out_path}")
    return out_path


if __name__ == "__main__":
    create_flow_template()
