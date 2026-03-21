import streamlit as st


def render_logic_tab(tab):
    with tab:
        st.header("計算ロジック・数式説明")

        with st.expander("1. UNIFAC Dortmund モデル（活量係数）", expanded=True):
            st.markdown(r"""
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

ここで $r_i$ はvan der Waals体積パラメータ, $q_i$ は表面積パラメータ。

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
""")

        with st.expander("2. 液液平衡（LLE）条件"):
            st.markdown(r"""
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
""")

        with st.expander("3. フラッシュアルゴリズム"):
            st.markdown(r"""
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

本アプリでは **thermo ライブラリ**（`FlashVLN`）が上記を自動実行します。
""")

        with st.expander("4. 密度・単位変換"):
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

        with st.expander("5. 蒸気圧曲線"):
            st.markdown(r"""
**蒸気圧の相関式（Antoine 式）：**

thermo ライブラリは各化合物の蒸気圧を温度の関数として保持しています。代表的な形式は Antoine 式です：

$$
\log_{10} P^{sat} = A - \frac{B}{C + T}
$$

（$T$ は °C または K、$P^{sat}$ は mmHg または kPa。係数 $A, B, C$ は化合物ごとに異なる）

**沸点の計算：**

外部圧力 $P$ に対して $P^{sat}(T_{bp}) = P$ となる温度 $T_{bp}$ を二分探索で求めます。

$$
P^{sat}(T_{lo}) < P \leq P^{sat}(T_{hi}) \quad \Rightarrow \quad T_{mid} = \frac{T_{lo} + T_{hi}}{2}
$$

を繰り返し、収束したときの $T_{mid}$ が沸点です。

**サロゲート化合物の補正：**

UNIFAC グループデータがない化合物には、類似化合物（サロゲート）を代替として使用し、
蒸気圧曲線を温度オフセット $\Delta T$ だけシフトすることで実測沸点に合わせます：

$$
P^{sat}_{\text{target}}(T) \approx P^{sat}_{\text{surrogate}}(T - \Delta T)
$$
""")

        with st.expander("6. 気液平衡（VLE線図）"):
            st.markdown(r"""
**修正ラウールの法則：**

$$
y_i P = x_i \gamma_i(T, \mathbf{x})\, P_i^{sat}(T)
$$

ここで $\gamma_i$ は UNIFAC Dortmund モデルによる活量係数、$P_i^{sat}$ は純成分蒸気圧。

**沸点（bubble point）温度の計算：**

液相組成 $\mathbf{x}$ と圧力 $P$ が既知のとき、沸点条件は：

$$
\sum_i x_i \gamma_i(T, \mathbf{x})\, P_i^{sat}(T) = P
$$

この方程式を $T$ について二分探索で解きます。

**露点（dew point）温度の計算：**

気相組成 $\mathbf{y}$ と圧力 $P$ が既知のとき、露点条件は：

$$
\sum_i \frac{y_i P}{\gamma_i(T, \mathbf{x})\, P_i^{sat}(T)} = 1
$$

$\mathbf{x}$ と $T$ を同時に収束させる必要があるため、thermo ライブラリの `FlashVLN` が逐次代入法で解きます。

**不均一共沸（三相系）：**

水と非水溶性溶媒の混合系では、不均一共沸（heterogeneous azeotrope）が現れることがあります。
三相共存温度 $T_3$ はスチーム蒸留方程式から推定します：

$$
\sum_i P_i^{sat}(T_3) = P
$$

この式は活量係数に依存しないため（完全非混和近似）、UNIFAC の誤差を受けません。
$T_3$ は系中の最低沸点（Konovalov の第2法則）を下回る必要があり、それを超える場合は均一共沸の誤検出として除外します。
""")

        with st.expander("7. 濃縮シミュレーション（レイリー蒸留）"):
            st.markdown(r"""
**微分ステップ法：**

各ステップで現在の液相を沸点フラッシュし、微小量 $\Delta V$ の蒸気を取り除くことを繰り返します。

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

**三相域の高速化（Gibbs の相律）：**

三相（気相 + 液相1 + 液相2）共存域では、自由度が $F = C - P + 2 = C - 1$（$C$ は成分数）となり、
$T, P$ 固定のとき気相組成 $\mathbf{y}$ も固定されます。
したがって三相域内では沸点フラッシュをスキップしてキャッシュした $T_3$ と $\mathbf{y}^{(3)}$ を再利用します。
成分が枯渇し始めると $T$ が上昇するため、その時点でキャッシュを無効化して通常計算に戻ります。
""")
