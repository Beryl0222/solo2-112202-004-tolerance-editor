"""计量单位与对称不确定区间。

换算只服务于数值比较；记录原始单位、原始读数和原文条件必须原样保留，
不能因为换算后数值相等就把不同对象或不同时点的测量合并。
"""
from __future__ import annotations

from dataclasses import dataclass

# 统一内部比较基准为毫米
_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "um": 0.001,
    "µm": 0.001,
}

_STAGE_ALIASES: dict[str, str] = {
    # 中文规范写法
    "去毛刺前": "去毛刺前",
    "去毛刺后": "去毛刺后",
    "装机前": "装机前",
    "装机后": "装机后",
    # 计量单/译稿可能出现的英文写法
    "before deburring": "去毛刺前",
    "after deburring": "去毛刺后",
    "before mounting": "装机前",
    "after mounting": "装机后",
}


class UnitError(ValueError):
    """出现资料中未登记的计量单位。"""


def to_millimeters(value: float, unit: str) -> float:
    """把给定单位的数值换算为毫米，结果保留 6 位小数避免浮点尾差。"""
    try:
        factor = _TO_MM[unit]
    except KeyError as exc:
        raise UnitError(f"未登记的计量单位: {unit}") from exc
    return round(value * factor, 6)


def canonical_stage(stage: str) -> str:
    """把计量单来源写法归并为规范时点；未登记写法原样返回并标记为待解释。"""
    text = stage.strip()
    return _STAGE_ALIASES.get(text, text)


@dataclass(frozen=True)
class Interval:
    """由读数与对称半区间得到的可能区间，单位统一为毫米。"""

    lower: float
    upper: float
    reading_mm: float
    uncertainty_mm: float | None

    @classmethod
    def from_record(cls, reading: float, uncertainty: float | None, unit: str) -> "Interval":
        reading_mm = to_millimeters(reading, unit)
        if uncertainty is None or uncertainty == "":
            half = None
            lower = upper = reading_mm
        else:
            half = to_millimeters(float(uncertainty), unit)
            lower = round(reading_mm - half, 6)
            upper = round(reading_mm + half, 6)
        return cls(lower=lower, upper=upper, reading_mm=reading_mm, uncertainty_mm=half)
