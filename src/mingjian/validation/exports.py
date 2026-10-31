from __future__ import annotations

import re
import zipfile
from pathlib import Path

from mingjian.domain.models import now


def read_output(path):
    path = Path(path)
    if path.suffix == ".txt":
        return path.read_text(encoding="utf-8")
    if path.suffix == ".docx":
        from docx import Document
        with zipfile.ZipFile(path) as archive:
            forbidden = [name for name in archive.namelist() if any(token in name.lower() for token in
                         ("comments", "embeddings", "vba", "customxml/item"))]
            if forbidden:
                raise ValueError("输出 DOCX 含未允许的对象")
            text = archive.read("word/document.xml")
            if b"<w:ins " in text or b"<w:del " in text:
                raise ValueError("输出 DOCX 含修订")
        return "\n".join(p.text for p in Document(path).paragraphs)
    if path.suffix == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as doc:
            if doc.doc.encryption:
                raise ValueError("输出 PDF 不可直接读取")
            return "\n".join(p.extract_text() or "" for p in doc.pages)
    raise ValueError("不支持的回查格式")


def visible(text):
    # Independent text comparison tolerates layout-only line wrapping/whitespace.
    return re.sub(r"\s+", "", text)


def validate_outputs(directory, expected, report_paths, sensitive_values):
    directory = Path(directory)
    checks = []
    for relative, text in expected.items():
        try:
            actual = read_output(directory / relative)
            passed = visible(actual) == visible(text)
            checks.append({"check": "reparse_exact_visible_text", "path": relative, "passed": passed,
                           "expected_chars": len(visible(text)), "actual_chars": len(visible(actual)), "time": now()})
            if Path(relative).suffix == ".txt":
                checks.append({"check": "txt_exact_text", "path": relative, "passed": actual == text, "time": now()})
        except Exception as exc:
            checks.append({"check": "reparse", "path": relative, "passed": False,
                           "error_type": type(exc).__name__, "time": now()})
    for relative in report_paths:
        try:
            text = read_output(directory / relative) if Path(relative).suffix in (".txt", ".pdf", ".docx") else (directory / relative).read_text(encoding="utf-8")
            leaked = any(len(value.strip()) >= 3 and value in text for value in sensitive_values)
            checks.append({"check": "report_no_full_sensitive_examples", "path": relative, "passed": not leaked, "time": now()})
        except Exception as exc:
            checks.append({"check": "report_readable", "path": relative, "passed": False, "error_type": type(exc).__name__, "time": now()})
    allowed = {".txt", ".docx", ".pdf", ".json", ".png"}
    package_safe = all(p.suffix in allowed for p in directory.rglob("*") if p.is_file())
    checks.append({"check": "package_allowlist", "passed": package_safe, "time": now()})
    return {"passed": all(item["passed"] for item in checks), "checks": checks, "executed_at": now(),
            "scope": "核对重新解析的副本文本与冻结替换预期、报告示例及包结构；不证明未识别敏感信息不存在"}
