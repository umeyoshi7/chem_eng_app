from thermo import ChemicalConstantsPackage, GibbsExcessLiquid, FlashVLN, CEOSGas, PRMIX
from thermo.unifac import UNIFAC, DOUFSG, DOUFIP2016
from thermo.nrtl import NRTL
import streamlit as st
import concurrent.futures as _cf
import atexit
from functools import lru_cache

import numpy as np
from scipy.optimize import least_squares, minimize

# VF=0 タイムアウト用スレッドプール（LL系で VF=0 が 4s かかる問題を回避）
_vf0_executor = _cf.ThreadPoolExecutor(max_workers=4)
atexit.register(_vf0_executor.shutdown, wait=False)


class _VaporPressureShifted:
    """サロゲート化合物の蒸気圧曲線を T_offset_K だけ高温側にシフトし、
    実測沸点に合わせる。GibbsExcessLiquid の VaporPressures リストに差し替えて使用。

    例: THP(Tb=88°C)サロゲートを 4-MeTHP(Tb=105°C)に補正する場合
        → T_offset_K = 16.7
        → P_4MeTHP(T) ≈ P_THP(T − 16.7 K)
    """
    def __init__(self, vp_base, T_offset_K: float):
        self._vp = vp_base
        self._dT = T_offset_K
        if getattr(vp_base, 'Tmin', None) is not None:
            self.Tmin = vp_base.Tmin + T_offset_K
        if getattr(vp_base, 'Tmax', None) is not None:
            self.Tmax = vp_base.Tmax + T_offset_K

    def __call__(self, T):
        return self._vp(T - self._dT)

    def T_dependent_property(self, T):
        return self._vp.T_dependent_property(T - self._dT)

    def __getattr__(self, name):
        return getattr(self._vp, name)


def _flash_vf0_timeout(flasher, P: float, zs: list, timeout: float = 0.35):
    """VF=0 フラッシュをタイムアウト付きで実行する。
    均一系では VF=0 は ~50ms で完了するが、LL 系では ~4s かかって失敗する。
    timeout 秒以内に完了しなければ None を返し、_vf0_known_fail を立てさせる。
    """
    future = _vf0_executor.submit(flasher.flash, **{"P": P, "VF": 0, "zs": zs})
    try:
        r = future.result(timeout=timeout)
        return r if (r.gas is not None) else None
    except _cf.TimeoutError:
        return None   # タイムアウト → LL 系の可能性が高い
    except Exception:
        return None


def density_water(T_C):
    """Kell (1975) polynomial, g/mL, valid 10-100°C"""
    t = T_C
    return (999.842594 + 6.793952e-2*t - 9.095290e-3*t**2
            + 1.001685e-4*t**3 - 1.120083e-6*t**4 + 6.536332e-9*t**5) / 1000.0


def density_solvent(solvent: dict, T_C: float) -> float:
    return solvent["density_a"] + solvent["density_b"] * T_C


# ── 活量係数モデル: UNIFAC Dortmund ＋ THF/水 の NRTL 補正 ────────────────────
#
# UNIFAC Dortmund（公開パラメータ DOUFIP2016）は THF/水 を部分混和（約 59 °C 以下で
# 液液分離）と予測するが、実際の THF/水 は常圧で完全混和し、共沸点は約 64 °C・
# THF 約 95 wt% にある。そこで THF と水を両方含む系に限り、NRTL を使用する:
#   - THF/水 ペア : 共沸データ 1 点から NRTL パラメータを決定（「完全混和」を制約条件に追加）
#   - その他ペア  : UNIFAC Dortmund の活量係数に NRTL を回帰（Aspen の UNIFAC 推算と同じ考え方）
# THF/水 を含まない系は従来どおり UNIFAC Dortmund のみを使う（結果は変わらない）。

# THF/水 共沸データ（101.325 kPa）。文献代表値（文献により 63.2〜64.0 °C、94〜96 wt% THF）。
# 値を変更すると次回計算時にパラメータが再決定される。
THF_WATER_AZEOTROPE = {"T_C": 63.8, "w_THF": 0.946, "P_kPa": 101.325}
_NRTL_ALPHA = 0.3              # 非ランダム性パラメータ α（水-有機系の標準値）
_NRTL_STABILITY_MARGIN = 0.02  # 完全混和の制約: d ln(a_THF)/dx_THF ≥ この値（全組成）
_THF_ID, _WATER_ID = "tetrahydrofuran", "water"
_MW_THF, _MW_WATER = 72.106, 18.015


def _nrtl_binary_lngammas(t12, t21, alpha, x1):
    """2 成分 NRTL の ln γ1, ln γ2（x1 は配列可）。"""
    x2 = 1.0 - x1
    G12, G21 = np.exp(-alpha * t12), np.exp(-alpha * t21)
    ln1 = x2**2 * (t21 * (G21 / (x1 + x2 * G21))**2 + t12 * G12 / (x2 + x1 * G12)**2)
    ln2 = x1**2 * (t12 * (G12 / (x2 + x1 * G12))**2 + t21 * G21 / (x1 + x2 * G21)**2)
    return ln1, ln2


@lru_cache(maxsize=None)
def _thf_water_nrtl_taus(T_az_C: float, w_THF: float, P_kPa: float,
                         alpha: float = _NRTL_ALPHA,
                         margin: float = _NRTL_STABILITY_MARGIN) -> tuple:
    """共沸データ 1 点から THF(1)/水(2) の NRTL τ12, τ21（温度依存なし）を決定する。

    共沸点では x = y なので γi = P / Pi_sat(T_az)。未知数 2・式 2 で解けるが、THF/水 は
    液液分離の一歩手前の系で、この 2 式だけでは解が液液分離側に入る。そこで
    「全組成で安定（完全混和）」を制約条件にした最小二乗として解く。
    """
    _, corr = ChemicalConstantsPackage.from_IDs([_THF_ID, _WATER_ID])
    vp_thf, vp_w = corr.VaporPressures
    T = T_az_C + 273.15
    P = P_kPa * 1000.0
    x1 = (w_THF / _MW_THF) / (w_THF / _MW_THF + (1.0 - w_THF) / _MW_WATER)
    target = np.log([P / vp_thf(T), P / vp_w(T)])
    xs = np.linspace(0.002, 0.998, 499)

    def obj(p):
        return float(np.sum((np.array(_nrtl_binary_lngammas(p[0], p[1], alpha, x1)) - target)**2))

    def stability(p):
        ln_a1 = np.log(xs) + _nrtl_binary_lngammas(p[0], p[1], alpha, xs)[0]
        return float(np.min(np.diff(ln_a1) / np.diff(xs))) - margin

    best = None
    for guess in [(1.0, 2.0), (0.5, 2.5), (1.5, 1.5), (2.0, 2.0)]:
        r = minimize(obj, guess, method="SLSQP",
                     constraints=[{"type": "ineq", "fun": stability}])
        if r.success and stability(r.x) >= -1e-6 and (best is None or r.fun < best.fun):
            best = r
    if best is None:
        raise RuntimeError("THF/水 の NRTL パラメータ決定に失敗しました")
    return float(best.x[0]), float(best.x[1])


