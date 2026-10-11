# AI エージェント（AI_agent/）

自然言語の質問を Azure OpenAI（function calling）で解釈し、本リポジトリの計算モジュールを
ツールとして呼び出して、計算結果・グラフ・タイムテーブル Excel を返すエージェントです。

**コンセプト：LLM は解釈と説明のみ。数値はすべて既存の計算エンジンで算出する。**
LLM が数値を推測で作ることはなく、既定値・推定値を使った場合は回答に前提条件として明示します。

## セットアップ

接続情報は環境変数 `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_DEPLOYMENT`
（任意で `AZURE_OPENAI_API_VERSION`）から読み込みます。

- **Cloud Run（本番）**：Secret Manager に登録し、`gcloud run deploy --update-secrets` で
  上記の環境変数名にマウントする。手順はルートの [README「Cloud Run へのデプロイ」](../README.md#cloud-run-へのデプロイsecret-manager)
- **ローカル開発**：`cp .env.example .env` で記入しても可（`.env` は git / Docker イメージに含まれない）

```bash
pip install -r requirements.txt
```

- 接続設定は既存の `timetable_agent` と共通（`timetable_agent/config.py`）
- デプロイには function calling 対応モデル（gpt-4o / gpt-4.1 など）を指定
- 未設定時はエラーメッセージで設定方法を案内します（モックモードはありません）

## 使い方

### Streamlit（チャット画面）

```bash
streamlit run app.py      # サイドバー「AI → AIエージェント」
```

- 製造フロー Excel・反応実験データは「📎 ファイル添付」から添付
- グラフは画面に表示、Excel はダウンロードボタンで取得
- 「実行した計算」を開くと、呼び出したツールの引数と結果（JSON）を確認できる

### CLI

```bash
python -m AI_agent                                  # 対話モード
python -m AI_agent "トルエンの 20 kPa での沸点は？"     # 1 問だけ
python -m AI_agent --attach flow.xlsx "このフローでタイムテーブルを作って"
```

対話中コマンド：`/attach <path>`・`/files`・`/reset`・`/quit`。
グラフ（.html）と Excel は `AI_agent/outputs/`（`--out` で変更可、git 管理外）に保存されます。

### Python から

```python
from AI_agent import ChemEngAgent
agent = ChemEngAgent()
turn = agent.chat("R-102 にトルエン 120 L、20→80°C の昇温時間は？")
print(turn.text)          # 回答
turn.artifacts            # グラフ（plotly Figure）・ファイル（bytes）
turn.tool_calls           # 実行したツールと引数・結果
```

## 質問例

| 分野 | 例 |
|------|----|
| 蒸気圧 | トルエンの 20 kPa での沸点と 50°C の蒸気圧は？ |
| VLE | エタノール/水の常圧 VLE。共沸点は？ |
| LLE | 水 100 g・エタノール 20 g・酢酸エチル 100 g を混ぜたときの各層の組成は？ |
| 濃縮 | THF 50 L とトルエン 50 L を 60% 留去したときの残液組成と沸点は？ |
| 伝熱 | R-103 で水 300 L をジャケット 10°C 一定で 80°C から 30°C に冷やす時間は？ |
| 濃縮時間 | R-102 でトルエン 100 L・水 2 L を 20 kPa、ジャケット 70°C で 80% 濃縮する時間は？ |
| ろ過 | F-102 で ΔP 0.2 MPa、α 5e11、ケーキ 5 kg、ろ液 150 L のろ過時間は？ |
| 反応速度 | （実験データ添付）このデータを解析して。60°C で 95% 転化する時間は？ |
| タイムテーブル | R-102 で仕込み 30 分 → トルエン 120 L を 80°C まで昇温 → 3 時間反応 → 20°C まで冷却 → F-102 でろ過（ケーキ 5 kg, ろ液 120 L）のタイムテーブルを作って |
| 〃（修正） | 反応を 4 時間にして、開始を 9 時にして |

## ツール一覧

| ツール | 内容 | 使用する既存モジュール |
|--------|------|------------------------|
| `list_solvents` | 対応溶媒 63 成分の一覧 | `solvents.py` |
| `vapor_pressure` | 蒸気圧・任意圧力での沸点 | `engine.calc_vapor_pressure_curve` |
| `vle_binary` | 2 成分 VLE・共沸点・三相 | `engine.calc_vle_xy` |
| `lle_layer_split` | 水＋2 溶媒の層分離組成 | `engine.calc_layer_composition` |
| `concentration_simulation` | レイリー蒸留（残液組成・沸点推移） | `engine.calc_rayleigh_distillation` |
| `list_equipment` | 反応槽・フィルター DB | `heat_transfer/src/equipment_repo.py` |
| `heat_transfer` | 内温制御 / 外温制御 / 添加 | `heat_transfer/src/heat_calc.py` |
| `concentration_time` | 濃縮所要時間 | `engine` ＋ `ui_conc_time._calc_concentration_time` |
| `filtration_cake_resistance` | ケーキ比抵抗・圧縮性指数 | `filtration/src/calc.py` |
| `filtration_time` | 加圧 / 遠心ろ過時間 | `filtration/src/calc.py` |
| `reaction_analysis` | k・次数・アレニウス解析、Excel レポート | `react_analysis/src/*` |
| `reaction_simulation` | 濃度プロファイル・転化率到達時間 | `react_analysis/src/simulation.py` |
| `get_template` | フロー / 実験データの Excel テンプレート | 既存テンプレート |
| `read_flow_excel` | フロー Excel の読込 | `timetable/flow_reader.py` |
| `create_timetable` | 時間推算＋タイムテーブル Excel・Gantt | `timetable/*` ＋ 伝熱・ろ過・濃縮時間 |

タイムテーブルの所要時間は HEAT/COOL＝伝熱計算、FILTER＝Ruth 式、CONCENTRATE＝濃縮時間推算、
その他＝指定時間（分）で決まります。HEAT/COOL の初期温度を省略すると直前の昇降温工程の到達温度を引き継ぎ、
同一機器の時間重複は警告されます。

## 構成

```
AI_agent/
├── __init__.py      # ChemEngAgent などを公開
├── __main__.py      # python -m AI_agent
├── agent.py         # function calling ループ（Azure OpenAI）
├── prompts.py       # システムプロンプト（溶媒リスト・機器DBを埋め込み）
├── workspace.py     # 添付ファイル・成果物（グラフ/Excel）・ツール間状態
├── ui.py            # Streamlit チャットページ（app.py から呼び出し）
├── cli.py           # CLI
├── tools/
│   ├── common.py    # ツール登録・溶媒名解決（日本語/略称）・単位変換・機器DB
│   ├── properties.py
│   ├── process.py
│   ├── reaction.py
│   └── timetable.py
├── tests/test_ai_agent.py
└── outputs/         # CLI の成果物（git 管理外）
```

## テスト

```bash
.venv/Scripts/python AI_agent/tests/test_ai_agent.py
```

API キー不要。ツールは実際の計算エンジンで検証し、エージェントループと UI は
OpenAI 互換のフェイククライアントで検証します。

## 既知の制約

- 気液・液液平衡は UNIFAC Dortmund モデルの予測に従います。ただし THF と水を両方含む系は、
  UNIFAC が誤って液液分離を予測するため、共沸データから決めた NRTL で計算します
  （`engine.THF_WATER_AZEOTROPE`。共沸点付近の精度は ±1 K 程度、THF 低濃度側は推定精度が下がる）。
- 濃縮計算が途中で止まった場合は、到達できた蒸発率を回答に示します。
- 反応速度の時間・濃度単位は入力データの単位に従います。
