# バッチ製造支援ツール

医薬品・化学品のバッチ製造に必要な生産管理・化学工学計算を一元化した Streamlit アプリケーションです。

## 起動方法

```bash
pip install -r requirements.txt
streamlit run app.py
```

Azure OpenAI の接続情報（AI エージェント・AI パラメータ補完で使用）は環境変数
`AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_DEPLOYMENT`
（任意で `AZURE_OPENAI_API_VERSION`）から読み込みます。
ローカルでは `.env.example` を `.env` にコピーして記入しても構いません（git / Docker イメージには含まれません）。

## Cloud Run へのデプロイ（Secret Manager）

API キーなどは Secret Manager に登録し、Cloud Run がデプロイ時に環境変数としてマウントします
（アプリ側のコード変更や追加ライブラリは不要）。

```bash
PROJECT_ID=<プロジェクトID>
REGION=asia-northeast1
SERVICE=<Cloud Run サービス名>
gcloud config set project $PROJECT_ID
gcloud services enable run.googleapis.com secretmanager.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com

# 1) シークレット登録（printf を使い末尾に改行を入れない）
printf '%s' 'https://<resource>.openai.azure.com/' | gcloud secrets create azure-openai-endpoint   --data-file=-
printf '%s' '<APIキー>'                            | gcloud secrets create azure-openai-api-key    --data-file=-
printf '%s' 'gpt-4o'                               | gcloud secrets create azure-openai-deployment --data-file=-

# 2) Cloud Run の実行サービスアカウントに読み取り権限を付与
#    （既定は Compute Engine 既定 SA。専用 SA を使う場合は deploy に --service-account を付ける）
SA=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')-compute@developer.gserviceaccount.com
for s in azure-openai-endpoint azure-openai-api-key azure-openai-deployment; do
  gcloud secrets add-iam-policy-binding $s \
    --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor
done

# 3) デプロイ（シークレット → 環境変数）
gcloud run deploy $SERVICE --source . --region $REGION \
  --update-secrets=AZURE_OPENAI_ENDPOINT=azure-openai-endpoint:latest,AZURE_OPENAI_API_KEY=azure-openai-api-key:latest,AZURE_OPENAI_DEPLOYMENT=azure-openai-deployment:latest \
  --update-env-vars=AZURE_OPENAI_API_VERSION=2024-10-21 \
  --session-affinity
```

- `--update-secrets` は既存サービスの他の設定を残したままシークレットを追加・更新します
  （`--set-secrets` は既存のシークレット設定をすべて置き換え）
- `:latest` はインスタンス起動時に解決されます。キーを更新したら
  `printf '%s' '<新キー>' | gcloud secrets versions add azure-openai-api-key --data-file=-` の後、
  再デプロイ（新リビジョン作成）で反映されます
- エンドポイント・デプロイ名は機密でなければ `--update-env-vars` で直接渡しても構いません
- `--session-affinity`：Streamlit のセッション（チャット履歴など）を同じインスタンスに保つため推奨
- 末尾の空白・改行はアプリ側でも除去しますが、登録時は `printf '%s'` を推奨します

---

## ページ構成

### 生産管理

#### タイムテーブル作成

製造フローシートから工程タイムテーブルを自動生成します。

- Excel テンプレートをダウンロードしてフローシート・パラメータを記入
- 工程時間の入力方法：**手動入力** または **計算モジュール自動推算**（伝熱・ろ過・濃縮・反応）
- 対応工程タイプ：仕込み・加熱・冷却・反応・濃縮・ろ過・洗浄・移液・その他
- 各工程を色分けしたガントチャート形式の **タイムテーブル Excel** をダウンロード
- 計算モジュール未実装工程は手動入力値にフォールバック（警告表示付き）

**AI パラメータ補完（Azure OpenAI）**

アップロードした製造フローに対し、タイムテーブル生成に必要なパラメータ
（機器 Tag No.・所要時間・伝熱/ろ過計算パラメータ）を LLM が推定し、
既存の編集画面に自動入力します。

- コンセプト：**LLM は提案のみ、計算は既存エンジン**。AI の提案は既存編集ウィジェットに
  下書きとして書き込まれるだけで、ユーザーが全項目を手修正してから生成できる
- チャット欄から追い指示も可能（例：「操作3 を 45分 に」）
- 提案は適用前に検証（機器 Tag No. の実在チェック・操作タイプとの整合・数値下限）され、
  不正値は警告表示のうえ無視される