@lru_cache(maxsize=None)
def _nrtl_from_unifac(groups_i: tuple, groups_j: tuple, alpha: float = _NRTL_ALPHA) -> tuple:
    """UNIFAC Dortmund の 2 成分活量係数に NRTL（τ = a + b/T）を回帰する。

    Returns (a_ij, b_ij, a_ji, b_ji)。groups_* は UNIFAC サブグループの (id, 数) タプル。
    """
    chemgroups = [dict(groups_i), dict(groups_j)]
    Ts = [293.15, 323.15, 353.15, 383.15]
    xs = np.concatenate([[0.001, 0.005, 0.02], np.linspace(0.05, 0.95, 10), [0.98, 0.995, 0.999]])
    ref = []
    for T in Ts:
        ge = UNIFAC.from_subgroups(chemgroups=chemgroups, version=1, T=T, xs=[0.5, 0.5],
                                   interaction_data=DOUFIP2016, subgroups=DOUFSG)
        for x in xs:
            g = ge.to_T_xs(T, [x, 1.0 - x]).gammas()
            ref.append((T, x, np.log(g[0]), np.log(g[1])))
    ref = np.array(ref)

    def resid(p):
        a12, b12, a21, b21 = p
        t12 = a12 + b12 / ref[:, 0]
        t21 = a21 + b21 / ref[:, 0]
        l1, l2 = _nrtl_binary_lngammas(t12, t21, alpha, ref[:, 1])
        return np.concatenate([l1 - ref[:, 2], l2 - ref[:, 3]])

    r = least_squares(resid, [0.0, 300.0, 0.0, 300.0], method="lm")
    return tuple(float(v) for v in r.x)


def _needs_thf_water_nrtl(thermo_ids) -> bool:
    return _THF_ID in thermo_ids and _WATER_ID in thermo_ids


def _build_ge_model(thermo_ids, chemgroups, T: float, zs: list):
    """活量係数モデルを生成する。THF と水を両方含む系のみ NRTL、それ以外は UNIFAC Dortmund。"""
    if not _needs_thf_water_nrtl(thermo_ids):
        return UNIFAC.from_subgroups(chemgroups=chemgroups, version=1, T=T, xs=zs,
                                     interaction_data=DOUFIP2016, subgroups=DOUFSG)
    n = len(thermo_ids)
    tau_as = [[0.0] * n for _ in range(n)]
    tau_bs = [[0.0] * n for _ in range(n)]
    alphas = [[0.0 if i == j else _NRTL_ALPHA for j in range(n)] for i in range(n)]
    az = THF_WATER_AZEOTROPE
    t_tw, t_wt = _thf_water_nrtl_taus(az["T_C"], az["w_THF"], az["P_kPa"])
    for i in range(n):
        for j in range(i + 1, n):
            pair = (thermo_ids[i], thermo_ids[j])
            if pair == (_THF_ID, _WATER_ID):
                tau_as[i][j], tau_as[j][i] = t_tw, t_wt
            elif pair == (_WATER_ID, _THF_ID):
                tau_as[i][j], tau_as[j][i] = t_wt, t_tw
            else:
                a_ij, b_ij, a_ji, b_ji = _nrtl_from_unifac(
                    tuple(sorted(chemgroups[i].items())), tuple(sorted(chemgroups[j].items())))
                tau_as[i][j], tau_bs[i][j] = a_ij, b_ij
                tau_as[j][i], tau_bs[j][i] = a_ji, b_ji
    return NRTL(T=T, xs=zs, tau_as=tau_as, tau_bs=tau_bs, alpha_cs=alphas)


@st.cache_resource
def _get_hvap_correlations(thermo_ids: tuple):
    """各成分の蒸発熱相関式リスト（EnthalpyVaporization callables）を返す。

    thermo_ids は _solvents_to_flasher_args が返すサロゲート済み ID タプル。
    @st.cache_resource でキャッシュされるため、初回のみ ChemicalConstantsPackage
    が呼ばれる。
    """
    _, props = ChemicalConstantsPackage.from_IDs(list(thermo_ids))
    return props.EnthalpyVaporizations


def calc_hvap_mix_J_mol(thermo_ids: tuple, y: list, T_K: float) -> float:
    """蒸気相モル分率 y[i] で重み付けした混合蒸発熱 [J/mol_vapor] を返す。

    Parameters
    ----------
    thermo_ids : tuple of str
        _solvents_to_flasher_args が返すサロゲート済み thermo ID タプル
    y : list[float]
        各成分の気相モル分率 (sum ≈ 1)
    T_K : float
        温度 [K]

    Returns
    -------
    float  J/mol  (> 0)。相関式が失敗した場合はフォールバック 40000 J/mol。
    """
    hvap_funcs = _get_hvap_correlations(thermo_ids)
    total = 0.0
    for yi, hv in zip(y, hvap_funcs):
        try:
            h = hv(T_K)
            if h is not None and h > 0:
                total += yi * h
        except Exception:
            pass
    return total if total > 0 else 40000.0


@st.cache_resource
def build_flasher(T: float, thermo_id_1: str, thermo_id_2: str,
                  surrogate_1: str = None, surrogate_2: str = None,
                  unifac_override_1: tuple = None, unifac_override_2: tuple = None,
                  vp_offset_1: float = 0.0, vp_offset_2: float = 0.0):
    P = 101325
    ids = ['water',
           surrogate_1 if surrogate_1 else thermo_id_1,
           surrogate_2 if surrogate_2 else thermo_id_2]
    constants, correlations = ChemicalConstantsPackage.from_IDs(ids)
    if unifac_override_1 or unifac_override_2:
        new_groups = list(constants.UNIFAC_Dortmund_groups)
        if unifac_override_1:
            new_groups[1] = dict(unifac_override_1)
        if unifac_override_2:
            new_groups[2] = dict(unifac_override_2)
        constants = constants.with_new_constants(UNIFAC_Dortmund_groups=new_groups)
    GE = _build_ge_model(ids, constants.UNIFAC_Dortmund_groups, T, [1/3]*3)
    vp_list = list(correlations.VaporPressures)
    if vp_offset_1:
        shifted1 = _VaporPressureShifted(vp_list[1], vp_offset_1)
        vp_list[1] = shifted1
        correlations.VaporPressures[1] = shifted1  # FlashVLN 内部の K値推算にも反映
    if vp_offset_2:
        shifted2 = _VaporPressureShifted(vp_list[2], vp_offset_2)
        vp_list[2] = shifted2
        correlations.VaporPressures[2] = shifted2  # FlashVLN 内部の K値推算にも反映
    liquid = GibbsExcessLiquid(
        VaporPressures=vp_list,
        VolumeLiquids=correlations.VolumeLiquids,
        HeatCapacityGases=correlations.HeatCapacityGases,
        GibbsExcessModel=GE,
        T=T, P=P, zs=[1/3]*3,
    )
    eos_kwargs = dict(Tcs=constants.Tcs, Pcs=constants.Pcs, omegas=constants.omegas)
    gas = CEOSGas(PRMIX, eos_kwargs=eos_kwargs,
                  HeatCapacityGases=correlations.HeatCapacityGases,
                  T=T, P=P, zs=[1/3]*3)
    return FlashVLN(constants, correlations,
                    liquids=[liquid, liquid], gas=gas)


