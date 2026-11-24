"""按“具体工装、具体工位”汇总证据并给出签发判定。

主线/备线各自独立判断，备工位通过不能放行主线。
签发只引用签署原文、有效补充函以及实际可用的测量；译稿线索单列。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .mapping import MappingRegistry, build_registry
from .measurements import (
    GaugeOutcome,
    GaugeRecord,
    MeasurementVerdict,
    combinable,
    evaluate_gauge,
    load_gauges,
    load_source_marks,
)
from .requirements import Requirement, RequirementSet
from .spec_parser import SpecDocument, parse_specs
from .translation import TranslationReview, review_translations


class Decision(str, Enum):
    APPROVED = "可使用"
    REJECTED = "明确拒绝"
    RETEST = "须复测"
    INSUFFICIENT = "证据不足"


class SignoffFlag(str, Enum):
    REOPEN = "加严修订到达，尚未投入，须重审"
    ENGINEERING_PENDING = "已按旧版投用，待工程处理"
    NOT_ISSUED = "未签发/待签，按现行修订直接判定"


@dataclass(frozen=True)
class TextReference:
    document_id: str
    role: str
    sentence: str
    issued: str


@dataclass(frozen=True)
class RetestNeed:
    condition: str
    gauge_ids: tuple[str, ...]
    references: tuple[str, ...]


@dataclass
class StationDecisionReport:
    serial: str
    station: str
    decision: Decision
    requirement_refs: list[TextReference] = field(default_factory=list)
    verdicts: list[MeasurementVerdict] = field(default_factory=list)
    retest_needs: list[RetestNeed] = field(default_factory=list)
    signoff_flag: SignoffFlag | None = None
    authorization_id: str | None = None
    authorization_use: str | None = None
    historical_basis: list[TextReference] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class PackReview:
    decisions: list[StationDecisionReport]
    translation_review: TranslationReview
    registry: MappingRegistry
    requirement_sets: dict[str, RequirementSet]
    documents: dict[str, SpecDocument]
    parse_warnings: list[str]

    def for_serial(self, serial: str) -> list[StationDecisionReport]:
        return [report for report in self.decisions if report.serial == serial]


def _is_mounted(use_text: str | None) -> bool:
    return bool(use_text and "已装机" in use_text)


def _applicable_requirements(
    station: str, requirement_set: RequirementSet, mounted: bool
) -> list[Requirement]:
    """按装机事实选择适用要求。

    补函的装机例外（装机后指定测点取两端最大差）只对已装机夹具生效；
    已装机时该工位的 spread 只走装机后测法，装机前原限保留为历史依据；
    未装机夹具继续走装机前原限，看不到装机后例外。
    """
    post_mount = [
        requirement for requirement in requirement_set.active
        if requirement.station == station
        and requirement.object_kind == "spread" and requirement.point is not None
    ]
    result = []
    for requirement in requirement_set.active:
        if requirement.station != station:
            continue
        if requirement.object_kind == "spread":
            if mounted and post_mount:
                if requirement.point is not None:
                    result.append(requirement)
                # 装机前原限对已装机夹具不再是现行判据
            elif not mounted and requirement.point is None:
                result.append(requirement)
            elif not post_mount:
                result.append(requirement)
        else:
            result.append(requirement)
    return result


def _candidate_gauges(
    gauges: list[GaugeRecord], requirement: Requirement
) -> list[GaugeRecord]:
    if requirement.object_kind == "face":
        return [
            gauge for gauge in gauges
            if not gauge.is_spread_sheet and gauge.face_raw == requirement.face_label
        ]
    if requirement.point is not None:
        return [gauge for gauge in gauges if gauge.station == requirement.station]
    return [gauge for gauge in gauges if gauge.is_spread_sheet]


def _text_ref(requirement: Requirement, documents: dict[str, SpecDocument]) -> TextReference:
    doc = documents[requirement.source_id]
    return TextReference(
        document_id=doc.id,
        role=doc.role,
        sentence=requirement.source_sentence,
        issued=doc.issued,
    )


def evaluate_pack(tables: dict[str, list[dict]]) -> PackReview:
    spec_rows = tables["specs.tsv"]
    gauge_rows = tables["gauges.tsv"]
    auth_rows = tables["authorizations.tsv"]

    requirement_sets, documents, parse_warnings = parse_specs(spec_rows)
    translation_review = review_translations(documents, requirement_sets)
    registry = build_registry(documents, tables["gauge_sources.tsv"], gauge_rows)
    gauges = load_gauges(gauge_rows)
    source_marks = load_source_marks(tables["gauge_sources.tsv"])

    auth_index = {(row["serial"], row["station"]): row for row in auth_rows}
    pairs = sorted({(gauge.serial, gauge.station) for gauge in gauges}
                   | {(row["serial"], row["station"]) for row in auth_rows})

    reports: list[StationDecisionReport] = []
    for serial, station in pairs:
        auth = auth_index.get((serial, station))
        use_text = auth["use"] if auth else None
        mounted = _is_mounted(use_text)
        requirement_set = requirement_sets.get(station, RequirementSet())
        applicable = _applicable_requirements(station, requirement_set, mounted)
        station_gauges = [
            gauge for gauge in gauges if gauge.serial == serial and gauge.station == station
        ]

        report = StationDecisionReport(
            serial=serial,
            station=station,
            decision=Decision.INSUFFICIENT,
            authorization_id=auth["id"] if auth else None,
            authorization_use=use_text,
        )
        report.requirement_refs = [_text_ref(req, documents) for req in applicable]

        outcomes: list[GaugeOutcome] = []
        for requirement in applicable:
            candidates = _candidate_gauges(station_gauges, requirement)
            if not candidates:
                report.retest_needs.append(
                    RetestNeed(
                        condition=(
                            f"缺少决定性测点：{_describe_requirement(requirement)}，"
                            f"须在“{requirement.stage}”补测并出具测单"
                        ),
                        gauge_ids=(),
                        references=(requirement.source_id,),
                    )
                )
                continue
            for gauge in candidates:
                verdict = evaluate_gauge(gauge, requirement, registry, source_marks)
                report.verdicts.append(verdict)
                if verdict.outcome is not GaugeOutcome.NOT_APPLICABLE:
                    outcomes.append(verdict.outcome)
                if verdict.outcome is GaugeOutcome.RETEST:
                    report.retest_needs.append(
                        RetestNeed(
                            condition=(
                                f"{gauge.id}（{gauge.face_raw}，{gauge.stage}）："
                                + "；".join(verdict.reasons)
                                + f"。复测条件：按 {_describe_requirement(requirement)} 重新取数"
                            ),
                            gauge_ids=(gauge.id,),
                            references=(requirement.source_id,),
                        )
                    )
                elif verdict.outcome is GaugeOutcome.REJECT:
                    report.retest_needs.append(
                        RetestNeed(
                            condition=(
                                f"{gauge.id}（{gauge.face_raw}，{gauge.stage}）整段超差，"
                                f"须按 {_describe_requirement(requirement)} 整改后复测"
                            ),
                            gauge_ids=(gauge.id,),
                            references=(requirement.source_id,),
                        )
                    )

        # 同一报告内多张测单的合用条件：基准与最近装夹标识必须一致
        if len(station_gauges) > 1:
            usable_marks = [
                gauge for gauge in station_gauges
                if any(
                    verdict.gauge is gauge and verdict.outcome is not GaugeOutcome.NOT_APPLICABLE
                    for verdict in report.verdicts
                )
            ]
            if len(usable_marks) > 1:
                ok, reasons = combinable(usable_marks, source_marks)
                if not ok:
                    report.notes.append("依赖合并的报告须拆回复测：" + "；".join(reasons))
                    report.retest_needs.append(
                        RetestNeed(
                            condition=(
                                "两张测单计量基准或最近装夹标识不同，不能合用；"
                                "须在同一计量基准、同一装夹标识下重新测量对应端面并合并出单"
                            ),
                            gauge_ids=tuple(gauge.id for gauge in usable_marks),
                            references=tuple(
                                mark.source_id
                                for mark in (source_marks.get(g.id) for g in usable_marks)
                                if mark is not None
                            ),
                        )
                    )

        # 历史依据：被修订要求下曾取得的通过记录
        for old in requirement_set.superseded:
            if old.station != station:
                continue
            for gauge in station_gauges:
                if gauge.stage != old.stage:
                    continue
                if old.object_kind == "face" and (gauge.is_spread_sheet or gauge.face_raw != old.face_label):
                    continue
                old_verdict = evaluate_gauge(gauge, old, registry, source_marks)
                if old_verdict.outcome is GaugeOutcome.PASS:
                    report.historical_basis.append(_text_ref(old, documents))
                    break
        report.historical_basis = list(dict.fromkeys(report.historical_basis))

        # 全量扫描：未参与判定的计量单也要说明为何不能用（如测点映射被驳回、
        # 时点不符），避免工程师收到“静默消失”的测单
        judged_ids = {verdict.gauge.id for verdict in report.verdicts}
        point_probes = [
            requirement for requirement in requirement_set.active
            if requirement.station == station and requirement.point is not None
        ]
        for gauge in station_gauges:
            if gauge.id in judged_ids:
                continue
            # 优先用指定测点要求探测，使“映射被复校驳回”等关键原因可见
            probe = next(iter(point_probes), None) or next(iter(applicable), None)
            if probe is None:
                continue
            extra = evaluate_gauge(gauge, probe, registry, source_marks)
            extra.reasons.insert(0, "该测单不适用任何现行要求，仅作不可用原因记录")
            report.verdicts.append(extra)

        report.decision = _aggregate(outcomes, applicable, report.verdicts)
        report.signoff_flag = _signoff_flag(
            auth, report.decision, requirement_set, mounted, documents
        )
        reports.append(report)

    return PackReview(
        decisions=reports,
        translation_review=translation_review,
        registry=registry,
        requirement_sets=requirement_sets,
        documents=documents,
        parse_warnings=parse_warnings,
    )


def _describe_requirement(requirement: Requirement) -> str:
    if requirement.object_kind == "face":
        return (
            f"{requirement.station} 工位{requirement.face_label}端面、"
            f"{requirement.stage}、上限公称+{requirement.limit.delta_mm} mm"
        )
    point_text = f"测点 {requirement.point} " if requirement.point else ""
    relation = "严格小于" if not requirement.limit.inclusive else "不超过"
    return (
        f"{requirement.station} 工位{point_text}两端最大差、"
        f"{requirement.stage}、{relation} {requirement.limit.delta_mm} mm"
    )


def _aggregate(
    outcomes: list[GaugeOutcome],
    applicable: list[Requirement],
    verdicts: list[MeasurementVerdict],
) -> Decision:
    if GaugeOutcome.REJECT in outcomes:
        return Decision.REJECTED
    if GaugeOutcome.RETEST in outcomes:
        return Decision.RETEST
    if GaugeOutcome.PASS in outcomes:
        # 每条适用要求都至少有一条通过记录才算可使用；否则属于缺关键部位
        covered_requirements = {
            verdict.requirement
            for verdict in verdicts
            if verdict.outcome is GaugeOutcome.PASS and verdict.requirement in applicable
        }
        if covered_requirements == set(applicable):
            return Decision.APPROVED
        return Decision.RETEST
    return Decision.INSUFFICIENT


def _signoff_flag(
    auth: dict | None,
    decision: Decision,
    requirement_set: RequirementSet,
    mounted: bool,
    documents: dict[str, SpecDocument],
) -> SignoffFlag | None:
    if auth is None:
        return None
    use_text = auth["use"]
    signed = auth["signed"]
    amendment_dates = [
        doc.issued for doc in documents.values()
        if doc.role == "签署补函" and doc.issued > signed
    ]
    if "待签" in use_text or "申请" in use_text:
        return SignoffFlag.NOT_ISSUED
    if amendment_dates:
        if mounted:
            return SignoffFlag.ENGINEERING_PENDING
        return SignoffFlag.REOPEN
    return None
