"""接收组查询入口：按交付工装查看可用工位、可委托复测与签发依据。

用法：
    python3 -m fixture_review.review_cli                # 全部工装
    python3 -m fixture_review.review_cli --serial V2    # 指定制造编号
    python3 -m fixture_review.review_cli --json         # 机器可读输出
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .engine import PackReview, evaluate_pack
from pack_check import DEFAULT_DATA, read_tables


def _review(directory: Path) -> PackReview:
    return evaluate_pack(read_tables(directory))


def _render_text(review: PackReview, serial_filter: str | None) -> str:
    lines: list[str] = []
    decisions = review.decisions
    if serial_filter:
        decisions = [report for report in decisions if report.serial == serial_filter]

    lines.append("# 工装工位判定（依据：签署原规与有效补充函；供方译稿未确认仅供线索）")
    for report in decisions:
        lines.append("")
        lines.append(f"## 工装 {report.serial} / 工位 {report.station} —— {report.decision.value}")
        if report.authorization_id:
            lines.append(
                f"- 签发记录 {report.authorization_id}，原始状态：{report.authorization_use}"
                + (f"；{report.signoff_flag.value}" if report.signoff_flag else "")
            )
        lines.append("- 现行有效文字：")
        for ref in report.requirement_refs:
            lines.append(f"  - [{ref.document_id}｜{ref.role}｜{ref.issued}] {ref.sentence}")
        if report.historical_basis:
            lines.append("- 历史依据（旧版下成立的测量）：")
            for ref in report.historical_basis:
                lines.append(f"  - [{ref.document_id}｜{ref.issued}] {ref.sentence}")
        lines.append("- 计量判定：")
        for verdict in report.verdicts:
            gauge = verdict.gauge
            interval = gauge.interval
            lines.append(
                f"  - {gauge.id} {gauge.face_raw}（{gauge.stage}，原单位 {gauge.unit}，"
                f"区间 {interval.lower}~{interval.upper} mm）→ {verdict.outcome.value}"
            )
            for reason in verdict.reasons:
                lines.append(f"      · {reason}")
        for note in report.notes:
            lines.append(f"- 合用性：{note}")
        if report.retest_needs:
            lines.append("- 可委托复测：")
            for need in report.retest_needs:
                lines.append(
                    f"  - {need.condition}（关联测单 {', '.join(need.gauge_ids) or '无'}；"
                    f"依据 {', '.join(need.references)}）"
                )
        else:
            lines.append("- 可委托复测：无")

    lines.append("")
    lines.append("# 译稿核对（仅影响测量判读的实质译差进入供方解释队列）")
    substantive = review.translation_review.substantive
    if not substantive:
        lines.append("- 未发现实质性译差")
    for divergence in substantive:
        lines.append(
            f"- 工位 {divergence.station}｜维度 {divergence.dimension}：{divergence.detail_zh}"
        )
        lines.append(f"    原文 [{divergence.requirement_ref[0]}]：{divergence.requirement_ref[1]}")
        lines.append(f"    译稿 [{divergence.translation_ref[0]}]：{divergence.translation_ref[1]}")
    for warning in review.translation_review.warnings:
        lines.append(f"- 译稿告警：{warning}")

    lines.append("")
    lines.append("# 测点映射登记")
    for mapping in sorted(review.registry.mappings.values(), key=lambda item: item.serial):
        lines.append(
            f"- {mapping.serial}：{mapping.label} -> {mapping.point}：{mapping.status.value}"
            f"（证据 {'/'.join(mapping.evidence)}"
            + (f"，适用修订 {mapping.applicable_revision}" if mapping.applicable_revision else "")
            + "）"
        )

    if review.parse_warnings:
        lines.append("")
        lines.append("# 解析告警（须人工处理，未参与自动判定）")
        for warning in review.parse_warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_json(review: PackReview, serial_filter: str | None) -> str:
    payload = {
        "decisions": [
            asdict(report)
            for report in review.decisions
            if not serial_filter or report.serial == serial_filter
        ],
        "translation_divergences": [
            asdict(item) for item in review.translation_review.divergences
        ],
        "translation_warnings": review.translation_review.warnings,
        "mappings": [asdict(item) for item in review.registry.mappings.values()],
        "parse_warnings": review.parse_warnings,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DATA, help="业务资料包路径")
    parser.add_argument("--serial", help="只输出指定制造编号")
    parser.add_argument("--json", action="store_true", dest="as_json", help="输出 JSON")
    args = parser.parse_args(argv)

    review = _review(args.directory)
    if args.serial and not review.for_serial(args.serial):
        parser.error(f"资料中找不到制造编号 {args.serial}")
    print(_render_json(review, args.serial) if args.as_json else _render_text(review, args.serial))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