def calc_lle_diagram(T_C, solvent1: dict, solvent2: dict, n_grid=25):
    """
    三角図上を格子スキャン → FlashVLN で2液相分離点を検出

    Returns:
        tie_lines: list of (L1_zs, L2_zs)
        binodal_pts: 各液相の組成点リスト
    """
    T = T_C + 273.15
    P = 101325
    flasher = build_flasher(
        T, solvent1["thermo_id"], solvent2["thermo_id"],
        surrogate_1=solvent1.get("thermo_surrogate"),
        surrogate_2=solvent2.get("thermo_surrogate"),
        unifac_override_1=tuple(sorted(solvent1["unifac_groups"].items())) if "unifac_groups" in solvent1 else None,
        unifac_override_2=tuple(sorted(solvent2["unifac_groups"].items())) if "unifac_groups" in solvent2 else None,
        vp_offset_1=solvent1.get("vp_T_offset", 0.0),
        vp_offset_2=solvent2.get("vp_T_offset", 0.0),
    )

    tie_lines = []
    binodal_pts = []
    seen = set()

    for i in range(n_grid + 1):
        for j in range(n_grid + 1 - i):
            k = n_grid - i - j
            if k < 0:
                continue
            z = [i / n_grid, j / n_grid, k / n_grid]
            try:
                res = flasher.flash(T=T, P=P, zs=z)
                if res.phase_count == 2 and res.liquid_count == 2:
                    L1 = tuple(round(x, 6) for x in res.liquids[0].zs)
                    L2 = tuple(round(x, 6) for x in res.liquids[1].zs)
                    key = (min(L1, L2), max(L1, L2))
                    if key not in seen:
                        seen.add(key)
                        tie_lines.append((list(L1), list(L2)))
                        binodal_pts.append(list(L1))
                        binodal_pts.append(list(L2))
            except Exception:
                continue

    return tie_lines, binodal_pts


def calc_layer_composition(T_C, amounts, unit, solvent1: dict, solvent2: dict):
    """
    Parameters
    ----------
    amounts : [water, solvent1, solvent2] in unit
    unit    : 'g' | 'mol' | 'mL'

    Returns
    -------
    dict with keys: phase_count, water_layer, organic_layer, input_zs,
                    beta_water, beta_organic, error
    """
    MW = [18.015, solvent1["mw"], solvent2["mw"]]
    rho = [density_water(T_C), density_solvent(solvent1, T_C), density_solvent(solvent2, T_C)]

    # 単位変換 → グラム
    if unit == 'g':
        grams = list(amounts)
    elif unit == 'mol':
        grams = [a * mw for a, mw in zip(amounts, MW)]
    else:  # mL
        grams = [a * r for a, r in zip(amounts, rho)]

    total_grams = sum(grams)
    if total_grams == 0:
        return {'error': '投入量がすべてゼロです', 'phase_count': 0,
                'input_zs': None, 'water_layer': None, 'organic_layer': None,
                'beta_water': None, 'beta_organic': None}

    moles = [g / mw for g, mw in zip(grams, MW)]
    total_moles = sum(moles)
    z = [m / total_moles for m in moles]

    T = T_C + 273.15
    P = 101325
    try:
        flasher = build_flasher(
            T, solvent1["thermo_id"], solvent2["thermo_id"],
            surrogate_1=solvent1.get("thermo_surrogate"),
            surrogate_2=solvent2.get("thermo_surrogate"),
            unifac_override_1=tuple(sorted(solvent1["unifac_groups"].items())) if "unifac_groups" in solvent1 else None,
            unifac_override_2=tuple(sorted(solvent2["unifac_groups"].items())) if "unifac_groups" in solvent2 else None,
            vp_offset_1=solvent1.get("vp_T_offset", 0.0),
            vp_offset_2=solvent2.get("vp_T_offset", 0.0),
        )
        res = flasher.flash(T=T, P=P, zs=z)
    except Exception as e:
        return {'error': str(e), 'phase_count': 0,
                'input_zs': z, 'water_layer': None, 'organic_layer': None,
                'beta_water': None, 'beta_organic': None}

    if not (res.phase_count == 2 and res.liquid_count == 2):
        return {'phase_count': 1, 'input_zs': z, 'error': None,
                'water_layer': None, 'organic_layer': None,
                'beta_water': None, 'beta_organic': None}

    # 水層 = Water mole fraction が高い方
    if res.liquids[0].zs[0] >= res.liquids[1].zs[0]:
        wl_idx, ol_idx = 0, 1
    else:
        wl_idx, ol_idx = 1, 0

    def phase_metrics(xs, beta):
        phase_moles_i = [xs[i] * beta * total_moles for i in range(3)]
        phase_grams_i = [phase_moles_i[i] * MW[i] for i in range(3)]
        total_pg = sum(phase_grams_i)
        phase_vol_i = [phase_grams_i[i] / rho[i] for i in range(3)]
        total_pv = sum(phase_vol_i)
        return {
            'zs': list(xs),
            'mol_pct': [x * 100 for x in xs],
            'ww_pct': [g / total_pg * 100 for g in phase_grams_i],
            'vv_pct': [v / total_pv * 100 for v in phase_vol_i],
            'beta': beta,
            'moles':      phase_moles_i,
            'grams':      phase_grams_i,
            'volumes_mL': phase_vol_i,
        }

    return {
        'phase_count': 2,
        'input_zs': z,
        'water_layer': phase_metrics(res.liquids[wl_idx].zs, res.betas[wl_idx]),
        'organic_layer': phase_metrics(res.liquids[ol_idx].zs, res.betas[ol_idx]),
        'beta_water': res.betas[wl_idx],
        'beta_organic': res.betas[ol_idx],
        'error': None,
    }


