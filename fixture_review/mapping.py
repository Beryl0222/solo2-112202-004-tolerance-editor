"""测点名映射与复校驳回。

资料关系：
- 供应商测点说明（如 C4）只提供“供方把某测点名写成哪个真实测点”的线索，
  未经质量工程师确认不得作为合并依据；
- gauge_sources 的复校记录可以直接驳回映射（S4：D 不对应 P2）；
- 质量工程师可以确认指定测点与适用修订，但确认不替代任何缺少的测量；
- 已确认映射若被后续复校驳回：同一报告内其他可确认测点仍可用，
  依赖该映射的合并报告必须拆回复测（由 engine 处理）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .spec_parser import SpecDocument

_C4_RE = re.compile(
    r"For\s+serial\s+(?P<serial>[A-Za-z0-9]+),\s*mark\s+(?P<label>.+?)\s+"
    r"corresponds to\s+(?P<point>P[0-9]+)"
)
_C4_DIFFERENT_RE = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z0-9 ]*?)\s+is a different surface on\s+(?P<serial>[A-Za-z0-9]+)"
)
_REJECT_RE = re.compile(r"确认(?P<label>.+?)不对应\s*(?P<point>P[0-9]+)")
_UNCONFIRMED_RE = re.compile(r"尚未确认\s*(?P<label>.+?)\s*与\s*(?P<point>P[0-9]+)\s*同基准")


def normalize_label(label: str) -> str:
    """归一测点名写法。

    资料中同一测点存在“Rear D / D / 该 D”等写法：去掉指示代词与方位前缀，
    保留末段字母编号作为登记键；原始写法仍保留在证据与展示中。
    """
    text = label.strip().lstrip("该").strip()
    parts = text.split()
    return parts[-1] if parts else text


class MappingStatus(str, Enum):
    UNCONFIRMED = "未确认"
    CONFIRMED = "已确认"
    REJECTED = "复校驳回"


@dataclass(frozen=True)
class PointMapping:
    serial: str
    label: str             # 计量单上的测点名，如 “Rear D”
    point: str             # 真实测点，如 P2
    status: MappingStatus
    evidence: tuple[str, ...]            # 出处记录 id
    applicable_revision: str | None = None  # 工程师确认时登记的适用修订文书 id


@dataclass
class MappingRegistry:
    mappings: dict[tuple[str, str, str], PointMapping] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def _key(self, serial: str, label: str, point: str) -> tuple[str, str, str]:
        return serial, normalize_label(label), point.strip()

    def add_claim(self, mapping: PointMapping) -> None:
        key = self._key(mapping.serial, mapping.label, mapping.point)
        existing = self.mappings.get(key)
        # 驳回状态优先级最高，供方重复来文不能翻案
        if existing and existing.status is MappingStatus.REJECTED:
            return
        self.mappings[key] = mapping

    def confirm(
        self, serial: str, label: str, point: str, revision_id: str, evidence: str
    ) -> PointMapping:
        """质量工程师确认映射及适用修订。

        只允许确认已有供方线索的映射；确认测点不等于补上测量。
        """
        key = self._key(serial, label, point)
        if key not in self.mappings:
            raise KeyError(f"没有可供确认的供方测点线索: {serial} {label} -> {point}")
        current = self.mappings[key]
        if current.status is MappingStatus.REJECTED:
            raise ValueError(f"映射已被复校驳回，不能确认: {key}")
        confirmed = PointMapping(
            serial=serial,
            label=current.label,
            point=current.point,
            status=MappingStatus.CONFIRMED,
            evidence=(*current.evidence, evidence),
            applicable_revision=revision_id,
        )
        self.mappings[key] = confirmed
        return confirmed

    def reject(self, serial: str, label: str, point: str, evidence: str) -> PointMapping:
        key = self._key(serial, label, point)
        current = self.mappings.get(key)
        rejected = PointMapping(
            serial=serial,
            label=normalize_label(label),
            point=point.strip(),
            status=MappingStatus.REJECTED,
            evidence=(*(current.evidence if current else ()), evidence),
            applicable_revision=current.applicable_revision if current else None,
        )
        self.mappings[key] = rejected
        return rejected

    def resolve(self, serial: str, label: str, point: str) -> PointMapping | None:
        return self.mappings.get(self._key(serial, label, point))

    def status_for(self, serial: str, label: str) -> PointMapping | None:
        normalized = normalize_label(label)
        matches = [
            mapping for mapping in self.mappings.values()
            if mapping.serial == serial and mapping.label == normalized
        ]
        if not matches:
            return None
        # 同一标签存在多条时，驳回优先
        if any(mapping.status is MappingStatus.REJECTED for mapping in matches):
            return next(mapping for mapping in matches if mapping.status is MappingStatus.REJECTED)
        if any(mapping.status is MappingStatus.CONFIRMED for mapping in matches):
            return next(mapping for mapping in matches if mapping.status is MappingStatus.CONFIRMED)
        return matches[0]


def build_registry(
    spec_docs: dict[str, SpecDocument],
    gauge_sources: list[dict],
    gauges: list[dict],
) -> MappingRegistry:
    """从供方测点说明与复校记录构造初始映射登记。"""
    registry = MappingRegistry()

    serial_by_gauge = {row["id"]: row["serial"] for row in gauges}

    # 1) 供方线索：仅登记为“未确认”
    for doc in spec_docs.values():
        if doc.role != "供应商测点说明":
            continue
        for match in _C4_RE.finditer(doc.text):
            registry.add_claim(
                PointMapping(
                    serial=match.group("serial"),
                    label=normalize_label(match.group("label")),
                    point=match.group("point"),
                    status=MappingStatus.UNCONFIRMED,
                    evidence=(doc.id,),
                )
            )
        for match in _C4_DIFFERENT_RE.finditer(doc.text):
            label = normalize_label(match.group("label"))
            serial = match.group("serial")
            # “D is a different surface on V3”：供方自己声明不是同一面，
            # 若存在指向 P2 的线索则直接驳回
            for point in {m.point for m in registry.mappings.values() if m.serial == serial}:
                registry.reject(serial, label, point, doc.id)

    # 2) 复校记录
    for row in gauge_sources:
        log = row["log"]
        serial = serial_by_gauge.get(row["gauge"], "")
        reject_match = _REJECT_RE.search(log)
        if reject_match:
            registry.reject(
                serial,
                normalize_label(reject_match.group("label")),
                reject_match.group("point"),
                row["id"],
            )
            continue
        unconfirmed_match = _UNCONFIRMED_RE.search(log)
        if unconfirmed_match:
            label = normalize_label(unconfirmed_match.group("label"))
            point = unconfirmed_match.group("point")
            key = registry._key(serial, label, point)
            if key not in registry.mappings:
                registry.add_claim(
                    PointMapping(
                        serial=serial,
                        label=label,
                        point=point,
                        status=MappingStatus.UNCONFIRMED,
                        evidence=(row["id"],),
                    )
                )
            # 已存在则维持原状态（未确认），证据由登记处保留
    return registry
