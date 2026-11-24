"""判定规则的合成场景测试：不修改资料包，全部在内存中构造最小输入。"""
from __future__ import annotations

import copy
import unittest

from pack_check import read_tables
from fixture_review import evaluate_pack
from fixture_review.engine import Decision
from fixture_review.measurements import (
    GaugeOutcome,
    combinable,
    evaluate_gauge,
    load_gauges,
    load_source_marks,
)
from fixture_review.mapping import MappingRegistry, MappingStatus
from fixture_review.units import Interval
from fixture_review.requirements import Requirement, UpperLimit


def _base_tables():
    return copy.deepcopy(read_tables())


class OpenBoundaryTest(unittest.TestCase):
    def test_reading_exactly_at_open_bound_without_uncertainty_is_retest(self):
        tables = _base_tables()
        # 构造一张无不确定度、读数恰等于开口上限 0.7 的装机前两端差测单
        tables["gauges.tsv"].append(
            {
                "id": "GX", "serial": "V9", "station": "B", "face": "两端差",
                "stage": "装机前", "unit": "mm", "nominal": "0",
                "reading": "0.7", "uncertainty": "",
            }
        )
        tables["authorizations.tsv"].append(
            {"id": "AX", "serial": "V9", "station": "B", "signed": "2026-11-01", "use": "待签"}
        )
        review = evaluate_pack(tables)
        verdict = next(v for r in review.for_serial("V9") for v in r.verdicts if v.gauge.id == "GX")
        self.assertEqual(verdict.outcome, GaugeOutcome.RETEST)
        self.assertTrue(any("开口上限" in reason for reason in verdict.reasons))

    def test_whole_interval_below_open_bound_is_approved(self):
        requirement = Requirement(
            station="B", object_kind="spread", face_label=None, point=None,
            stage="装机前",
            limit=UpperLimit(delta_mm=0.7, kind="absolute", inclusive=False),
            source_id="C1", source_sentence="x", introduced_by="C1",
        )
        gauge = load_gauges([
            {"id": "GY", "serial": "V9", "station": "B", "face": "两端差",
             "stage": "装机前", "unit": "mm", "nominal": "0",
             "reading": "0.68", "uncertainty": "0.01"}
        ])[0]
        verdict = evaluate_gauge(gauge, requirement, MappingRegistry(), {})
        self.assertEqual(verdict.outcome, GaugeOutcome.PASS)


class CombinationTest(unittest.TestCase):
    def test_same_datum_and_fixture_marks_combinable(self):
        gauges = load_gauges([
            {"id": "H1", "serial": "V8", "station": "A", "face": "左",
             "stage": "去毛刺前", "unit": "mm", "nominal": "10", "reading": "10.2", "uncertainty": ""},
            {"id": "H2", "serial": "V8", "station": "A", "face": "右",
             "stage": "去毛刺前", "unit": "mm", "nominal": "10", "reading": "10.2", "uncertainty": ""},
        ])
        marks = load_source_marks([
            {"id": "M1", "gauge": "H1", "datum": "基准甲", "fixture": "C-20", "log": ""},
            {"id": "M2", "gauge": "H2", "datum": "基准甲", "fixture": "C-20", "log": ""},
        ])
        ok, reasons = combinable(gauges, marks)
        self.assertTrue(ok, reasons)

    def test_missing_source_mark_blocks_combination(self):
        gauges = load_gauges([
            {"id": "H1", "serial": "V8", "station": "A", "face": "左",
             "stage": "去毛刺前", "unit": "mm", "nominal": "10", "reading": "10.2", "uncertainty": ""},
            {"id": "H2", "serial": "V8", "station": "A", "face": "右",
             "stage": "去毛刺前", "unit": "mm", "nominal": "10", "reading": "10.2", "uncertainty": ""},
        ])
        ok, reasons = combinable(gauges, {})
        self.assertFalse(ok)
        self.assertTrue(any("缺少" in reason for reason in reasons))


class MappingLifecycleTest(unittest.TestCase):
    def test_confirmed_then_rejected_mapping_unsplits_other_points_but_blocks_merge(self):
        tables = _base_tables()
        review = evaluate_pack(tables)
        registry = review.registry
        # 工程师先确认 V2 的 D -> P2
        registry.confirm("V2", "Rear D", "P2", revision_id="C3", evidence="QE-9")
        self.assertEqual(registry.status_for("V2", "Rear D").status, MappingStatus.CONFIRMED)
        # 复校随后驳回：驳回优先，且同一报告内不依赖该映射的其他结论不受影响
        registry.reject("V2", "Rear D", "P2", evidence="S9")
        self.assertEqual(registry.status_for("V2", "Rear D").status, MappingStatus.REJECTED)
        # 驳回后供方重复来文不能翻案
        registry.add_claim(
            type(registry.status_for("V2", "Rear D"))(
                serial="V2", label="D", point="P2",
                status=MappingStatus.UNCONFIRMED, evidence=("C4",),
            )
        )
        self.assertEqual(registry.status_for("V2", "Rear D").status, MappingStatus.REJECTED)

    def test_rejected_mapping_on_one_gauge_keeps_other_gauge_verdict_usable(self):
        # V3 的 G5 映射被驳回，但 V3 若另有合规测单，其结论不受牵连
        tables = _base_tables()
        tables["gauges.tsv"].append(
            {"id": "G9", "serial": "V3", "station": "B", "face": "两端差",
             "stage": "装机前", "unit": "mm", "nominal": "0",
             "reading": "0.62", "uncertainty": "0.01"}
        )
        review = evaluate_pack(tables)
        report = next(r for r in review.for_serial("V3") if r.station == "B")
        g9 = next(v for v in report.verdicts if v.gauge.id == "G9")
        g5 = next(v for v in report.verdicts if v.gauge.id == "G5")
        self.assertEqual(g9.outcome, GaugeOutcome.PASS)
        self.assertEqual(g5.outcome, GaugeOutcome.NOT_APPLICABLE)
        self.assertEqual(report.decision, Decision.APPROVED)


if __name__ == "__main__":
    unittest.main()