# ── VLE 機能 ──────────────────────────────────────────────────────────────────

@st.cache_resource
def build_flasher_general(thermo_ids: tuple, unifac_overrides: tuple = (),
                          vp_offsets: tuple = ()):
    """可変成分数 (2〜4) の FlashVLN を構築・キャッシュする。

    thermo_ids: surrogateを適用済みの thermo ID タプル
    unifac_overrides: ((idx, items_tuple), ...) UNIFAC グループ上書き用
    vp_offsets: ((idx, T_offset_K), ...) 蒸気圧曲線温度シフト用
    """
    n = len(thermo_ids)
    T0, P0 = 298.15, 101325
    zs0 = [1 / n] * n
    constants, correlations = ChemicalConstantsPackage.from_IDs(list(thermo_ids))
    if unifac_overrides:
        new_groups = list(constants.UNIFAC_Dortmund_groups)
        for idx, items in unifac_overrides:
            new_groups[idx] = dict(items)
        constants = constants.with_new_constants(UNIFAC_Dortmund_groups=new_groups)
    GE = _build_ge_model(thermo_ids, constants.UNIFAC_Dortmund_groups, T0, zs0)
    vp_list = list(correlations.VaporPressures)
    for idx, dT in vp_offsets:
        shifted = _VaporPressureShifted(vp_list[idx], dT)
        vp_list[idx] = shifted
        correlations.VaporPressures[idx] = shifted  # FlashVLN 内部の K値推算にも反映
    liquid = GibbsExcessLiquid(
        VaporPressures=vp_list,
        VolumeLiquids=correlations.VolumeLiquids,
        HeatCapacityGases=correlations.HeatCapacityGases,
        GibbsExcessModel=GE,
        T=T0, P=P0, zs=zs0,
    )
    eos_kwargs = dict(Tcs=constants.Tcs, Pcs=constants.Pcs, omegas=constants.omegas)
    gas = CEOSGas(PRMIX, eos_kwargs=eos_kwargs,
                  HeatCapacityGases=correlations.HeatCapacityGases,
                  T=T0, P=P0, zs=zs0)
    return FlashVLN(constants, correlations, liquids=[liquid, liquid], gas=gas)


def _solvents_to_flasher_args(solvents: list):
    """solvent dict リストから build_flasher_general 用ハッシュ可能引数を生成する。"""
    thermo_ids = tuple(s.get("thermo_surrogate", s["thermo_id"]) for s in solvents)
    unifac_overrides = tuple(
        (i, tuple(sorted(s["unifac_groups"].items())))
        for i, s in enumerate(solvents)
        if "unifac_groups" in s
    )
    vp_offsets = tuple(
        (i, s["vp_T_offset"])
        for i, s in enumerate(solvents)
        if s.get("vp_T_offset", 0.0) != 0.0
    )
    return thermo_ids, unifac_overrides, vp_offsets


def _bubble_point_flash(flasher, P: float, zs: list,
                        T_lo: float = 250.0, T_hi: float = 450.0,
                        n_bisect: int = 12, try_vf0: bool = True):
    """泡点フラッシュ。VF=0指定を試し、失敗時は T-P 二分探索にフォールバック。

    LL 分離が先行する異質共沸系（例: Water-Toluene 含む多成分系）では
    VF=0 仕様が thermo ライブラリ内部で IndexError を起こすため、
    T-P フラッシュの二分探索で LL→VLL 転移温度（泡点）を求める。

    T_lo / T_hi にウォームスタート値を渡すと探索を絞り込んで高速化できる。
    try_vf0=False を指定すると VF=0 を試みず直接二分探索に入る
    （LL系で VF=0 が常に失敗する場合に 2s/step の無駄を回避できる）。

    Returns
    -------
    flash result object (gas 相が存在する)
    """
    # Fast path: VF=0 仕様（2成分系や均一液相系で動作）
    if try_vf0:
        _r = _flash_vf0_timeout(flasher, P, zs, timeout=0.35)
        if _r is not None:
            return _r

    # Fallback: T-P 二分探索で vapor が初めて現れる T を探す
    _T_lo, _T_hi = T_lo, T_hi

    # warm-start で範囲が狭い（< 50K）場合は T_hi 検証をスキップして高速化
    # （T_bp + 30K は必ず気相領域なので信頼できる）
    if T_hi - T_lo > 50.0:
        # 広い初期範囲: T_hi に vapor があることを確認し、なければ拡張
        _T_hi_ok = False
        for _ in range(5):          # 最大 5 回 × 20K 拡張
            try:
                r = flasher.flash(T=_T_hi, P=P, zs=zs)
                if r.gas is not None and r.VF > 0:
                    _T_hi_ok = True
                    break
            except Exception:
                pass
            _T_hi = min(500.0, _T_hi + 20.0)

        if not _T_hi_ok:
            for T_try in [450.0, 420.0, 400.0, 380.0, 360.0, 350.0]:
                try:
                    r = flasher.flash(T=T_try, P=P, zs=zs)
                    if r.gas is not None and r.VF > 0:
                        _T_hi = T_try
                        break
                except Exception:
                    pass

    # --- 二分探索（n_bisect 回、デフォルト 12 回 ≈ 0.05°C 精度 @ 200K 幅, 0.01°C @ 40K 幅）---
    for _ in range(n_bisect):
        T_mid = (_T_lo + _T_hi) / 2.0
        try:
            r = flasher.flash(T=T_mid, P=P, zs=zs)
            if r.gas is not None and r.VF > 1e-10:
                _T_hi = T_mid
            else:
                _T_lo = T_mid
        except Exception:
            _T_lo = T_mid  # 失敗 → より低温側を疑う

    res = flasher.flash(T=_T_hi, P=P, zs=zs)
    if res.gas is None:
        raise ValueError(
            f"Bubble point not found in [{_T_lo - 273.15:.1f}, {_T_hi - 273.15:.1f}] °C"
        )
    return res


