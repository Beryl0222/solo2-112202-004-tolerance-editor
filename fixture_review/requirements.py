"""规格要求的数据模型。

一条 Requirement 对应规范原文中的一个可判定句子，保留：
- 适用工位、测量对象（逐端面 / 两端最大差 / 指定测点）
- 上下限及其开闭（严格小于必须保留为开口）
- 相对公称值的偏差
- 测量时点、测点以及出处文书与原句
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 规范时点（与 units.canonical_stage 的取值对齐）
STAGE_PRE_DEBURR = "去毛刺前"
STAGE_POST_DEBURR = "去毛刺后"
STAGE_PRE_MOUNT = "装机前"
STAGE_POST_MOUNT = "装机后"


@dataclass(frozen=True)
class UpperLimit:
    """相对公称值的单侧上限。

    kind:
      "nominal_plus" 表示上限 = 公称值 + delta_mm；
      "absolute"     表示上限为绝对数值（公称值视为 0，如两端差）。
    inclusive=False 表示原文为“严格小于/不大于以外的开口”要求。
    """

    delta_mm: float
    kind: str = "nominal_plus"
    inclusive: bool = True

    def bound_mm(self, nominal_mm: float) -> float:
        base = nominal_mm if self.kind == "nominal_plus" else 0.0
        return round(base + self.delta_mm, 6)


@dataclass(frozen=True)
class Requirement:
    """单个工位的单个可判定要求。"""

    station: str
    object_kind: str          # "face"（逐端面） | "spread"（两端最大差）
    face_label: str | None    # object_kind == "face" 时为左/右等端面名
    point: str | None         # 指定测点（如 P2），无指定为 None
    stage: str
    limit: UpperLimit
    source_id: str            # 出处文书 id
    source_sentence: str      # 文书原句
    introduced_by: str        # 首次提出该要求的文书 id
    serial_scope: tuple[str, ...] = ()  # 例外条款限定的制造编号，空表示不按编号限定


@dataclass
class RequirementSet:
    """一个工位当前有效要求 + 被修订的历史要求（用于历史依据展示与重审）。"""

    active: list[Requirement] = field(default_factory=list)
    superseded: list[Requirement] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
