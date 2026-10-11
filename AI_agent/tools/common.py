"""ツール共通部品: 登録デコレータ・溶媒名解決・単位変換・機器DB"""

from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solvents import ALL_SOLVENTS, IMMISCIBLE_SOLVENTS, MISCIBLE_SOLVENTS  # noqa: E402


class ToolError(ValueError):
    """ユーザー入力に起因するエラー（LLM にそのまま返して再質問・修正させる）。"""


# ---------------------------------------------------------------------------
# ツール登録
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., dict]

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, properties: dict, required: list[str] | None = None):
    """func(ws, **kwargs) -> dict をツールとして登録する。"""
    params = {"type": "object", "properties": properties, "required": required or []}

    def deco(func):
        REGISTRY[name] = Tool(name, description, params, func)
        return func
    return deco


# JSON Schema 部品
NUM = {"type": "number"}
INT = {"type": "integer"}
STR = {"type": "string"}


def num(desc: str) -> dict:
    return {"type": "number", "description": desc}


def string(desc: str, enum: list[str] | None = None) -> dict:
    d = {"type": "string", "description": desc}
    if enum:
        d["enum"] = enum
    return d


SOLVENT_AMOUNTS = {
    "type": "array",
    "description": "成分と量のリスト。name は溶媒名（日本語・略称可）",
    "items": {
        "type": "object",
        "properties": {"name": STR, "amount": NUM},
        "required": ["name", "amount"],
    },
}
AMOUNT_UNIT = string("量の単位", ["mol", "g", "kg", "mL", "L"])


# ---------------------------------------------------------------------------
# 溶媒名解決
# ---------------------------------------------------------------------------

_ALIASES = {
    "水": "Water", "h2o": "Water",
    "エタノール": "Ethanol", "etoh": "Ethanol",
    "メタノール": "Methanol", "meoh": "Methanol",
    "アセトン": "Acetone",
    "アセトニトリル": "Acetonitrile", "mecn": "Acetonitrile", "acn": "Acetonitrile",
    "ipa": "2-Propanol (IPA)", "イソプロパノール": "2-Propanol (IPA)",
    "2-プロパノール": "2-Propanol (IPA)", "イソプロピルアルコール": "2-Propanol (IPA)",
    "isopropanol": "2-Propanol (IPA)", "2-propanol": "2-Propanol (IPA)",
    "1-プロパノール": "1-Propanol", "テトラヒドロフラン": "THF",
    "dmf": "DMF", "ジメチルホルムアミド": "DMF",
    "エチレングリコール": "Ethylene glycol", "ジオキサン": "1,4-Dioxane",
    "酢酸": "Acetic Acid", "acoh": "Acetic Acid",
    "dmac": "N,N-Dimethylacetamide", "dma": "N,N-Dimethylacetamide",
    "dmso": "Dimethyl Sulfoxide", "ジメチルスルホキシド": "Dimethyl Sulfoxide",
    "ギ酸": "Formic Acid", "ホルムアミド": "Formamide",
    "酢酸メチル": "Methyl Acetate", "mek": "Methyl Ethyl Ketone",
    "メチルエチルケトン": "Methyl Ethyl Ketone",
    "nmp": "n-Methyl-2-Pyrrolidone", "ピリジン": "Pyridine", "スルホラン": "Sulfolane",
    "tbuoh": "t-Butyl Alcohol", "tert-butanol": "t-Butyl Alcohol",
    "dme": "Ethylene Glycol Dimethyl Ether", "dmi": "1,3-Dimethylimidazolidin-2-One",
    "トルエン": "Toluene", "ヘキサン": "n-Hexane", "n-ヘキサン": "n-Hexane", "hexane": "n-Hexane",
    "ヘプタン": "n-Heptane", "n-ヘプタン": "n-Heptane", "heptane": "n-Heptane",
    "シクロヘキサン": "Cyclohexane", "ベンゼン": "Benzene", "クロロホルム": "Chloroform",
    "ジエチルエーテル": "Diethyl ether", "エーテル": "Diethyl ether",
    "酢酸エチル": "Ethyl acetate", "etoac": "Ethyl acetate", "acoet": "Ethyl acetate",
    "ジクロロメタン": "Dichloromethane", "dcm": "Dichloromethane", "塩化メチレン": "Dichloromethane",
    "mibk": "Methyl isobutyl ketone", "メチルイソブチルケトン": "Methyl isobutyl ketone",
    "酢酸ブチル": "n-Butyl acetate", "酢酸n-ブチル": "n-Butyl acetate",
    "イソオクタン": "Isooctane", "アニソール": "Anisole",
    "1-ブタノール": "1-Butanol", "ブタノール": "1-Butanol", "n-ブタノール": "1-Butanol",
    "2-ブタノール": "2-Butanol", "クロロベンゼン": "Chlorobenzene",
    "ギ酸エチル": "Ethyl Formate", "酢酸イソブチル": "Isobutyl Acetate",
    "イソブタノール": "Isobutyl Alcohol", "酢酸イソプロピル": "Isopropyl Acetate",
    "ipac": "Isopropyl Acetate", "メチルシクロヘキサン": "Methylcyclohexane",
    "mtbe": "Methyl tert-Butyl Ether", "ニトロメタン": "Nitromethane",
    "ペンタン": "Pentane", "1-ペンタノール": "1-Pentanol", "酢酸プロピル": "n-Propyl Acetate",
    "テトラリン": "Tetrahydronaphthalene", "トリクロロエチレン": "Trichloroethylene",
    "トリエチルアミン": "Triethylamine", "tea": "Triethylamine", "et3n": "Triethylamine",
    "キシレン": "Xylene (o-)", "o-キシレン": "Xylene (o-)", "xylene": "Xylene (o-)",
    "イソアミルアルコール": "Isoamyl Alcohol", "クメン": "Isopropylbenzene (Cumene)",
    "2-methf": "2-Methyltetrahydrofuran", "2-メチルテトラヒドロフラン": "2-Methyltetrahydrofuran",
    "cpme": "Cyclopentyl Methyl Ether", "シクロペンチルメチルエーテル": "Cyclopentyl Methyl Ether",
    "4-methp": "4-Methyltetrahydropyran", "4-メチルテトラヒドロピラン": "4-Methyltetrahydropyran",
}