def _dew_point_flash(flasher, P: float, zs: list,
                     T_bp_K: float = None, try_vf1: bool = True):
    """露点フラッシュ。VF=1指定を試み、失敗時は T-P 二分探索にフォールバック。

    T_bp_K: 泡点温度(K)。露点 >= 泡点 なので T_lo の下限として活用。

    Returns
    -------
    flash result object (liquid 相が存在する)
    """
    # Fast path: VF=1 仕様（均一系や2成分系で動作）
    if try_vf1:
        try:
            r = flasher.flash(P=P, VF=1, zs=zs)
            if r is not None:
                return r
        except Exception:
            pass

    # Fallback: T-P 二分探索で vapor が完全になる T を探す
    _T_lo = T_bp_K if T_bp_K else 250.0
    _T_hi = _T_lo + 50.0

    # T_hi で VF >= 1 を確認し、なければ上方拡張
    _T_hi_ok = False
    for _ in range(6):          # 最大 6 回 × 20K 拡張
        try:
            r = flasher.flash(T=_T_hi, P=P, zs=zs)
            if r.VF >= 1.0 - 1e-6:
                _T_hi_ok = True
                break
        except Exception:
            pass
        _T_hi = min(550.0, _T_hi + 20.0)

    if not _T_hi_ok:
        raise ValueError(
            f"Dew point not found above T_lo={_T_lo - 273.15:.1f} °C"
        )

    # --- 二分探索 ---
    for _ in range(10):
        T_mid = (_T_lo + _T_hi) / 2.0
        try:
            r = flasher.flash(T=T_mid, P=P, zs=zs)
            if r.VF >= 1.0 - 1e-6:
                _T_hi = T_mid
            else:
                _T_lo = T_mid
        except Exception:
            _T_hi = T_mid  # 収束不安定な高温側を除外

    return flasher.flash(T=_T_hi, P=P, zs=zs)


def calc_vapor_pressure_curve(thermo_id: str, T_min_C: float = 0.0,
                               T_max_C: float = 150.0, n: int = 200,
                               T_offset_K: float = 0.0):
    """蒸気圧曲線を計算する。

    T_offset_K: サロゲートの沸点ずれを補正するための温度シフト量 (K)
    Returns: {"T_C": list, "P_kPa": list, "T_bp_C": float or None,
              "T_valid_min_C": float or None, "T_valid_max_C": float or None}
    """
    _, props = ChemicalConstantsPackage.from_IDs([thermo_id])
    vp_raw = props.VaporPressures[0]
    vp = _VaporPressureShifted(vp_raw, T_offset_K) if T_offset_K else vp_raw
    temps = [T_min_C + (T_max_C - T_min_C) * i / (n - 1) for i in range(n)]
    pressures = [vp(t + 273.15) / 1000.0 for t in temps]  # Pa → kPa

    P_atm = 101.325  # kPa
    T_bp = None
    try:
        p_lo = vp(T_min_C + 273.15) / 1000.0
        p_hi = vp(T_max_C + 273.15) / 1000.0
        if p_lo <= P_atm <= p_hi:
            lo, hi = T_min_C, T_max_C
            for _ in range(60):
                mid = (lo + hi) / 2.0
                if vp(mid + 273.15) / 1000.0 < P_atm:
                    lo = mid
                else:
                    hi = mid
            T_bp = (lo + hi) / 2.0
    except Exception:
        T_bp = None

    T_valid_min_C = (vp.Tmin - 273.15) if getattr(vp, "Tmin", None) is not None else None
    T_valid_max_C = (vp.Tmax - 273.15) if getattr(vp, "Tmax", None) is not None else None

    return {"T_C": temps, "P_kPa": pressures, "T_bp_C": T_bp,
            "T_valid_min_C": T_valid_min_C, "T_valid_max_C": T_valid_max_C}


def _steam_distillation_T3(vp_funcs: list, P: float,
                            T_lo: float = 270.0, T_hi: float = 460.0) -> float:
    """純成分蒸気圧の和 = P となる温度 (K) を二分探索で求める。

    完全非混和系での不均一共沸温度 T3 の推定に使用（スチーム蒸留方程式）。
    Σ P_sat_i(T) = P は活量係数に依存しないため、UNIFAC の誤差を受けない。
    """
    P_sum = lambda T_K: sum(vp(T_K) for vp in vp_funcs)
    # 探索範囲を P_sum の大小に合わせて調整
    while P_sum(T_lo) >= P and T_lo > 200.0:
        T_lo -= 20.0
    while P_sum(T_hi) < P and T_hi < 600.0:
        T_hi += 20.0
    for _ in range(60):
        T_mid = (T_lo + T_hi) / 2.0
        if P_sum(T_mid) < P:
            T_lo = T_mid
        else:
            T_hi = T_mid
    return (T_lo + T_hi) / 2.0


def _detect_three_phase(x1_list, T_b_list, y1_list, tol=1.0, min_points=8):
    """泡点曲線のプラトー（三相域）を検出する。

    投票法（voting）により外れ値に強い三相温度 T3 を推定する。
    各 T_b 値を候補として tol°C 以内の支持点数を数え、最多得票の温度を T3 とする。
    連続グループ法と異なり、少数の外れ値があっても検出が崩れない。

    Returns: {"T3_C": float, "x_alpha": float, "x_beta": float, "y3": float}
    または None（検出不可）
    """
    # 内部組成（端点を除外）のみ使用
    interior = [(i, x, T, y) for i, (x, T, y) in enumerate(zip(x1_list, T_b_list, y1_list))
                if T is not None and 0.001 < x < 0.999]
    if len(interior) < min_points:
        return None

    # 投票法: 各 T_b 値を候補として tol°C 以内の点数を数える
    T_vals = [T for _, _, T, _ in interior]
    best_T, best_count = None, 0
    for T_cand in T_vals:
        count = sum(1 for T in T_vals if abs(T - T_cand) <= tol)
        if count > best_count:
            best_count = count
            best_T = T_cand

    if best_count < min_points:
        return None

    plateau = [(i, x, T, y) for i, x, T, y in interior if abs(T - best_T) <= tol]
    x_alpha = min(pt[1] for pt in plateau)
    x_beta = max(pt[1] for pt in plateau)

    # 純成分沸点との誤検出防止（端点 ≈ 0 または ≈ 1 の場合は純成分の誤検出）
    if x_alpha < 0.001 or x_beta > 0.999:
        return None

    T3_C = sum(pt[2] for pt in plateau) / len(plateau)
    y3_vals = [pt[3] for pt in plateau if pt[3] is not None]
    y3 = sum(y3_vals) / len(y3_vals) if y3_vals else None

    return {"T3_C": T3_C, "x_alpha": x_alpha, "x_beta": x_beta, "y3": y3}


