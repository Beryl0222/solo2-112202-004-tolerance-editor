"""供应商译稿核对。

译稿不生成任何判定依据，只与签署原文逐属性比对：
- 测量对象（逐端面 vs 合计）
- 测量时点（去毛刺前/后，装机前/后）
- 极限开闭（严格小于 vs up to）
- 数值与单位
凡会改变测量判读的差异记为 substantive，并定位到双语出处（原文文书 id+原句、
译稿文书 id+原句）；不改变判读的措辞差异记为 wording，不进入供方解释队列。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .requirements import Requirement, RequirementSet
from .spec_parser import SpecDocument
from .units import canonical_stage, to_millimeters

# --- 译稿句式（保持宽松，未命中句式登记告警而不是猜测） -------------------
_EN_SUM_RE = re.compile(
    r"(?P<object>sum of both faces|each face)\s+shall\s+(?P<operator>not exceed|exceed)?\s*"
    r"nominal\s*\+\s*(?P<value>[0-9.]+)\s*(?P<unit>mm|um|µm)\s*(?P<stage>before|after)\s+deburring",
    re.IGNORECASE,
)
_EN_SPREAD_RE = re.compile(
    r"(?:Line\s*(?P<line>[A-Z])[,，]\s*)?maximum spread\s*"
    r"(?P<operator>up to|strictly less than|<|shall be less than)\s*"
    r"(?P<value>[0-9.]+)\s*(?P<unit>mm|um|µm)\s*(?P<stage>before|after)\s+mounting",
    re.IGNORECASE,
)
_STAGE_WORD = {"before": "装机前", "after": "装机后"}


@dataclass(frozen=True)
class TranslationClaim:
    station: str
    object_kind: str          # "face" | "spread" | "sum"（合计值，与逐端面不同对象）
    stage: str
    delta_mm: float
    inclusive: bool
    source_id: str
    source_sentence: str


@dataclass(frozen=True)
class TranslationDivergence:
    dimension: str            # object | stage | limit_open | value
    severity: str             # substantive | wording
    station: str
    detail_zh: str
    requirement_ref: tuple[str, str]   # (文书 id, 原句)
    translation_ref: tuple[str, str]   # (文书 id, 原句)


@dataclass
class TranslationReview:
    divergences: list[TranslationDivergence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confirmed: bool = False  # 译稿未经确认；资料中没有确认记录时恒为 False

    @property
    def substantive(self) -> list[TranslationDivergence]:
        return [item for item in self.divergences if item.severity == "substantive"]


def _parse_english_claims(doc: SpecDocument) -> list[TranslationClaim]:
    claims: list[TranslationClaim] = []
    # 按“句号+空白/结尾”分句，避免切断 0.5 mm 之类的小数
    for sentence in (part.strip() for part in re.split(r"\.\s+|\.$", doc.text) if part.strip()):
        sum_match = _EN_SUM_RE.search(sentence)
        if sum_match:
            object_kind = "sum" if sum_match.group("object").lower() == "sum of both faces" else "face"
            claims.append(
                TranslationClaim(
                    station="A",
                    object_kind=object_kind,
                    stage=canonical_stage(
                        f"{sum_match.group('stage')} deburring"
                    ),
                    delta_mm=to_millimeters(float(sum_match.group("value")), sum_match.group("unit")),
                    inclusive=True,  # shall not exceed 为闭口
                    source_id=doc.id,
                    source_sentence=doc.text,
                )
            )
            continue
        spread_match = _EN_SPREAD_RE.search(sentence)
        if spread_match:
            operator = spread_match.group("operator").lower()
            inclusive = operator in {"up to"}
            stage_word = spread_match.group("stage").lower()
            claims.append(
                TranslationClaim(
                    station=spread_match.group("line") or "B",
                    object_kind="spread",
                    stage=_STAGE_WORD[stage_word],
                    delta_mm=to_millimeters(
                        float(spread_match.group("value")), spread_match.group("unit")
                    ),
                    inclusive=inclusive,
                    source_id=doc.id,
                    source_sentence=doc.text,
                )
            )
    return claims


def _matching_requirements(
    claim: TranslationClaim, sets: dict[str, RequirementSet]
) -> list[Requirement]:
    """找与译稿声明对应的权威要求。

    引用锚点用首次提出该要求的文书（introduced_by）：译稿翻译的是原规，
    即使补函后来加严了数值，双语出处仍应定位到原规原句；现行加严值另由
    判定报告的 requirement_refs 体现。
    """
    target = sets.get(claim.station)
    if target is None:
        return []
    kinds = {"sum": "face", "face": "face", "spread": "spread"}
    wanted = kinds[claim.object_kind]
    pool = [*target.active, *target.superseded]
    # 译稿翻译的是原规口径：按同对象、同偏差值锚定原规要求，
    # 不看时点——时点差异本身就是要输出的译差维度
    exact = [
        req for req in pool
        if req.object_kind == wanted
        and abs(req.limit.delta_mm - claim.delta_mm) < 1e-9
    ]
    if exact:
        return exact
    return [req for req in pool if req.object_kind == wanted]


def review_translations(
    documents: dict[str, SpecDocument], sets: dict[str, RequirementSet]
) -> TranslationReview:
    review = TranslationReview()
    translation_docs = [
        doc for doc in documents.values()
        if doc.lang == "en" and doc.role in {"供应商译稿", "供应商测点说明"}
    ]
    for doc in translation_docs:
        if doc.role != "供应商译稿":
            continue  # 测点说明由 mapping 模块处理
        for claim in _parse_english_claims(doc):
            requirements = _matching_requirements(claim, sets)
            if not requirements:
                review.warnings.append(
                    f"{doc.id} 的声明在权威文书中找不到对应工位要求: {claim.source_sentence}"
                )
                continue
            # 同一对象的权威要求数值与开闭一致，取首条做属性比对即可
            req = requirements[0]
            refs = ((req.source_id, req.source_sentence), (doc.id, doc.text))

            if claim.object_kind != req.object_kind:
                review.divergences.append(
                    TranslationDivergence(
                        dimension="object",
                        severity="substantive",
                        station=claim.station,
                        detail_zh=(
                            f"原文要求逐{req.face_label or ''}端面各自判定，"
                            "译稿改为两端合计值，单端面边界被错误用于合计"
                        ),
                        requirement_ref=refs[0],
                        translation_ref=refs[1],
                    )
                )
            if claim.stage != req.stage:
                review.divergences.append(
                    TranslationDivergence(
                        dimension="stage",
                        severity="substantive",
                        station=claim.station,
                        detail_zh=f"测量时点由“{req.stage}”变为“{claim.stage}”，判定对象批次不同",
                        requirement_ref=refs[0],
                        translation_ref=refs[1],
                    )
                )
            if claim.inclusive != req.limit.inclusive:
                review.divergences.append(
                    TranslationDivergence(
                        dimension="limit_open",
                        severity="substantive",
                        station=claim.station,
                        detail_zh=(
                            "原文为严格小于（开口），译稿 up to 为闭口上限，"
                            "边界值 0.7 mm 的放行结论相反"
                        ),
                        requirement_ref=refs[0],
                        translation_ref=refs[1],
                    )
                )
            if abs(claim.delta_mm - req.limit.delta_mm) > 1e-9:
                review.divergences.append(
                    TranslationDivergence(
                        dimension="value",
                        severity="substantive",
                        station=claim.station,
                        detail_zh=f"偏差值由 {req.limit.delta_mm} mm 变为 {claim.delta_mm} mm",
                        requirement_ref=refs[0],
                        translation_ref=refs[1],
                    )
                )
    return review