_NORM_RE = re.compile(r"[\s_\-()（）]")


def _norm(s: str) -> str:
    return _NORM_RE.sub("", s).lower()


def _build_index() -> dict[str, dict]:
    idx: dict[str, dict] = {}
    by_name = {s["name"]: s for s in ALL_SOLVENTS}
    for s in ALL_SOLVENTS:
        idx[_norm(s["name"])] = s
        idx[_norm(s["thermo_id"])] = s
    for alias, name in _ALIASES.items():
        idx[_norm(alias)] = by_name[name]
    return idx


_INDEX = _build_index()


def resolve_solvent(name: str) -> dict:
    key = _norm(str(name))
    if key in _INDEX:
        return _INDEX[key]
    cands = [s["name"] for k, s in _INDEX.items() if key and (key in k or k in key)]
    cands = list(dict.fromkeys(cands))
    if len(cands) == 1:
        return next(s for s in ALL_SOLVENTS if s["name"] == cands[0])
    hint = f" 候補: {', '.join(cands[:8])}" if cands else " list_solvents で対応溶媒を確認してください。"
    raise ToolError(f"溶媒 '{name}' はデータベースにありません。{hint}")


def solvent_category(s: dict) -> str:
    if s["name"] == "Water":
        return "水"
    if s in MISCIBLE_SOLVENTS:
        return "水溶性"
    if s in IMMISCIBLE_SOLVENTS:
        return "非水溶性"
    return "-"


# ---------------------------------------------------------------------------
# 密度・単位変換
# ---------------------------------------------------------------------------

def liquid_density(s: dict, T_C: float) -> float:
    from engine import density_solvent, density_water
    return density_water(T_C) if s["name"] == "Water" else density_solvent(s, T_C)


def to_moles(s: dict, amount: float, unit: str, T_C: float = 25.0) -> float:
    if amount < 0:
        raise ToolError(f"{s['name']} の量が負です: {amount}")
    unit = unit.strip()
    if unit == "mol":
        return amount
    if unit == "g":
        return amount / s["mw"]
    if unit == "kg":
        return amount * 1000.0 / s["mw"]
    if unit == "mL":
        return amount * liquid_density(s, T_C) / s["mw"]
    if unit == "L":
        return amount * 1000.0 * liquid_density(s, T_C) / s["mw"]
    raise ToolError(f"未対応の単位: {unit}（mol / g / kg / mL / L）")