def calc_vle_xy(solvents: list, P_kPa: float, n: int = 120):
    """2成分系 VLE xy 線図・T-xy 線図用データを計算する。

    solvents: 2成分の solvent dict リスト
    Returns: {"x1": list, "y1": list, "T_bubble_C": list, "T_dew_C": list}
    """
    thermo_ids, unifac_overrides, vp_offsets = _solvents_to_flasher_args(solvents)
    flasher = build_flasher_general(thermo_ids, unifac_overrides, vp_offsets)
    P = P_kPa * 1000.0

    # VF=0 の実行可否を1点でプローブしてから全点に適用（LL系のタイムアウト42秒削減）
    _probe_z = [0.5, 0.5]
    _try_vf0 = _flash_vf0_timeout(flasher, P, _probe_z, timeout=0.35) is not None

    x1_list, y1_list, T_b_list, T_d_list = [], [], [], []
    for i in range(n + 1):
        z1 = i / n
        z = [z1, 1.0 - z1]
        y1, T_b, T_d = None, None, None
        try:
            res_b = _bubble_point_flash(flasher, P, z, try_vf0=_try_vf0)
            y1 = res_b.gas.zs[0] if res_b.gas else None
            T_b = res_b.T - 273.15
        except Exception:
            pass
        try:
            T_bp_K_hint = (T_b + 273.15) if T_b is not None else None
            res_d = _dew_point_flash(flasher, P, z, T_bp_K=T_bp_K_hint)
            T_d = res_d.T - 273.15
        except Exception:
            pass
        x1_list.append(z1)
        y1_list.append(y1)
        T_b_list.append(T_b)
        T_d_list.append(T_d)

    three_phase = _detect_three_phase(x1_list, T_b_list, y1_list)
    if three_phase is not None:
        # 物理的検証: T3 は両純成分の沸点より低い必要がある（Konovalov の第2法則）
        # 不均一共沸点 T3 は系中の最低沸点であり、必ず両端の純成分沸点を下回る。
        # 均一共沸系の false positive では検出「T3」≈ 下端純成分沸点 となるため排除できる。
        _T3 = three_phase["T3_C"]
        _tb0 = T_b_list[0]    # x1=0 の純成分沸点
        _tb1 = T_b_list[-1]   # x1=1 の純成分沸点
        if _tb0 is not None and _tb1 is not None:
            _T_min_pure = min(_tb0, _tb1)
            if _T3 > _T_min_pure - 0.5:
                three_phase = None  # T3 ≥ 純成分最低沸点 → 均一共沸の誤検出

    if three_phase is not None:
        # 液液分離の確認: 泡点が平坦なだけの均一系（例: THF/水）を三相域と誤検出しないよう、
        # プラトー内の組成が T3 直下で実際に 2 液相に分かれるかを確かめる。
        _xa, _xb = three_phase["x_alpha"], three_phase["x_beta"]
        _probe_xs = [_xa + (_xb - _xa) * f for f in (0.25, 0.5, 0.75)]
        _T3_K = three_phase["T3_C"] + 273.15
        if not any(_is_two_liquid(flasher, P, [x, 1.0 - x], _T3_K) for x in _probe_xs):
            three_phase = None

    if three_phase is not None:
        T3_C = three_phase["T3_C"]
        T3_K = T3_C + 273.15

        # Step 1: T3 フラッシュで三相域を特定し T_b と y1 を補完する
        # VF > 0 at T3 → 気化開始温度が T3 以下 → 正しい T_b = T3
        # 「既に T3 近傍」の点もスキップせず全点を対象にする（初期計算の y1 が
        # 準安定 VL 解から求まっている場合に y1 も合わせて修正するため）
        for idx, x1 in enumerate(x1_list):
            if not (0.001 < x1 < 0.999):
                continue
            T_b = T_b_list[idx]
            if T_b is not None and abs(T_b - T3_C) <= 2.0:
                continue  # 既に T3 近傍 → T_b 補正は不要（y1 は Step 3 で更新）
            try:
                r_t3 = flasher.flash(T=T3_K, P=P, zs=[x1, 1.0 - x1])
                if r_t3.gas is not None and r_t3.VF > 1e-6:
                    T_b_list[idx] = T3_C
                    y1_list[idx] = r_t3.gas.zs[0]
            except Exception:
                pass

        # Step 2: 補完後に x_alpha, x_beta を更新（三相域の両端を再確定）
        # 三相温度は系の最低沸点（不均一共沸点）なので、T_b < T3 の点はフラッシュが
        # 液液分離を見逃した準安定解（例: トルエン/水 x=0.95 で 70.7 °C）。実際は三相域内。
        plateau_xs = [x for x, T_b in zip(x1_list, T_b_list)
                      if T_b is not None and (abs(T_b - T3_C) <= 1.0 or T_b < T3_C - 1.0)
                      and 0.001 < x < 0.999]
        if plateau_xs:
            three_phase["x_alpha"] = min(plateau_xs)
            three_phase["x_beta"] = max(plateau_xs)

        # Step 3: 三相域外の端部（x < x_alpha または x > x_beta）のみ精密化する
        # 三相域内の組成は Step 1 の T3 補正を維持し、flasher が返す可能性のある
        # 準安定 VL 解で上書きされるのを防ぐ
        x_alpha_step2 = three_phase["x_alpha"]
        x_beta_step2 = three_phase["x_beta"]
        for idx, x1 in enumerate(x1_list):
            if not (0.001 < x1 < 0.999):
                continue
            if x_alpha_step2 <= x1 <= x_beta_step2:
                continue  # 三相域内はスキップ（Step 1 の補正を保護）
            T_b = T_b_list[idx]
            if T_b is not None and abs(T_b - T3_C) <= 3.0:
                try:
                    res_b = _bubble_point_flash(
                        flasher, P, [x1, 1.0 - x1],
                        T_lo=T3_K - 5.0, T_hi=T3_K + 5.0,
                        n_bisect=12, try_vf0=False
                    )
                    T_b_list[idx] = res_b.T - 273.15
                    if res_b.gas:
                        y1_list[idx] = res_b.gas.zs[0]
                except Exception:
                    pass

        # Step 4: 三相域全組成の T_b と y1 を T3・y3 に統一する
        # 物理的根拠: 異質共沸点では T と気相組成は全体組成によらず一定（Gibbs 相律 F=0）
        x_alpha_f = three_phase["x_alpha"]
        x_beta_f = three_phase["x_beta"]
        # y3 を三相域内の y1 平均値で更新（Step 1 の T3 フラッシュ結果を使用）
        y3_vals_updated = [y1_list[idx] for idx, x1 in enumerate(x1_list)
                           if y1_list[idx] is not None
                           and x_alpha_f <= x1 <= x_beta_f
                           and 0.001 < x1 < 0.999]
        if y3_vals_updated:
            y3 = sum(y3_vals_updated) / len(y3_vals_updated)
            three_phase["y3"] = y3
        else:
            y3 = three_phase.get("y3")
        # 三相域の T_b と y1 を強制適用（準安定解の混入を排除）
        if y3 is not None:
            for idx, x1 in enumerate(x1_list):
                if x_alpha_f <= x1 <= x_beta_f and 0.001 < x1 < 0.999:
                    T_b_list[idx] = T3_C
                    y1_list[idx] = y3

        # Step 5: 不均一共沸点組成(y1≈y3)では露点=三相温度（ギブス相律: C=2,P=3→F=0）
        if y3 is not None:
            for idx, y1 in enumerate(y1_list):
                if y1 is not None and abs(y1 - y3) < 0.015:
                    T_d_list[idx] = T3_C

        # Step 6: スチーム蒸留方程式による T3 補正
        # 完全非混和系では Σ P_sat_i(T3) = P が成立し、活量係数不要で正確な T3 が得られる。
        # UNIFAC が T3 を過小評価する系（例: Toluene-Water, MTHP-Water）で補正する。
        # T3_steam が UNIFAC 値より 2°C 以上高い場合のみ上書き（均一系への誤適用を防止）。
        try:
            _vp_funcs = list(flasher.liquids[0].VaporPressures)
            T3_steam_K = _steam_distillation_T3(_vp_funcs, P)
            T3_steam_C = T3_steam_K - 273.15
            T3_UNIFAC_C = three_phase["T3_C"]
            if T3_steam_C > T3_UNIFAC_C + 2.0:
                y3_steam = _vp_funcs[0](T3_steam_K) / P
                three_phase["T3_C"] = T3_steam_C
                three_phase["y3"] = y3_steam
                x_alpha_f = three_phase["x_alpha"]
                x_beta_f = three_phase["x_beta"]
                for idx, x1 in enumerate(x1_list):
                    if not (0.001 < x1 < 0.999):
                        continue
                    # 補正後の T3 より低い泡点は準安定解 → 三相域内（Step 2 と同じ考え方）
                    _tb = T_b_list[idx]
                    if (x_alpha_f <= x1 <= x_beta_f
                            or (_tb is not None and _tb < T3_steam_C - 1.0)):
                        T_b_list[idx] = T3_steam_C
                        y1_list[idx] = y3_steam
                        three_phase["x_alpha"] = min(three_phase["x_alpha"], x1)
                        three_phase["x_beta"] = max(three_phase["x_beta"], x1)
                y3 = y3_steam
                for idx, _y1 in enumerate(y1_list):
                    if _y1 is not None and abs(_y1 - y3) < 0.015:
                        T_d_list[idx] = T3_steam_C
        except Exception:
            pass

    return {"x1": x1_list, "y1": y1_list,
            "T_bubble_C": T_b_list, "T_dew_C": T_d_list,
            "three_phase": three_phase}


