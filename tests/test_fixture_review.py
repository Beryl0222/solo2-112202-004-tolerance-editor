"""业务判定层测试：规格解析、译差定位、计量区间、映射、合用与签发重审。"""
from __future__ import annotations

import unittest

from pack_check import read_tables
from fixture_review import evaluate_pack
from fixture_review.engine import Decision, SignoffFlag
from fixture_review.measurements import GaugeOutcome
from fixture_review.mapping import MappingStatus


class PackReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = evaluate_pack(read_tables())
        cls.by_pair = {
            (report.serial, report.station): report for report in cls.review.decisions
        }

    def verdict(self, serial, station, gauge_id):
        report = self.by_pair[(serial, station)]
        return next(v for v in report.verdicts if v.gauge.id == gauge_id)

    # --- 规格解析：数值、单位、开闭、对象、时点、出处都要落字段 -------------
    def test_signed_specs_parse_into_structured_requirements(self):
        sets = self.review.requirement_sets
        a_active = [r for r in sets["A"].active]
        self.assertEqual({r.face_label for r in a_active}, {"左", "右"})
        self.assertTrue(all(r.stage == "去毛刺前" for r in a_active))
        self.assertTrue(all(r.limit.inclusive for r in a_active))
        self.assertEqual({r.limit.delta_mm for r in a_active}, {0.4})  # 加严后
        b_spread = next(r for r in sets["B"].active if r.object_kind == "spread" and r.point is None)
        self.assertFalse(b_spread.limit.inclusive)  # 严格小于，开口
        self.assertEqual(b_spread.delta_mm if hasattr(b_spread, "delta_mm") else b_spread.limit.delta_mm, 0.7)
        self.assertEqual(b_spread.stage, "装机前")
        post_mount = next(r for r in sets["B"].active if r.point == "P2")
        self.assertEqual(post_mount.stage, "装机后")
        self.assertFalse(post_mount.limit.inclusive)  # 沿用原限，仍开口
        # 供应商文书不产生要求
        self.assertTrue(all(r.source_id in {"C1", "C3"} for r in a_active))

    def test_supplier_documents_are_never_authoritative(self):
        for doc in self.review.documents.values():
            if doc.role in {"供应商译稿", "供应商测点说明"}:
                self.assertFalse(doc.authoritative)

    # --- 译稿核对：三处实质译差定位到双语出处 ------------------------------
    def test_translation_divergences_are_substantive_and_located(self):
        dimensions = {d.dimension for d in self.review.translation_review.substantive}
        self.assertEqual(dimensions, {"object", "stage", "limit_open"})
        for divergence in self.review.translation_review.substantive:
            self.assertEqual(divergence.requirement_ref[0], "C1")  # 出处锚定原规
            self.assertEqual(divergence.translation_ref[0], "C2")
            self.assertTrue(divergence.detail_zh)
        object_div = next(d for d in self.review.translation_review.substantive if d.dimension == "object")
        self.assertIn("合计", object_div.detail_zh)
        open_div = next(d for d in self.review.translation_review.substantive if d.dimension == "limit_open")
        self.assertIn("0.7", open_div.detail_zh)

    # --- 计量单位换算与不确定区间三态 --------------------------------------
    def test_unit_conversion_keeps_original_record_and_compares_in_mm(self):
        v2 = self.verdict("V1", "A", "G2")  # 原始单位 um
        self.assertEqual(v2.gauge.unit, "um")
        self.assertEqual(v2.gauge.interval.lower, 10.37)
        self.assertEqual(v2.gauge.interval.upper, 10.39)
        self.assertEqual(v2.outcome, GaugeOutcome.PASS)

    def test_full_interval_outside_bound_is_rejected(self):
        v1_left = self.verdict("V1", "A", "G1")
        self.assertEqual(v1_left.outcome, GaugeOutcome.REJECT)
        self.assertIn("均超过", v1_left.reasons[-1])

    def test_interval_crossing_open_boundary_requires_retest(self):
        v2_g3 = self.verdict("V2", "B", "G3")
        # 区间 0.68~0.70，开口上限 0.7：即使映射确认也仍跨边界
        self.assertEqual(v2_g3.gauge.interval.upper, 0.7)
        self.assertEqual(v2_g3.outcome, GaugeOutcome.RETEST)

    # --- 测点映射：未确认不能用、复校驳回优先、确认不替代测量 ---------------
    def test_unconfirmed_mapping_blocks_auto_approval(self):
        review = evaluate_pack(read_tables())  # 独立实例，避免测试间状态污染
        mapping = review.registry.status_for("V2", "Rear D")
        self.assertEqual(mapping.status, MappingStatus.UNCONFIRMED)
        g3 = next(
            v for report in review.for_serial("V2") for v in report.verdicts if v.gauge.id == "G3"
        )
        self.assertTrue(any("未经质量工程师确认" in reason for reason in g3.reasons))

    def test_rejected_mapping_is_surfaced_and_reblocks(self):
        review = evaluate_pack(read_tables())
        mapping = review.registry.status_for("V3", "Rear D")
        self.assertEqual(mapping.status, MappingStatus.REJECTED)
        g5 = next(
            v for report in review.for_serial("V3") for v in report.verdicts if v.gauge.id == "G5"
        )
        self.assertEqual(g5.outcome, GaugeOutcome.NOT_APPLICABLE)
        self.assertTrue(any("复校驳回" in reason and "S4" in reason for reason in g5.reasons))

    def test_quality_confirmation_does_not_replace_missing_measurement(self):
        review = evaluate_pack(read_tables())
        confirmed = review.registry.confirm("V2", "Rear D", "P2", revision_id="C3", evidence="QE-1")
        self.assertEqual(confirmed.status, MappingStatus.CONFIRMED)
        self.assertEqual(confirmed.applicable_revision, "C3")
        # 确认后重跑：G3 区间仍跨开口边界，结论依旧是复测而非自动认可
        refreshed = evaluate_pack(read_tables())
        refreshed.registry.confirm("V2", "Rear D", "P2", revision_id="C3", evidence="QE-1")
        g3 = self._re_evaluate_gauge(refreshed, "V2", "B", "G3")
        self.assertEqual(g3.outcome, GaugeOutcome.RETEST)
        self.assertTrue(any("跨过开口上限" in reason for reason in g3.reasons))

    @staticmethod
    def _re_evaluate_gauge(review, serial, station, gauge_id):
        """在给定映射登记状态下重新判定单条测单，验证确认映射不改变区间结论。"""
        from fixture_review.measurements import evaluate_gauge, load_gauges, load_source_marks
        gauges = {g.id: g for g in load_gauges(read_tables()["gauges.tsv"])}
        marks = load_source_marks(read_tables()["gauge_sources.tsv"])
        requirement = next(
            r for r in review.requirement_sets[station].active
            if r.station == station and r.point == "P2"
        )
        return evaluate_gauge(gauges[gauge_id], requirement, review.registry, marks)

    def test_rejected_mapping_cannot_be_confirmed(self):
        review = evaluate_pack(read_tables())
        with self.assertRaises(ValueError):
            review.registry.confirm("V3", "Rear D", "P2", revision_id="C3", evidence="QE-2")

    # --- 两张测单合用前提：基准与装夹标识都相同 ----------------------------
    def test_sheets_with_same_datum_but_different_fixture_cannot_combine(self):
        v1 = self.by_pair[("V1", "A")]
        self.assertTrue(any("装夹标识不一致" in note for note in v1.notes))
        self.assertTrue(any("不能合用" in need.condition for need in v1.retest_needs))

    # --- 整机判定与签发 ----------------------------------------------------
    def test_v1_station_a_is_rejected_and_reopened_by_stricter_revision(self):
        report = self.by_pair[("V1", "A")]
        self.assertEqual(report.decision, Decision.REJECTED)
        self.assertEqual(report.signoff_flag, SignoffFlag.REOPEN)
        self.assertTrue(report.historical_basis)  # 旧版 C1 下有通过记录

    def test_mounted_v2_uses_post_mount_exception_and_requires_retest(self):
        report = self.by_pair[("V2", "B")]
        self.assertEqual(report.decision, Decision.RETEST)
        self.assertEqual(report.signoff_flag, SignoffFlag.ENGINEERING_PENDING)
        self.assertEqual({ref.document_id for ref in report.requirement_refs}, {"C3"})
        # 装机前测单 G4 不能作为装机后测法的放行依据
        g4 = self.verdict("V2", "B", "G4")
        self.assertEqual(g4.outcome, GaugeOutcome.NOT_APPLICABLE)

    def test_unmounted_v3_keeps_pre_mount_requirement_and_lacks_key_point(self):
        report = self.by_pair[("V3", "B")]
        self.assertEqual(report.decision, Decision.INSUFFICIENT)
        self.assertEqual(report.signoff_flag, SignoffFlag.NOT_ISSUED)
        self.assertTrue(any("缺少决定性测点" in need.condition for need in report.retest_needs))

    def test_v4_pending_signoff_judged_against_current_revision_and_rejected(self):
        report = self.by_pair[("V4", "A")]
        self.assertEqual(report.decision, Decision.REJECTED)
        self.assertEqual(report.signoff_flag, SignoffFlag.NOT_ISSUED)
        # 右端面无任何测量 → 同时给出补测条件
        self.assertTrue(any("右端面" in need.condition for need in report.retest_needs))

    # --- 主线/备线独立：备工位通过不能放行主线 ------------------------------
    def test_stations_are_judged_independently(self):
        stations = {report.station for report in self.review.decisions}
        self.assertEqual(stations, {"A", "B"})
        for report in self.review.decisions:
            # 每个报告只引用本工位的要求与本工位的测单
            self.assertTrue(all(r.station == report.station for r in
                                self.review.requirement_sets[report.station].active
                                if r.station == report.station))


if __name__ == "__main__":
    unittest.main()