- **API キー未設定時はモック（デモ）モード**で動作。Azure OpenAI の接続情報
  （ENDPOINT / API_KEY / DEPLOYMENT）を環境変数で設定すると LLM 補完に切り替わる
  （Cloud Run では Secret Manager 経由。「Cloud Run へのデプロイ」参照）
- テスト：`tests/test_timetable_agent.py`（ユニット）、
  `tests/test_timetable_agent_ui.py`（AppTest による UI 統合テスト）

---

### 化学工学計算

#### LLE 線図

Water を固定した 3成分系（Water + 水溶性溶媒 + 非水溶性溶媒）の液液平衡を計算します。

- **三角図（直角三角座標）** にタイライン・バイノーダル点を表示
- **UNIFAC Dortmund モデル**（活量係数）による相平衡計算
- 温度スライダー（10〜100°C）・格子点数スライダー（10〜50）で条件を変更
- 溶媒・温度・格子点数のいずれかが変更されると**自動再計算**
- タイラインデータを **CSV ダウンロード**可能

**仕込み組成 → 層分離計算**

- Water / 水溶性溶媒 / 非水溶性溶媒の量を g / mol / mL 単位で入力
- 計算結果を **mol% / w/w% / v/v%** の表形式で水層・有機層それぞれ表示
- 相比率（β値）、三角図上への仕込みタイライン描画

---

#### 蒸気圧曲線

純成分の蒸気圧 vs 温度曲線を表示します。

- 対象成分：全 63成分（Water + 水溶性 25種 + 非水溶性 37種）から選択
- 温度範囲：-50〜250°C のスライダーで自由設定
- **大気圧（101.325 kPa）での沸点**を自動計算・表示
- 表示範囲が蒸気圧相関式の有効範囲外の場合は**警告を表示**

---

#### VLE 線図

任意の 2成分系の気液平衡を計算し、2種の線図を表示します。

| 線図 | 内容 |
|------|------|
| xy 線図 | 液相モル分率（x₁）vs 気相モル分率（y₁）、対角線付き |
| T-xy 線図 | モル分率 vs 泡点温度（青）・露点温度（橙破線）・三相水平線（緑破線） |

- 圧力を kPa 単位で任意指定
- 不均一共沸系（LL 分離先行系）も泡点二分探索により計算可能
- **異質共沸系（Water–n-Hexane 等）の三相表示**：三相温度 T₃ 水平線・α/β端点・異質共沸気相組成を明示

---

#### 濃縮シミュレーション（レイリー蒸留）

混合物を連続的に蒸発させた場合の組成・沸点変化をシミュレーションします。

- **2〜4 成分**を全 63 成分から自由選択
- 入力単位：mol / g / mL（mL 使用時は仕込み温度で密度補正）
- 圧力を kPa 単位で任意指定
- グラフ表示：各成分量・合計（入力単位）および沸点 vs 蒸発割合
- 三相共存域（T・y 一定）を自動検出してプラトーとして表示

**アルゴリズム：微分蒸留（Rayleigh 方程式）**

各ステップで泡点フラッシュを実行して平衡蒸気組成 y を求め、液相組成を更新します。不均一系では T-P 二分探索にフォールバック。

---

#### 反応速度解析

実験データから反応速度定数・反応次数・アレニウスパラメータを推算します。

**対応反応タイプ**

| タイプ | 反応式 | 必要データ |
|--------|--------|-----------|
| 単純反応 | A → products | 濃度 A |
| 逐次反応 | A → B → C | 濃度 A + 濃度 B（+ 濃度 C） |
| 並列反応 | A → B + A → C | 濃度 A + 濃度 B + 濃度 C |

**解析手法**

| 手法 | 概要 |
|------|------|
| RK4+最小二乗法 | RK45 数値積分 + `scipy.optimize.least_squares` でパラメータ最適化。マルチスタートで局所解を回避 |
| アレニウス解析 | 複数温度データから Ea（活性化エネルギー）と頻度因子 A を線形回帰で算出 |
| 反応シミュレーション | 解析済みパラメータまたは任意温度でのアレニウス外挿値を用いて濃度プロファイルを予測 |

**使い方**：Excel テンプレートをダウンロード → データ記入 → アップロード → 解析実行 → Excel レポートダウンロード

---

#### 伝熱計算

