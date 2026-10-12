"""リポジトリ内データファイルの場所（パスはここで一元管理する）。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TEMPLATE_DIR = DATA_DIR / "templates"
SAMPLE_DIR = DATA_DIR / "samples"

REACTOR_DB = DATA_DIR / "reactor_db.xlsx"                     # 反応槽・フィルター DB
FLOW_TEMPLATE = TEMPLATE_DIR / "flow_template.xlsx"            # 製造フローテンプレート
EXPERIMENT_TEMPLATE = TEMPLATE_DIR / "experiment_template.xlsx"  # 反応速度 実験データテンプレート
REACTION_SAMPLE_DIR = SAMPLE_DIR / "reaction"                  # 反応速度解析のサンプルデータ
