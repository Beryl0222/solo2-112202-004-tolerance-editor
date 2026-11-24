"""工装外协规格核对与签发判定。

本包只做只读判定：权威依据为签署原规与有效补充函；供应商译稿在未确认前
仅作为制作沟通线索，不参与放行。所有判定保留文本出处（文档 id 与原句），
单位换算只用于数值比较，不消解原文要求的对象、测点与测量时点。
"""
from __future__ import annotations

from .engine import evaluate_pack

__all__ = ["evaluate_pack"]