反応釜の加熱・冷却時間をシミュレーションします。

- 反応釜を **データベースから選択**（Tag No.）または **手動入力**
- 反応釜仕様：総括伝熱係数 U、容量、内径、鏡形状（ED / SD）
- 内容液の液量・密度・比熱（溶媒選択または手動入力）を設定
- シミュレーションモード：
  - **内温制御**（内温 → 目標温度）
  - **外温制御**（ジャケット温度一定）
  - **液添加**（低温液の添加による温度変化）
- 温度プロファイルをグラフ表示

---

#### 濃縮時間推算

レイリー蒸留シミュレーションと伝熱計算を組み合わせて、濃縮に要する時間を推算します。

- 溶媒組成の入力元：**手動入力** または **濃縮シミュレーションから引用**
- 反応槽仕様：**データベースから選択**（Tag No.）または **手動入力**
- 操作条件：ジャケット設定温度・目標蒸発分率（スライダー）
- 濃縮時間プロファイル（蒸発分率 vs 時間）をグラフ表示
- 目標蒸発分率への到達時間を補間して表示

**アルゴリズム**：各ステップの沸点・気相組成・混合蒸発熱を Rayleigh 蒸留結果から取得し、伝熱面積（液量 → 幾何計算）と U 値から瞬時蒸発速度 \[mol/s\] を求めて時間を積算。

---

#### ろ過時間推算

Ruth のろ過方程式を用いてケーキ比抵抗・圧縮性指数の算出とろ過時間の推算を行います。

**3つのタブ構成**

| タブ | 内容 |
|------|------|
| ケーキ比抵抗算出 | 一定速度ろ過の実測値（ΔP・Q・Rm）から各圧力点のケーキ比抵抗 α を算出 |
| 圧縮性指数算出 | α vs ΔP の log-log 回帰から圧縮性指数 n と基準比抵抗 α₀ を算出（Tab1 自動取得 or 手動入力） |
| ろ過時間推算 | α 入力元（Tab1 / Tab2 圧縮性考慮 / 手動）を選択し、加圧ろ過または遠心ろ過の時間を推算 |

- 遠心ろ過では RPM・内外半径・液密度から等価差圧を自動計算
- 累積ろ液量 vs 時間の曲線をグラフ表示

---

### AI エージェント

自然言語で質問すると、Azure OpenAI が上記の計算モジュールをツールとして呼び出し、
計算結果・グラフ・タイムテーブル Excel を返します（数値はすべて既存エンジンで算出）。
Streamlit の「AI → AIエージェント」ページ、または `python -m AI_agent`（CLI）で利用できます。
詳細は [AI_agent/README.md](AI_agent/README.md) を参照。

---

### ロジック説明

計算に使用している数式・アルゴリズムの解説を掲載しています。

- UNIFAC Dortmund モデルの数式（Combinatorial / Residual 項）
- LLE 等フガシティー条件・Rachford-Rice 方程式
- フラッシュアルゴリズム（TPD 安定性テスト → 逐次代入法）
- 密度計算・単位変換の詳細

---

## 対応溶媒（63成分）

| 種別 | 成分数 | 例 |
|------|--------|----|
| Water | 1 | Water |
| 水溶性（Miscible） | 25 | Ethanol, Methanol, Acetone, Acetonitrile, IPA, THF, DMF, DMSO, NMP, ... |
| 非水溶性（Immiscible） | 37 | Toluene, n-Hexane, EtOAc, DCM, Chloroform, n-Heptane, Cyclohexane, 2-MeTHF, CPME, ... |

---

## 技術仕様

| 項目 | 内容 |
|------|------|
| 活量係数モデル | UNIFAC Dortmund（thermo ライブラリ）。THF/水を含む系のみ NRTL（共沸データから決定） |
| 状態方程式（気相） | Peng-Robinson（PRMIX） |
| 液相フガシティー | GibbsExcessLiquid（蒸気圧相関式 + 活量係数） |
| フラッシャー | FlashVLN（多相多成分） |
| 反応速度フィッティング | RK45 + scipy.optimize.least_squares（マルチスタート） |
| 水密度 | Kell (1975) 多項式（10〜100°C） |
| 有機溶媒密度 | 線形近似 ρ = a + b·T |

---

## ファイル構成

