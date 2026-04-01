import contextlib
import streamlit as st


def render_logic_tab(tab=None):
    with (tab if tab is not None else contextlib.nullcontext()):
        st.header("計算ロジック・数式説明")

        with st.expander("1. LLE線図（液液平衡）", expanded=True):
            st.markdown(r"""
#### 活量係数モデル（UNIFAC Dortmund）

**活量係数の分解：**

$$
\ln \gamma_i = \ln \gamma_i^C + \ln \gamma_i^R
$$

**組み合わせ項（Combinatorial term）：**

$$
\ln \gamma_i^C = 1 - V_i' + \ln V_i' - 5q_i \left(1 - \frac{V_i}{F_i} + \ln \frac{V_i}{F_i}\right)
$$

$$
V_i' = \frac{r_i^{3/4}}{\sum_j x_j r_j^{3/4}}, \quad V_i = \frac{r_i}{\sum_j x_j r_j}, \quad F_i = \frac{q_i}{\sum_j x_j q_j}
$$

ここで $r_i$ はvan der Waals体積パラメータ、$q_i$ は表面積パラメータ。

**残差項（Residual term）：**

$$
\ln \gamma_i^R = \sum_k \nu_k^{(i)} \left(\ln \Gamma_k - \ln \Gamma_k^{(i)}\right)
$$

$$
\ln \Gamma_k = Q_k \left[1 - \ln\left(\sum_m \Theta_m \Psi_{mk}\right) - \sum_m \frac{\Theta_m \Psi_{km}}{\sum_n \Theta_n \Psi_{nm}}\right]
$$

**温度依存相互作用パラメータ（Dortmund修正）：**

$$
\Psi_{mn} = \exp\left(-\frac{a_{mn} + b_{mn}T + c_{mn}T^2}{T}\right)
$$

---

#### 液液平衡条件

**等フガシティー条件：**

$$
f_i^{L1} = f_i^{L2} \quad (i = 1, 2, \ldots, N)
$$

液相では $f_i^L = x_i \gamma_i P_i^{sat}$ なので：

$$
x_i^{L1} \gamma_i^{L1} = x_i^{L2} \gamma_i^{L2}
$$

**物質収支：**

$$
z_i = \beta^{L1} x_i^{L1} + \beta^{L2} x_i^{L2}, \quad \beta^{L1} + \beta^{L2} = 1
$$

**Rachford-Rice方程式（2液相）：**

$$
\sum_i \frac{z_i (K_i - 1)}{1 + \beta (K_i - 1)} = 0, \quad K_i = \frac{x_i^{L2}}{x_i^{L1}}
$$

---

#### フラッシュ計算

**ステップ 1 — 安定性テスト（Tangent Plane Distance, TPD）：**

$$
\text{TPD}(\mathbf{y}) = \sum_i y_i \left[\ln y_i + \ln \gamma_i(\mathbf{y}) - \ln z_i - \ln \gamma_i(\mathbf{z})\right]
$$

$\text{TPD} < 0$ となる試験組成 $\mathbf{y}$ が存在する場合、相分離が起こる。

**ステップ 2 — 逐次代入法（Successive Substitution）：**

1. 初期 $K_i$ を推定（Wilson式など）
2. Rachford-Rice方程式を解いて $\beta$, $x_i^{L1}$, $x_i^{L2}$ を計算
3. 活量係数 $\gamma_i^{L1}$, $\gamma_i^{L2}$ を更新
4. $K_i \leftarrow \gamma_i^{L1} / \gamma_i^{L2}$
5. 収束まで繰り返す（$|\Delta K_i| < \varepsilon$）
""")

        with st.expander("2. VLE線図（気液平衡）"):
            st.markdown(r"""
#### 修正ラウールの法則

$$
y_i P = x_i \gamma_i(T, \mathbf{x})\, P_i^{sat}(T)
$$

ここで $\gamma_i$ は UNIFAC Dortmund モデルによる活量係数、$P_i^{sat}$ は純成分蒸気圧。

---

#### 沸点・露点計算

**沸点（bubble point）温度の計算：**

液相組成 $\mathbf{x}$ と圧力 $P$ が既知のとき、沸点条件は：

$$
\sum_i x_i \gamma_i(T, \mathbf{x})\, P_i^{sat}(T) = P
$$

この方程式を $T$ について解くことで沸点が得られる。

**露点（dew point）温度の計算：**

気相組成 $\mathbf{y}$ と圧力 $P$ が既知のとき、露点条件は：

$$
\sum_i \frac{y_i P}{\gamma_i(T, \mathbf{x})\, P_i^{sat}(T)} = 1
$$

$\mathbf{x}$ と $T$ を同時に収束させる逐次代入法で解く。

---

#### 不均一共沸（三相系）

水と非水溶性溶媒の混合系では、不均一共沸（heterogeneous azeotrope）が現れることがある。
三相共存温度 $T_3$ はスチーム蒸留方程式から推定する：

$$
\sum_i P_i^{sat}(T_3) = P
$$

この式は活量係数に依存しないため（完全非混和近似）、$T_3$ は系中の最低沸点（Konovalov の第2法則）を下回る必要がある。

---

#### Antoine 蒸気圧式

純成分の飽和蒸気圧と温度の関係を表す相関式：

$$
\log_{10} P^{sat} = A - \frac{B}{C + T}
$$

（$T$ は °C または K、$P^{sat}$ は mmHg または kPa。係数 $A, B, C$ は化合物ごとに異なる）

沸点は $P^{sat}(T_{bp}) = P$ を満たす温度 $T_{bp}$ として定義される。
""")

        with st.expander("3. 濃縮シミュレーション（レイリー蒸留）"):
            st.markdown(r"""
**微分ステップ法：**

各ステップで現在の液相を沸点フラッシュし、微小量 $\Delta V$ の蒸気を取り除くことを繰り返す。

$$
\Delta V = \frac{L_0}{N_{\text{steps}}}
$$

**物質収支（各ステップ）：**

$$
n_i^{(k+1)} = n_i^{(k)} - \Delta V \cdot y_i^{(k)}
$$

ここで $y_i^{(k)}$ はステップ $k$ における気相組成（沸点フラッシュで計算）。

**総蒸発率：**

$$
f_{\text{evap}} = \frac{L_0 - L}{L_0}, \quad L = \sum_i n_i
$$

**三相域と Gibbs の相律：**

三相（気相 + 液相1 + 液相2）共存域では、自由度が $F = C - P + 2 = C - 1$（$C$ は成分数）となり、
$T, P$ 固定のとき気相組成 $\mathbf{y}$ も固定される。
""")

        with st.expander("4. 反応速度解析"):
            st.markdown(r"""
#### 反応速度式

**単反応（A → products）：**

$$
\frac{dC_A}{dt} = -k \cdot C_A^n
$$

ここで $k$ は速度定数、$n$ は反応次数。

**逐次反応（A → B → C）：**

$$
\frac{dC_A}{dt} = -k_1 C_A^{n_1}, \quad
\frac{dC_B}{dt} = k_1 C_A^{n_1} - k_2 C_B^{n_2}, \quad
\frac{dC_C}{dt} = k_2 C_B^{n_2}
$$

**並列反応（A → B および A → C）：**

$$
\frac{dC_A}{dt} = -(k_1 + k_2) C_A^n, \quad
\frac{dC_B}{dt} = k_1 C_A^n, \quad
\frac{dC_C}{dt} = k_2 C_A^n
$$

---

#### Arrhenius 式

速度定数の温度依存性：

$$
k(T) = A \exp\!\left(-\frac{E_a}{RT}\right)
$$

ここで $A$ は頻度因子、$E_a$ は活性化エネルギー [J/mol]、$R = 8.314\;\text{J/(mol·K)}$、$T$ は絶対温度 [K]。

**線形化（Arrhenius プロット）：**

$$
\ln k = \ln A - \frac{E_a}{R} \cdot \frac{1}{T}
$$

$1/T$ に対する $\ln k$ の直線の傾きから $E_a = -\text{slope} \times R$、切片から $A = \exp(\text{intercept})$ が得られる。

---

#### 速度定数のフィッティング

実験濃度プロファイルに対して最小二乗法で速度定数 $k$ および反応次数 $n$ を推定する：

$$
\min_{k,\,n} \sum_i \left(C_{\text{obs},i} - C_{\text{calc},i}\right)^2
$$

**適合度指標：**

$$
R^2 = 1 - \frac{\displaystyle\sum_i (C_{\text{obs},i} - C_{\text{calc},i})^2}{\displaystyle\sum_i (C_{\text{obs},i} - \bar{C}_{\text{obs}})^2}
$$

$$
\text{RMSE} = \sqrt{\frac{\displaystyle\sum_i (C_{\text{obs},i} - C_{\text{calc},i})^2}{N}}
$$
""")

        with st.expander("5. 伝熱計算"):
            st.markdown(r"""
#### 基本伝熱式

$$
Q = U \cdot A \cdot \Delta T \quad [\text{W}]
$$

ここで $U$ は総括伝熱係数 [W/(m²·K)]、$A$ は伝熱面積 [m²]、$\Delta T$ は内温とジャケット温の差 [K]。

---

#### 反応槽の伝熱面積（鏡板形状）

| 鏡形状 | 鏡部伝熱面積 | 鏡部容積 |
|---|---|---|
| ED（楕円鏡） | $A = 0.87\,\pi (D/2)^2$ | $V = \pi D^3 / 24$ |
| SD（皿形鏡） | $A = 0.57\,\pi (D/2)^2$ | $V \approx 0.0847\,D^3$ |

胴体部の側面積：$A_{\text{cyl}} = \pi D h_{\text{cyl}}$

総伝熱面積：$A_{\text{total}} = A_{\text{mirror}} + A_{\text{cyl}}$

---

#### 昇降温シミュレーション

**内温制御（一定 $\Delta T$ 追従）：**

ジャケット温を $T_{\text{jacket}} = T_{\text{inner}} + \Delta T_{\text{offset}}$ に保つと伝熱速度が一定になる：

$$
\frac{dT}{dt} = \frac{U \cdot A \cdot \Delta T_{\text{offset}}}{m \cdot C_p} = \text{const.}
$$

目標温度 $T_{\text{target}}$ への到達時間：

$$
t = \frac{|T_{\text{target}} - T_0| \cdot m \cdot C_p}{U \cdot A \cdot |\Delta T_{\text{offset}}|}
$$

**外温制御（ジャケット温固定）：**

ジャケット温 $T_{\text{jacket}}$ を固定すると、内温は指数応答する：

$$
\tau = \frac{m \cdot C_p}{U \cdot A} \quad \text{（時定数）}
$$

$$
T_{\text{inner}}(t) = T_{\text{jacket}} + (T_0 - T_{\text{jacket}}) \exp\!\left(-\frac{t}{\tau}\right)
$$

$3\tau$ で約 95%、$5\tau$ で約 99% の温度変化が完了する。

---

#### 試薬添加シミュレーション

**一括添加（断熱温度上昇 + 冷却）：**

反応による断熱温度変化：

$$
\Delta T_{\text{ad}} = \frac{Q_{\text{rxn}} \times 10^3}{m_{\text{total}} \cdot C_{p,\text{mix}}}
$$

添加直後の内温 $T_0' = T_0 + \Delta T_{\text{ad}}$ を初期値として、ジャケット冷却の微分方程式を解く：

$$
\frac{dT}{dt} = \frac{U \cdot A \cdot (T_{\text{jacket}} - T)}{m_{\text{total}} \cdot C_{p,\text{mix}}}
$$

**連続添加：**

添加速度 $\dot{m} = m_{\text{reagent}} / t_{\text{add}}$ で試薬を加えると、質量と混合比熱が時間変化する：

$$
m(t) = m_{\text{initial}} + \dot{m} \cdot t
$$

$$
C_{p,\text{mix}}(t) = \frac{C_{p,0} \cdot m_{\text{initial}} + C_{p,\text{reagent}} \cdot \dot{m} \cdot t}{m(t)}
$$

エネルギーバランス：

$$
\frac{dT}{dt} = \frac{U A (T_{\text{jacket}} - T) + \dot{Q}_{\text{rxn}} - \dot{m}\, C_{p,\text{reagent}} (T - T_{\text{reagent}})}{m(t) \cdot C_{p,\text{mix}}(t)}
$$

ここで $\dot{Q}_{\text{rxn}} = Q_{\text{rxn,total}} \times 10^3 / t_{\text{add}}$ [W] は添加中の反応発熱速度。
""")

        with st.expander("6. 濃縮時間推算"):
            st.markdown(r"""
#### 計算フロー概要

濃縮シミュレーション（Rayleigh 蒸留）の結果を用い、各蒸発ステップの熱移動から累積時間を積算する。

1. Rayleigh 蒸留の各ステップで液量・沸点・混合蒸発エンタルピーを取得
2. 反応槽形状から伝熱面積を算出（鏡板＋胴体 → 伝熱計算セクション参照）
3. 熱移動量と蒸発速度からステップ時間を計算
4. 全ステップで累積し、蒸発分率 vs 時間の曲線を得る

---

#### 伝熱による熱移動量

$$
Q = U \cdot A_{\text{total}} \cdot (T_{\text{jacket}} - T_{\text{bp}}) \quad [\text{W}]
$$

ここで $T_{\text{bp}}$ は現在の液相沸点 [°C]、$T_{\text{jacket}}$ はジャケット温度 [°C]。

---

#### 蒸発速度と時間積算

**蒸発速度（mol/s）：**

$$
\frac{dn}{dt} = \frac{Q}{\Delta H_{\text{vap,mix}}} \quad [\text{mol/s}]
$$

$\Delta H_{\text{vap,mix}}$ は液相組成から計算した混合蒸発エンタルピー [J/mol]。

**各ステップの所要時間：**

$$
\Delta t = \frac{\Delta n}{dn/dt} = \frac{\Delta n \cdot \Delta H_{\text{vap,mix}}}{Q} \quad [\text{s}]
$$

$\Delta n$ は 1 ステップの蒸発モル数（Rayleigh 蒸留の刻み幅）。

**累積時間：**

$$
t_k = \sum_{j=1}^{k} \Delta t_j
$$

---

#### 注意事項

- $T_{\text{jacket}} \leq T_{\text{bp}}$ の場合、伝熱方向が逆転するため蒸発不可（警告を表示）
- 蒸発エンタルピーが取得できない成分は 40 kJ/mol（代替値）を使用
""")

        with st.expander("7. ろ過時間推算"):
            st.markdown(r"""
#### Ruth のろ過方程式（定圧ろ過）

$$
t(V) = \frac{\mu \alpha c}{2 A^2 \Delta P} V^2 + \frac{\mu R_m}{A \Delta P} V
$$

| 記号 | 意味 | 単位 |
|---|---|---|
| $t$ | ろ過時間 | s |
| $V$ | 累積ろ液量 | m³ |
| $\mu$ | ろ液粘度 | Pa·s |
| $\alpha$ | ケーク比抵抗 | m/kg |
| $c$ | スラリー固体濃度（= 乾燥ケーキ質量 / 総ろ液量） | kg/m³ |
| $A$ | ろ過面積 | m² |
| $\Delta P$ | 差圧 | Pa |
| $R_m$ | ろ材抵抗 | m⁻¹ |

---

#### ケーク比抵抗の測定（定流量法）

定流量 $Q$ でのろ過試験から Ruth 式を変形して $\alpha$ を求める：

$$
\alpha = \frac{A^2 \Delta P}{\mu Q m_{\text{cake}}} - \frac{R_m A}{m_{\text{cake}}}
$$

---

#### 圧縮性指数

ケーク比抵抗の差圧依存性：

$$
\alpha = \alpha_0 \cdot \Delta P^n
$$

両対数変換して線形回帰：

$$
\ln \alpha = \ln \alpha_0 + n \ln \Delta P
$$

$n = 0$：非圧縮性ケーク、$n \to 1$：高圧縮性ケーク

---

#### 遠心ろ過（等価差圧）

遠心機の回転数 $N$ [rpm] から等価差圧を算出してから Ruth 式に適用する：

$$
\Delta P_{\text{eq}} = \frac{\rho \omega^2 (r_{\text{outer}}^2 - r_{\text{inner}}^2)}{2} \quad [\text{Pa}]
$$

$$
\omega = \frac{2\pi N}{60} \quad [\text{rad/s}]
$$
""")

        with st.expander("8. 密度・単位変換（共通）"):
            st.markdown(r"""
**水の密度 — Kell (1975) 多項式（g/mL）：**

$$
\rho_{\text{water}}(T) = \frac{999.842594 + 6.793952\times10^{-2}T - 9.095290\times10^{-3}T^2 + \cdots}{1000}
$$

（$T$ は摂氏、有効範囲：10〜100 °C）

**有機溶媒の密度 — 線形近似（g/mL）：**

$$
\rho_{\text{solvent}}(T) = a + b \cdot T
$$

| パラメータ | 意味 |
|---|---|
| $a$ | 0°C外挿密度 (g/mL) |
| $b$ | 温度係数 (g/mL/°C)、通常負値 |

**単位変換（g / mol / mL → mol）：**

| 入力単位 | 変換式 |
|---|---|
| g | $n_i = m_i / M_i$ |
| mol | $n_i = $ 入力値そのまま |
| mL | $n_i = (V_i \cdot \rho_i) / M_i$ |
""")
