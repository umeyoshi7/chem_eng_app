from thermo import ChemicalConstantsPackage, GibbsExcessLiquid, FlashVLN, CEOSGas, PRMIX
from thermo.unifac import UNIFAC, DOUFSG, DOUFIP2016
import streamlit as st
import concurrent.futures as _cf

# VF=0 タイムアウト用スレッドプール（LL系で VF=0 が 4s かかる問題を回避）
_vf0_executor = _cf.ThreadPoolExecutor(max_workers=4)


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


@st.cache_resource
def build_flasher(T: float, thermo_id_1: str, thermo_id_2: str,
                  surrogate_1: str = None, surrogate_2: str = None,
                  unifac_override_1: tuple = None, unifac_override_2: tuple = None):
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
    GE = UNIFAC.from_subgroups(
        chemgroups=constants.UNIFAC_Dortmund_groups,
        version=1,
        T=T, xs=[1/3]*3,
        interaction_data=DOUFIP2016,
        subgroups=DOUFSG,
    )
    liquid = GibbsExcessLiquid(
        VaporPressures=correlations.VaporPressures,
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
def build_flasher_general(thermo_ids: tuple, unifac_overrides: tuple = ()):
    """可変成分数 (2〜4) の FlashVLN を構築・キャッシュする。

    thermo_ids: surrogateを適用済みの thermo ID タプル
    unifac_overrides: ((idx, items_tuple), ...) UNIFAC グループ上書き用
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
    GE = UNIFAC.from_subgroups(
        chemgroups=constants.UNIFAC_Dortmund_groups,
        version=1,
        T=T0, xs=zs0,
        interaction_data=DOUFIP2016,
        subgroups=DOUFSG,
    )
    liquid = GibbsExcessLiquid(
        VaporPressures=correlations.VaporPressures,
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
    return thermo_ids, unifac_overrides


def _bubble_point_flash(flasher, P: float, zs: list,
                        T_lo: float = 250.0, T_hi: float = 450.0,
                        n_bisect: int = 5, try_vf0: bool = True):
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

    # --- 二分探索（n_bisect 回、デフォルト 10 回 ≈ 0.04°C 精度 @ 40K 幅）---
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


def calc_vapor_pressure_curve(thermo_id: str, T_min_C: float = 0.0,
                               T_max_C: float = 150.0, n: int = 200):
    """蒸気圧曲線を計算する。

    Returns: {"T_C": list, "P_kPa": list, "T_bp_C": float or None}
    T_bp_C: 101.325 kPa での沸点（温度範囲外なら None）
    """
    _, props = ChemicalConstantsPackage.from_IDs([thermo_id])
    vp = props.VaporPressures[0]
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

    return {"T_C": temps, "P_kPa": pressures, "T_bp_C": T_bp}


def calc_vle_xy(solvents: list, P_kPa: float, n: int = 80):
    """2成分系 VLE xy 線図・T-xy 線図用データを計算する。

    solvents: 2成分の solvent dict リスト
    Returns: {"x1": list, "y1": list, "T_bubble_C": list, "T_dew_C": list}
    """
    thermo_ids, unifac_overrides = _solvents_to_flasher_args(solvents)
    flasher = build_flasher_general(thermo_ids, unifac_overrides)
    P = P_kPa * 1000.0

    x1_list, y1_list, T_b_list, T_d_list = [], [], [], []
    for i in range(n + 1):
        z1 = i / n
        z = [z1, 1.0 - z1]
        y1, T_b, T_d = None, None, None
        try:
            res_b = _bubble_point_flash(flasher, P, z)
            y1 = res_b.gas.zs[0] if res_b.gas else None
            T_b = res_b.T - 273.15
        except Exception:
            pass
        try:
            res_d = flasher.flash(P=P, VF=1, zs=z)
            T_d = res_d.T - 273.15
        except Exception:
            pass
        x1_list.append(z1)
        y1_list.append(y1)
        T_b_list.append(T_b)
        T_d_list.append(T_d)

    return {"x1": x1_list, "y1": y1_list,
            "T_bubble_C": T_b_list, "T_dew_C": T_d_list}


def calc_rayleigh_distillation(solvents: list, initial_moles: list,
                                P_kPa: float, n_steps: int = 50):
    """レイリー蒸留（微分ステップ法）シミュレーション。

    solvents: solvent dict リスト (2〜4成分)
    initial_moles: 各成分の初期モル数
    Returns: {"evap_fraction": list, "amounts": {name: list[mol]},
              "total": list[mol], "T_bp": list[°C or None]}
    """
    thermo_ids, unifac_overrides = _solvents_to_flasher_args(solvents)
    flasher = build_flasher_general(thermo_ids, unifac_overrides)
    P = P_kPa * 1000.0

    L = [float(m) for m in initial_moles]
    total_initial = sum(L)
    if total_initial <= 0:
        return {"evap_fraction": [],
                "amounts": {s["name"]: [] for s in solvents},
                "total": [], "T_bp": []}

    dV = total_initial / n_steps
    evap_fractions, total_history, T_bp_history = [], [], []
    amounts_history = [[] for _ in range(len(solvents))]
    _T_bp_prev_K = None   # ウォームスタート用: 前ステップの泡点 (K)
    _vf0_known_fail = False  # True になると VF=0 をスキップして二分探索へ直行
    _probe_fail_streak = 0   # プローブが連続失敗した回数
    _PROBE_SKIP_AFTER = 3    # 3回連続失敗 → プローブをスキップして二分探索に直行
    _y_cached = None          # 三相域キャッシュ: 気相組成（T一定 → y一定）
    _T_three_phase_K = None   # 三相温度 (K) 。None = 三相域未確定
    _three_phase_streak = 0   # 連続して同温度だったステップ数
    _THREE_PHASE_CONFIRM = 2  # この回数連続同温度で「三相域確定」とみなす

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
            if not _depleting:
                # 三相域のまま → キャッシュ使用
                T_bp_history.append(_T_three_phase_K - 273.15)
                if step < n_steps:
                    for i in range(len(solvents)):
                        L[i] = max(0.0, L[i] - dV * _y_cached[i])
                continue   # flash 不要 → 次のステップへ
            else:
                # 枯渇を検知 → キャッシュ無効化して通常パスへ
                _T_three_phase_K = None
                _y_cached = None
                _three_phase_streak = 0

        res = None
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
                T_bp_history.append(None)
                break

        y = list(res.gas.zs)
        T_bp = res.T - 273.15
        _T_bp_prev_K = res.T

        # 三相域検出: 前ステップと T_bp が 0.05°C 以内で一致したらカウント
        if _T_three_phase_K is None:
            if len(T_bp_history) > 0 and T_bp_history[-1] is not None:
                if abs(res.T - (T_bp_history[-1] + 273.15)) < 0.1:
                    _three_phase_streak += 1
                    if _three_phase_streak >= _THREE_PHASE_CONFIRM:
                        _T_three_phase_K = res.T
                        _y_cached = y
                else:
                    _three_phase_streak = 0
            else:
                _three_phase_streak = 0

        T_bp_history.append(T_bp)

        if step < n_steps:
            for i in range(len(solvents)):
                L[i] = max(0.0, L[i] - dV * y[i])

    while len(T_bp_history) < len(evap_fractions):
        T_bp_history.append(None)

    return {
        "evap_fraction": evap_fractions,
        "amounts": {solvents[i]["name"]: amounts_history[i]
                    for i in range(len(solvents))},
        "total": total_history,
        "T_bp": T_bp_history,
    }