```
chem_eng_app/
├── app.py                  # エントリーポイント（ページ設定・ナビゲーション定義）
├── core/                   # 計算ロジック（Streamlit 画面から独立）
│   ├── paths.py            # データファイルの場所（一元管理）
│   ├── solvents.py         # 溶媒データベース（63成分）
│   ├── thermo_engine.py    # 熱力学計算（LLE / VLE / 蒸気圧 / レイリー蒸留、THF/水 NRTL 補正）
│   ├── concentration_time.py  # 濃縮時間推算（レイリー蒸留 × 伝熱）
│   ├── heat_transfer/      # 伝熱計算
│   │   ├── geometry.py     # 伝熱面積計算
│   │   ├── heat_calc.py    # 内温/外温制御・添加シミュレーション
│   │   ├── equipment_repo.py  # 反応槽・フィルター DB（リポジトリ）
│   │   ├── reactor_db.py   # 反応槽 DB 読み込み
│   │   ├── models.py       # データクラス定義
│   │   └── plotting.py     # グラフ生成
│   ├── filtration/         # ろ過計算（ケーキ比抵抗・圧縮性指数・ろ過時間）
│   │   ├── calc.py / models.py / plotting.py
│   ├── reaction/           # 反応速度解析
│   │   ├── analysis.py     # 解析パイプライン統括
│   │   ├── arrhenius.py    # アレニウス解析
│   │   ├── data_loader.py  # データ読み込み・バリデーション
│   │   ├── fitting.py      # RK4+最小二乗法フィッティング
│   │   ├── ode_systems.py  # ODE 系定義・ソルバー
│   │   ├── simulation.py   # シミュレーション実行
│   │   ├── reporter.py     # Excel レポート出力
│   │   └── models.py / plotting.py
│   └── timetable/          # タイムテーブル生成
│       ├── flow_reader.py  # フローシート Excel 読み込み・スケジュール計算
│       └── timetable_writer.py  # タイムテーブル Excel 出力
├── ui/                     # Streamlit 画面（各ページの render 関数）
│   ├── timetable.py        # タイムテーブル作成
│   ├── timetable_agent_panel.py  # AI パラメータ補完パネル（タイムテーブルページに併設）
│   ├── lle.py / vle.py / vp.py / conc.py  # LLE / VLE / 蒸気圧 / 濃縮シミュレーション
│   ├── conc_time.py / heat_transfer.py / filtration.py / reaction.py
│   └── logic.py            # ロジック説明
├── timetable_agent/        # AI パラメータ補完エージェント
│   ├── config.py           # Azure OpenAI 接続設定（環境変数 / .env）
│   ├── schemas.py          # LLM 出力の Pydantic スキーマ（差分提案形式）
│   ├── client.py           # LLM クライアント（Azure OpenAI / モック自動切替）
│   ├── agent.py            # プロンプト組立・LLM 呼び出し
│   └── applier.py          # 提案の検証と編集画面への適用
├── AI_agent/               # 自然言語 AI エージェント（全計算モジュールをツール化、Streamlit / CLI）
├── data/                   # データファイル
│   ├── reactor_db.xlsx     # 反応槽・フィルターのマスターデータ
│   ├── templates/          # 入力テンプレート（製造フロー / 反応速度 実験データ）
│   ├── samples/reaction/   # 反応速度解析のサンプルデータ 6種
│   └── solvent_smiles.xlsx # 溶媒名と SMILES の一覧（参考資料）
├── tools/                  # 開発用スクリプト（テンプレート・サンプル生成）
├── docs/                   # 詳細ドキュメント（反応速度解析など）
├── tests/                  # テスト（python tests/<ファイル名>.py で実行）
├── requirements.txt
├── .env.example            # Azure OpenAI 接続設定テンプレート（ローカル用。本番は Secret Manager）
└── Dockerfile
```

依存の向き：`app.py` → `ui/` → `core/`（`AI_agent/`・`timetable_agent/` も `core/` を利用）。
`core/` は Streamlit 画面に依存しないため、計算ロジックの修正・テストは `core/` だけで完結します。
データファイルのパスは `core/paths.py` で一元管理しています。

### テスト

```bash
python tests/test_calc_fixes.py          # 計算エンジンの回帰テスト
python tests/test_ai_agent.py            # AI エージェント
python tests/test_timetable_agent.py     # AI パラメータ補完（ユニット）
python tests/test_timetable_agent_ui.py  # AI パラメータ補完（UI 統合）
```