def parse_solvent_amounts(items: list[dict], unit: str, T_C: float,
                          n_min: int = 1, n_max: int = 4) -> tuple[list[dict], list[float]]:
    if not items:
        raise ToolError("成分が指定されていません。")
    sols, moles = [], []
    for it in items:
        s = resolve_solvent(it["name"])
        if s in sols:
            raise ToolError(f"成分 {s['name']} が重複しています。")
        sols.append(s)
        moles.append(to_moles(s, float(it["amount"]), unit, T_C))
    if not (n_min <= len(sols) <= n_max):
        raise ToolError(f"成分数は {n_min}〜{n_max} にしてください（指定: {len(sols)}）。")
    if sum(moles) <= 0:
        raise ToolError("合計量が 0 です。")
    return sols, moles


def mix_volume_L(sols: list[dict], moles: list[float], T_C: float) -> float:
    return sum(m * s["mw"] / liquid_density(s, T_C) for s, m in zip(sols, moles)) / 1000.0


# ---------------------------------------------------------------------------
# 機器DB
# ---------------------------------------------------------------------------

def equipment_repo():
    from heat_transfer.src.equipment_repo import get_equipment_repo
    return get_equipment_repo()


def get_reactor(tag_no: str | None = None, manual: dict | None = None):
    """Tag No. か手動仕様（U_kJ_m2hK, volume_L, diameter_m, mirror_type）で ReactorSpec を返す。"""
    from heat_transfer.src.models import ReactorSpec
    if tag_no:
        try:
            return equipment_repo().get_reactor_spec(str(tag_no).strip())
        except ValueError as e:
            raise ToolError(f"{e} list_equipment で登録機器を確認してください。") from e
    if manual:
        try:
            return ReactorSpec(
                tag_no=manual.get("tag_no", "手動入力"),
                U=float(manual["U_kJ_m2hK"]) / 3.6,
                volume_L=float(manual["volume_L"]),
                diameter_m=float(manual["diameter_m"]),
                mirror_type=str(manual.get("mirror_type", "ED")).upper(),
            )
        except KeyError as e:
            raise ToolError(f"反応槽の手動仕様に {e} がありません。") from e
    raise ToolError("反応槽を reactor_tag（Tag No.）または reactor_manual で指定してください。")


REACTOR_MANUAL = {
    "type": "object",
    "description": "DB にない反応槽を使う場合の仕様（reactor_tag と排他）",
    "properties": {
        "U_kJ_m2hK": num("総括伝熱係数 U [kJ/(m²·h·K)]"),
        "volume_L": num("機器容量 [L]"),
        "diameter_m": num("内径 [m]"),
        "mirror_type": string("鏡形状", ["ED", "SD"]),
    },
    "required": ["U_kJ_m2hK", "volume_L", "diameter_m"],
}


def get_filter_area(tag_no: str) -> tuple[float, str]:
    try:
        spec = equipment_repo().get_filter_spec(str(tag_no).strip())
    except ValueError as e:
        raise ToolError(f"{e} list_equipment で登録機器を確認してください。") from e
    return spec.area_m2, spec.filter_type


# ---------------------------------------------------------------------------
# 数値整形
# ---------------------------------------------------------------------------

def r(x: Any, sig: int = 4) -> Any:
    """JSON 出力用に有効数字を丸める（None / 非数はそのまま）。"""
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return x
    if not math.isfinite(xf):
        return None
    if xf == 0:
        return 0.0
    return float(f"{xf:.{sig}g}")


def interp(x_target: float, xs: list[float], ys: list[float]) -> float | None:
    """単調増加 xs に対する線形補間（範囲外は None）。"""
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x_target <= x1:
            if x1 == x0:
                return y0
            return y0 + (y1 - y0) * (x_target - x0) / (x1 - x0)
    return None


def fmt_min(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h}時間{m:02d}分" if h else f"{m}分"
