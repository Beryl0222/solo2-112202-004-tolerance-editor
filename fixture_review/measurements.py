"""计量记录与单条测量判定。

判定规则（对应业务约定）：
- 读数带对称半区间时，整个可能区间都在工位要求内才自动认可；
  整个区间都超出则明确拒绝；区间跨过开闭边界则要求复测；
- 无不确定区间时，开口上限的边界值（读数恰等于极限）不能自动放行，转复测；
- 计量单位先换算为毫米再比较，原始单位与原始读数随记录保留；
- 测量时点、测量面/测点不匹配的记录不能用于该要求；
- 两张测单合用的前提是计量基准与最近一次装夹标识都相同；
- 映射未确认或被驳回的测点不能冒充要求测点。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .mapping import MappingRegistry, MappingStatus
from .requirements import Requirement
from .units import Interval, canonical_stage


class GaugeOutcome(str, Enum):
    PASS = "自动认可"
    REJECT = "明确拒绝"
    RETEST = "要求复测"
    NOT_APPLICABLE = "不适用于该要求"


@dataclass(frozen=True)
class GaugeRecord:
    id: str
    serial: str
    station: str
    face_raw: str
    stage_raw: str
    stage: str
    unit: str
    nominal_mm: float
    interval: Interval

    @property
    def is_spread_sheet(self) -> bool:
        return "差" in self.face_raw


@dataclass(frozen=True)
class SourceMark:
    """gauge_sources 声明的计量基准与装夹标识；缺失即无法证明合用条件。"""

    gauge: str
    datum: str
    fixture: str
    log: str
    source_id: str


@dataclass
class MeasurementVerdict:
    gauge: GaugeRecord
    requirement: Requirement | None
    outcome: GaugeOutcome
    bound_mm: float | None = None
    reasons: list[str] = field(default_factory=list)
    mapping_status: MappingStatus | None = None
    source_mark: SourceMark | None = None


def load_gauges(rows: list[dict]) -> list[GaugeRecord]:
    records: list[GaugeRecord] = []
    for row in rows:
        uncertainty = row["uncertainty"]
        records.append(
            GaugeRecord(
                id=row["id"],
                serial=row["serial"],
                station=row["station"],
                face_raw=row["face"],
                stage_raw=row["stage"],
                stage=canonical_stage(row["stage"]),
                unit=row["unit"],
                nominal_mm=Interval.from_record(float(row["nominal"]), None, row["unit"]).reading_mm,
                interval=Interval.from_record(
                    float(row["reading"]),
                    float(uncertainty) if uncertainty not in ("", None) else None,
                    row["unit"],
                ),
            )
        )
    return records


def load_source_marks(rows: list[dict]) -> dict[str, SourceMark]:
    marks: dict[str, SourceMark] = {}
    for row in rows:
        marks[row["gauge"]] = SourceMark(
            gauge=row["gauge"],
            datum=row["datum"],
            fixture=row["fixture"],
            log=row["log"],
            source_id=row["id"],
        )
    return marks


def _check_interval(interval: Interval, bound: float, inclusive: bool) -> tuple[GaugeOutcome, str]:
    if inclusive:
        if interval.upper <= bound:
            return GaugeOutcome.PASS, f"全区间 [{interval.lower}, {interval.upper}] 均不超过上限 {bound}"
        if interval.lower > bound:
            return GaugeOutcome.REJECT, f"全区间 [{interval.lower}, {interval.upper}] 均超过上限 {bound}"
        return GaugeOutcome.RETEST, f"区间 [{interval.lower}, {interval.upper}] 跨过闭口上限 {bound}"
    # 开口上限
    if interval.upper < bound:
        return GaugeOutcome.PASS, f"全区间 [{interval.lower}, {interval.upper}] 均严格小于 {bound}"
    if interval.lower > bound:
        return GaugeOutcome.REJECT, f"全区间 [{interval.lower}, {interval.upper}] 均不满足严格小于 {bound}"
    if interval.lower == interval.upper == bound:
        # 无不确定度且读数恰在开口边界：不能自动认可，须复测确认
        return GaugeOutcome.RETEST, f"读数 {bound} 恰为开口上限，无法确认严格小于，须复测"
    return GaugeOutcome.RETEST, f"区间 [{interval.lower}, {interval.upper}] 跨过开口上限 {bound}"


def evaluate_gauge(
    gauge: GaugeRecord,
    requirement: Requirement,
    registry: MappingRegistry,
    source_marks: dict[str, SourceMark],
) -> MeasurementVerdict:
    """判定单条计量记录对某条要求的适用性与区间结论。"""
    verdict = MeasurementVerdict(
        gauge=gauge,
        requirement=requirement,
        outcome=GaugeOutcome.NOT_APPLICABLE,
        source_mark=source_marks.get(gauge.id),
    )

    if requirement.object_kind == "spread" and requirement.point is not None:
        # “两端差”类测单没有测点标签，先看测量时点是否匹配；
        # 带测点标签（如 Rear D）的测单才需要查映射，且驳回优先暴露
        if gauge.is_spread_sheet:
            if gauge.stage != requirement.stage:
                verdict.reasons.append(
                    f"测量时点不符：计量单为“{gauge.stage}”，要求为“{requirement.stage}”"
                )
                return verdict
            verdict.reasons.append(
                f"测单未声明测点 {requirement.point}，不能证明取自该测点"
            )
            return verdict
        mapping = registry.status_for(gauge.serial, gauge.face_raw)
        if mapping is None:
            verdict.reasons.append(
                f"要求在测点 {requirement.point} 取数，但无“{gauge.face_raw}”对应 {requirement.point} 的任何线索"
            )
            return verdict
        verdict.mapping_status = mapping.status
        if mapping.status is MappingStatus.REJECTED:
            verdict.reasons.append(
                f"测点映射被复校驳回：{gauge.face_raw} 不对应 {requirement.point}（证据 {'/'.join(mapping.evidence)}）"
            )
            return verdict
        if mapping.status is MappingStatus.UNCONFIRMED:
            verdict.reasons.append(
                f"测点映射未经质量工程师确认：{gauge.face_raw} -> {requirement.point}"
                f"（线索 {'/'.join(mapping.evidence)}）；确认测点不替代缺少的测量"
            )
            verdict.outcome = GaugeOutcome.RETEST
            if gauge.stage != requirement.stage:
                verdict.reasons.append(
                    f"测量时点不符：计量单为“{gauge.stage}”，要求为“{requirement.stage}”"
                )
            return verdict
        if mapping.point != requirement.point:
            verdict.reasons.append(
                f"确认的真实测点为 {mapping.point}，与要求测点 {requirement.point} 不一致"
            )
            return verdict

    # 1) 测量时点必须一致，换算相等也不能消掉时点条件
    if gauge.stage != requirement.stage:
        verdict.reasons.append(
            f"测量时点不符：计量单为“{gauge.stage}”，要求为“{requirement.stage}”"
        )
        return verdict

    # 2) 测量对象/测点匹配
    if requirement.object_kind == "face":
        if gauge.is_spread_sheet or gauge.face_raw != requirement.face_label:
            verdict.reasons.append(
                f"测量面不符：计量单测“{gauge.face_raw}”，要求测端面“{requirement.face_label}”"
            )
            return verdict
    else:  # spread
        if requirement.point is not None:
            # 已确认映射的测点继续走区间判定；时点已在上面核对
            pass
        elif not gauge.is_spread_sheet:
            verdict.reasons.append(
                f"要求两端最大差测单，收到的是单面测“{gauge.face_raw}”"
            )
            return verdict

    # 3) 区间对边界
    bound = requirement.limit.bound_mm(gauge.nominal_mm)
    verdict.bound_mm = bound
    outcome, reason = _check_interval(gauge.interval, bound, requirement.limit.inclusive)
    verdict.outcome = outcome
    verdict.reasons.append(reason)
    return verdict


def combinable(
    gauges: list[GaugeRecord], source_marks: dict[str, SourceMark]
) -> tuple[bool, list[str]]:
    """同一工装同一工位的多张测单能否合用：基准与装夹标识必须都相同。"""
    reasons: list[str] = []
    marks = [source_marks.get(gauge.id) for gauge in gauges]
    if any(mark is None for mark in marks):
        missing = [gauge.id for gauge, mark in zip(gauges, marks) if mark is None]
        reasons.append(
            f"测单 {', '.join(missing)} 缺少计量基准/装夹标识声明，无法证明合用前提"
        )
        return False, reasons
    datums = {mark.datum for mark in marks}
    fixtures = {mark.fixture for mark in marks}
    if len(datums) != 1:
        reasons.append(
            "计量基准不一致：" + "；".join(f"{gauge.id}={mark.datum}" for gauge, mark in zip(gauges, marks))
        )
    if len(fixtures) != 1:
        reasons.append(
            "最近一次装夹标识不一致："
            + "；".join(f"{gauge.id}={mark.fixture}" for gauge, mark in zip(gauges, marks))
        )
    return not reasons, reasons
