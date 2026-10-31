"""Normalization never mutates original text; maps every emitted codepoint back."""
from dataclasses import dataclass


@dataclass
class MappedText:
    text: str
    spans: list[tuple[int, int]]

    def original_span(self, start, end):
        if not 0 <= start < end <= len(self.spans):
            raise ValueError("无效的规范化位置")
        return self.spans[start][0], self.spans[end - 1][1]


def normalize(text, fullwidth=False, lower=False, remove_spaces=False):
    chars, spans = [], []
    for index, char in enumerate(text):
        if remove_spaces and char in " \t\u3000":
            continue
        if fullwidth and "０" <= char <= "９":
            char = chr(ord(char) - 0xFEE0)
        if lower:
            char = char.lower()
        chars.extend(char)
        spans.extend([(index, index + 1)] * len(char))
    return MappedText("".join(chars), spans)


def utf16_offset(text, codepoint):
    if not 0 <= codepoint <= len(text):
        raise ValueError("无效的原文位置")
    return len(text[:codepoint].encode("utf-16-le")) // 2
