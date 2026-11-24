"""解析文书行，区分权威依据与供方线索，并从签署文书提取结构化要求。

解析原则：
- 只把 role 为“签署原规/签署补函”的文书作为要求来源；
- 供应商译稿、测点说明不生成 Requirement，交由 translation 模块核对；
- 解析不了的句子登记到 parse_warnings，绝不猜测放行；
- 数值、单位、开闭、对象、时点、测点逐项落字段，出处保留文书 id 与原句。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .requirements import (
    Requirement,
    RequirementSet,
    UpperLimit,
)
from .units import canonical_stage, to_millimeters

AUTHORITATIVE_ROLES = {"签署原规", "签署补函"}

# 主线 A：左右端面分别，不超过（闭口），公称值加偏差，句末给出时点
_FACE_RE = re.compile(
    r"主线\s*(?P<station>[A-Z])\s*夹具的左右端面距分别不超过公称值加"
    r"(?P<delta>.+?)，(?P<stage>.+?)测"
)
# 备线 B：两端最大差，严格小于（开口），绝对数值
_SPREAD_RE = re.compile(
    r"备线\s*(?P<station>[A-Z])\s*按两端最大差严格小于\s*"
    r"(?P<value>[0-9.]+)\s*(?P<unit>mm|um|µm)，(?P<stage>.+?)测"
)
# 补函：A 线上限调整
_AMEND_LIMIT_RE = re.compile(
    r"主线\s*(?P<station>[A-Z])\s*上限从加\s*(?P<old>[0-9.]+)\s*(?P<old_unit>mm|um|µm)"
    r"\s*调至加\s*(?P<new>[0-9.]+)\s*(?P<new_unit>mm|um|µm)"
)
# 补函：已装机夹具的例外，保留原限、更换测点与时点
_MOUNT_EXCEPTION_RE = re.compile(
    r"已装到\s*(?P<station>[A-Z])\s*台的仍按\s*(?:[A-Z]\s*)?原限，"
    r"仅改为(?P<stage>.+?)从测点\s*(?P<point>P[0-9]+)\s*取两端最大差"
)

# 中文数量词：当前资料出现“半毫米”，其余小数直接按阿拉伯数字处理
_CN_NUMBER = {"半": 0.5, "一": 1.0, "两": 2.0, "二": 2.0}
# 单位写法包含中文“毫米”；换算统一委托 units.to_millimeters
_QUANTITY_RE = re.compile(r"([0-9]+\.?[0-9]*|半|一|两|二)\s*(mm|um|µm|毫米)")
_CN_UNIT = {"毫米": "mm"}


@dataclass(frozen=True)
class SpecDocument:
    id: str
    lang: str
    role: str
    issued: str
    amends: str
    text: str

    @property
    def authoritative(self) -> bool:
        return self.role in AUTHORITATIVE_ROLES


def parse_quantity(phrase: str) -> float:
    """把“半毫米 / 0.40 mm”之类的短语换算为毫米。"""
    match = _QUANTITY_RE.search(phrase)
    if not match:
        raise ValueError(f"无法从短语中识别数值单位: {phrase}")
    number_text, unit = match.groups()
    number = _CN_NUMBER.get(number_text)
    if number is None:
        number = float(number_text)
    return to_millimeters(number, _CN_UNIT.get(unit, unit))


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"。", text) if part.strip()]


def parse_specs(rows: list[dict]) -> tuple[dict[str, RequirementSet], dict[str, SpecDocument], list[str]]:
    """按出具顺序解析文书。

    返回 ({工位: RequirementSet}, 文书登记表, 跨工位解析告警)。
    """
    documents: dict[str, SpecDocument] = {}
    sets: dict[str, RequirementSet] = {}
    warnings: list[str] = []

    def requirement_set(station: str) -> RequirementSet:
        if station not in sets:
            sets[station] = RequirementSet()
        return sets[station]

    for row in rows:  # 资料按出具日排列；同日内以 id 稳定排序
        doc = SpecDocument(
            id=row["id"],
            lang=row["lang"],
            role=row["role"],
            issued=row["issued"],
            amends=row["amends"],
            text=row["text"],
        )
        documents[doc.id] = doc
        if not doc.authoritative:
            continue

        for sentence in _split_sentences(doc.text):
            face_match = _FACE_RE.search(sentence)
            if face_match:
                station = face_match.group("station")
                delta_mm = parse_quantity(face_match.group("delta"))
                stage = canonical_stage(face_match.group("stage"))
                target = requirement_set(station)
                for face_label in ("左", "右"):
                    target.active.append(
                        Requirement(
                            station=station,
                            object_kind="face",
                            face_label=face_label,
                            point=None,
                            stage=stage,
                            limit=UpperLimit(delta_mm=delta_mm, kind="nominal_plus", inclusive=True),
                            source_id=doc.id,
                            source_sentence=sentence,
                            introduced_by=doc.id,
                        )
                    )
                continue

            spread_match = _SPREAD_RE.search(sentence)
            if spread_match:
                station = spread_match.group("station")
                value_mm = to_millimeters(
                    float(spread_match.group("value")), spread_match.group("unit")
                )
                stage = canonical_stage(spread_match.group("stage"))
                requirement_set(station).active.append(
                    Requirement(
                        station=station,
                        object_kind="spread",
                        face_label=None,
                        point=None,
                        stage=stage,
                        limit=UpperLimit(delta_mm=value_mm, kind="absolute", inclusive=False),
                        source_id=doc.id,
                        source_sentence=sentence,
                        introduced_by=doc.id,
                    )
                )
                continue

            amend_match = _AMEND_LIMIT_RE.search(sentence)
            if amend_match:
                station = amend_match.group("station")
                new_delta_mm = to_millimeters(
                    float(amend_match.group("new")), amend_match.group("new_unit")
                )
                target = requirement_set(station)
                # 同工位同对象的现行要求转入历史，保留原出处作为历史依据
                moving = [req for req in target.active if req.station == station]
                target.superseded.extend(moving)
                target.active = [req for req in target.active if req.station != station]
                for face_label in ("左", "右"):
                    target.active.append(
                        Requirement(
                            station=station,
                            object_kind="face",
                            face_label=face_label,
                            point=None,
                            stage=moving[0].stage,
                            limit=UpperLimit(delta_mm=new_delta_mm, kind="nominal_plus", inclusive=True),
                            source_id=doc.id,
                            source_sentence=sentence,
                            introduced_by=doc.id,
                        )
                    )
                continue

            exception_match = _MOUNT_EXCEPTION_RE.search(sentence)
            if exception_match:
                station = exception_match.group("station")
                stage = canonical_stage(exception_match.group("stage"))
                point = exception_match.group("point")
                target = requirement_set(station)
                original = next(
                    (req for req in [*target.active, *target.superseded]
                     if req.station == station and req.object_kind == "spread"),
                    None,
                )
                if original is None:
                    target.parse_warnings.append(
                        f"{doc.id} 的装机例外找不到 {station} 台原限: {sentence}"
                    )
                    continue
                # 原限照旧（开口 0.7 mm），仅更换时点与测点；装机前要求继续保留给未装机夹具
                target.active.append(
                    Requirement(
                        station=station,
                        object_kind="spread",
                        face_label=None,
                        point=point,
                        stage=stage,
                        limit=original.limit,
                        source_id=doc.id,
                        source_sentence=sentence,
                        introduced_by=doc.id,
                    )
                )
                continue

            warnings.append(f"{doc.id} 存在未识别句式: {sentence}")

    return sets, documents, warnings


# 未归位句式统一进入返回的 warnings，不制造伪工位 RequirementSet
