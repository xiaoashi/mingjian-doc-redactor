"""Optional AI extension contract. No model/network calls by default."""
from dataclasses import dataclass
from typing import Protocol

from mingjian.domain.models import Block, MingjianError


@dataclass(frozen=True)
class Suggestion:
    block_id: str
    start: int
    end: int
    category: str
    reason: str


class AIReviewer(Protocol):
    name: str

    def suggest(self, blocks: list[Block], *, cancel=None) -> list[Suggestion]: ...


def validate_suggestions(blocks, suggestions):
    text = {b.id: b.text for b in blocks}
    for item in suggestions:
        if item.block_id not in text or not 0 <= item.start < item.end <= len(text[item.block_id]):
            raise MingjianError("AI 建议引用了无效原文位置，已拒绝应用")
        if not item.reason.strip() or len(item.reason) > 1000:
            raise MingjianError("AI 建议缺少有效解释")
    return suggestions


class DisabledReviewer:
    name = "未启用"

    def suggest(self, blocks, *, cancel=None):
        raise MingjianError("AI 辅助接口尚未配置；本地规则和 OCR 不依赖 AI API")


def review_with_provider(provider, blocks, *, consent=False, cancel=None):
    if not consent:
        raise MingjianError("调用 AI 前需要明确授权本次文档内容的发送范围与去向")
    return validate_suggestions(blocks, provider.suggest(blocks, cancel=cancel))