def _is_two_liquid(flasher, P: float, zs: list, T_K: float) -> bool:
    """泡点直下 (T_K − 0.3 K) で液相が 2 相に分かれるか（不均一系か）を判定する。

    泡点が数ステップ一定でも、均一系の共沸点近傍では温度変化が小さいだけで
    三相域ではない。三相域キャッシュはこの判定が True の場合のみ使用する。
    """
    try:
        r = flasher.flash(T=T_K - 0.3, P=P, zs=zs)
        return r.liquid_count >= 2
    except Exception:
        return False


def calc_rayleigh_distillation(solvents: list, initial_moles: list,
                                P_kPa: float, n_steps: int = 50):
    """レイリー蒸留（微分ステップ法）シミュレーション。

    solvents: solvent dict リスト (2〜4成分)
    initial_moles: 各成分の初期モル数
    Returns: {"evap_fraction": list, "amounts": {name: list[mol]},
              "total": list[mol], "T_bp": list[°C or None]}
    """
    thermo_ids, unifac_overrides, vp_offsets = _solvents_to_flasher_args(solvents)
    flasher = build_flasher_general(thermo_ids, unifac_overrides, vp_offsets)
    P = P_kPa * 1000.0

    L = [float(m) for m in initial_moles]
    total_initial = sum(L)
    if total_initial <= 0:
        return {"evap_fraction": [],
                "amounts": {s["name"]: [] for s in solvents},
                "total": [], "T_bp": [], "vapor_fracs": []}

    dV = total_initial / n_steps
    evap_fractions, total_history, T_bp_history = [], [], []
    vapor_history = []
    amounts_history = [[] for _ in range(len(solvents))]
    _T_bp_prev_K = None   # ウォームスタート用: 前ステップの泡点 (K)
    _vf0_known_fail = False  # True になると VF=0 をスキップして二分探索へ直行
    _probe_fail_streak = 0   # プローブが連続失敗した回数
    _PROBE_SKIP_AFTER = 3    # 3回連続失敗 → プローブをスキップして二分探索に直行
    _y_cached = None          # 三相域キャッシュ: 気相組成（T一定 → y一定）
    _T_three_phase_K = None   # 三相温度 (K) 。None = 三相域未確定
    _three_phase_streak = 0   # 連続して同温度だったステップ数
    _THREE_PHASE_CONFIRM = 2  # この回数連続同温度で「三相域確定」とみなす
    _three_phase_by_steam = False  # 三相域がスチーム蒸留補正で確定したか

    # スチーム蒸留 T3 補正の事前計算（2成分系のみ）
    # Σ Psat_i(T3) = P を解いて T3_steam を求め、UNIFAC の過小評価を補正する。
    # 均一系誤適用防止: UNIFAC T_bp が T3_steam より 3°C 以上低い場合のみ上書き。
    _T3_steam_override_K = None
    _y3_steam_override = None
    if len(solvents) == 2:
        try:
            _vp_funcs_sd = list(flasher.liquids[0].VaporPressures)
            _T3s_K = _steam_distillation_T3(_vp_funcs_sd, P)
            _y3s_0 = _vp_funcs_sd[0](_T3s_K) / P
            _y3s_1 = _vp_funcs_sd[1](_T3s_K) / P
            _T3_steam_override_K = _T3s_K
            _y3_steam_override = [_y3s_0, _y3s_1]
        except Exception:
            pass

    for step in range(n_steps + 1):
        total = sum(L)
        if total < 1e-9:
            break

        evap_frac = (total_initial - total) / total_initial
        evap_fractions.append(evap_frac)
        for i in range(len(solvents)):
            amounts_history[i].append(L[i])
        total_history.append(total)

        if total < 1e-6 * total_initial:
            T_bp_history.append(None)
            break

        z = [li / total for li in L]

        # ── 三相域高速パス: Gibbs の相律より T も y も一定 ──
        # 三相域が確定している間は flash をスキップしてキャッシュを再利用する
        if _T_three_phase_K is not None:
            # 三相域では成分量の比が変わっても T_bp は変わらないが、
            # 1成分が枯渇し始めると T_bp が上昇する。
            # その検出は以下の「枯渇チェック」で行う:
            #   現在の液相組成 z と直前の気相組成 y_cached を比べて
            #   z_i / y_i の比が大きく変わっていれば枯渇が近い → フラッシュ実施
            _depleting = any(
                (z[i] < 0.02 and _y_cached[i] > 0.05)
                for i in range(len(solvents))
            )
            # 液組成が 2 液相域を外れたら（一方の液相が消失）三相域終了
            if not _depleting and not _three_phase_by_steam:
                _depleting = not _is_two_liquid(flasher, P, z, _T_three_phase_K)
            if not _depleting:
                # 三相域のまま → キャッシュ使用
                T_bp_history.append(_T_three_phase_K - 273.15)
                vapor_history.append(_y_cached[:])
                if step < n_steps:
                    for i in range(len(solvents)):
                        L[i] = max(0.0, L[i] - dV * _y_cached[i])
                continue   # flash 不要 → 次のステップへ
            else:
                # 枯渇を検知 → キャッシュ無効化して通常パスへ
                _T_three_phase_K = None
                _y_cached = None
                _three_phase_streak = 0
                _three_phase_by_steam = False

        res = None
        _steam_applied = False
        # プローブ失敗時は T_prev を T_lo として使用（探索範囲を狭める）
        _probe_confirmed_below = False

        # ── Fast path 1: VF=0 仕様（均一系で有効）──
        # LL 系では ~4s かかって失敗するため、タイムアウト付きで実行し
        # 一度 None が返ったら以降スキップして二分探索に直行する
        if not _vf0_known_fail:
            _r = _flash_vf0_timeout(flasher, P, z, timeout=0.35)
            if _r is not None:
                res = _r
            else:
                _vf0_known_fail = True   # 以降 VF=0 を試みない

        # ── Fast path 2: 前ステップの泡点を直接プローブ（1フラッシュ）──
        # 泡点が平坦 or わずかな上昇域で有効。連続失敗後はスキップして二分探索に直行
        if res is None and _T_bp_prev_K is not None and _probe_fail_streak < _PROBE_SKIP_AFTER:
            try:
                _r = flasher.flash(T=_T_bp_prev_K, P=P, zs=z)
                if _r.gas is not None and _r.VF > 1e-10:
                    res = _r   # 前回泡点がまだ気相域 → そのまま使用
                    _probe_fail_streak = 0
                else:
                    # VF≈0 → 現在 T_prev は泡点未満 → T_lo として使える
                    _probe_confirmed_below = True
                    _probe_fail_streak += 1
            except Exception:
                _probe_fail_streak += 1
        elif res is None and _T_bp_prev_K is not None and _probe_fail_streak >= _PROBE_SKIP_AFTER:
            # プローブをスキップ: T_prev は泡点未満と確定しているので T_lo に使う
            _probe_confirmed_below = True

        # ── Fallback: T-P 二分探索（泡点が上昇した場合 / コールドスタート）──
        if res is None:
            if _T_bp_prev_K is not None and _probe_confirmed_below:
                # T_prev < T_bp なのが分かっているので、狭い範囲で高速探索
                t_lo_hint = _T_bp_prev_K
                t_hi_hint = min(500.0, _T_bp_prev_K + 25.0)
            elif _T_bp_prev_K is not None:
                t_lo_hint = max(250.0, _T_bp_prev_K - 10.0)
                t_hi_hint = min(500.0, _T_bp_prev_K + 30.0)
            else:
                t_lo_hint, t_hi_hint = 250.0, 450.0
            try:
                res = _bubble_point_flash(flasher, P, z,
                                          T_lo=t_lo_hint, T_hi=t_hi_hint,
                                          try_vf0=False)
            except Exception:
                res = None
            if res is None and (t_lo_hint, t_hi_hint) != (250.0, 450.0):
                # 一成分が枯渇すると泡点が +25 K を超えて跳ぶ（例: トルエン/水で水が
                # 無くなると 84 → 110 °C）。狭い探索範囲で見つからなければ全域で再探索。
                try:
                    res = _bubble_point_flash(flasher, P, z,
                                              T_lo=250.0, T_hi=450.0,
                                              try_vf0=False)
                except Exception:
                    res = None
            if res is None:
                T_bp_history.append(None)
                vapor_history.append(None)
                break

        y = list(res.gas.zs)
        T_bp = res.T - 273.15
        _T_bp_prev_K = res.T

        # スチーム蒸留 T3 補正: UNIFAC が準安定 VL 解を返す場合に T3_steam で上書き
        # 条件: 端点以外（全組成が 0.01 < z < 0.99）かつ UNIFAC T_bp < T3_steam − 3°C
        if _T3_steam_override_K is not None:
            _all_interior = all(0.01 < zi < 0.99 for zi in z)
            if _all_interior and res.T < _T3_steam_override_K - 3.0:
                T_bp = _T3_steam_override_K - 273.15
                y = _y3_steam_override[:]
                _T_bp_prev_K = _T3_steam_override_K
                _steam_applied = True

        # 三相域検出: 前ステップと T_bp が 0.1 K 以内で一致し、かつ実際に 2 液相に
        # 分かれている場合のみ三相域とみなす（均一共沸点近傍の誤検出を防止）。
        # スチーム蒸留補正が掛かった点は UNIFAC が準安定解を返しているだけで
        # 不均一系であることは確定している（a_i > 1）ため液相数判定を省略する。
        if _T_three_phase_K is None:
            if len(T_bp_history) > 0 and T_bp_history[-1] is not None:
                if abs((T_bp + 273.15) - (T_bp_history[-1] + 273.15)) < 0.1:
                    _three_phase_streak += 1
                    if _three_phase_streak >= _THREE_PHASE_CONFIRM:
                        _T_hp_K = T_bp + 273.15
                        if _steam_applied or _is_two_liquid(flasher, P, z, _T_hp_K):
                            _T_three_phase_K = _T_hp_K
                            _y_cached = y
                            _three_phase_by_steam = _steam_applied
                        else:
                            _three_phase_streak = 0
                else:
                    _three_phase_streak = 0
            else:
                _three_phase_streak = 0

        T_bp_history.append(T_bp)
        vapor_history.append(y[:])

        if step < n_steps:
            for i in range(len(solvents)):
                L[i] = max(0.0, L[i] - dV * y[i])

    while len(T_bp_history) < len(evap_fractions):
        T_bp_history.append(None)
    while len(vapor_history) < len(evap_fractions):
        vapor_history.append(None)

    return {
        "evap_fraction": evap_fractions,
        "amounts": {solvents[i]["name"]: amounts_history[i]
                    for i in range(len(solvents))},
        "total": total_history,
        "T_bp": T_bp_history,
        "vapor_fracs": vapor_history,
    }
