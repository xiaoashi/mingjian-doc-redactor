"""Deterministic synthetic corpus, with truth authored before parsing/scanning.

Every item is a fabricated test value, not a claim that a person/card exists.
Run from the repository root: .venv/bin/python scripts/generate_fixtures.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mingjian.domain.models import dumps
from mingjian.reporting.writers import write_pdf
from mingjian.services.project import file_hash

ROOT = Path(__file__).resolve().parents[1]


def generate():
    from docx import Document
    target = ROOT / "fixtures" / "acceptance"
    target.mkdir(parents=True, exist_ok=True)
    annotations = []
    # Inputs and exact boundaries defined independently of the detector.
    fields = [
        ("phone", "联系电话：", "13800138000"), ("phone", "联系电话：", "13900139000"),
        ("phone", "联系电话：", "18600186000"),
        ("identity", "身份证号：", "11010519491231002X"), ("identity", "证件号码：", "11010519491231002x"),
        ("identity", "身份结构：", "11010519491231002X"),
        ("email", "联系邮箱：", "review@example.org"), ("email", "备用邮箱：", "a.b+tag@example.com"),
        ("email", "工作邮箱：", "Team@example.net"),
        ("bank", "银行卡号：", "4111111111111111"), ("bank", "银行卡号：", "5555555555554444"),
        ("bank", "银行卡号：", "4000 0000 0000 0002"),
        ("word:acceptance-secret", "文档标记：", "内部资料"),
    ]
    negatives = ["订单：13800138000", "流水：4111111111111111", "身份证号：110105194912310021",
                 "卡号：4111111111111112", "正常省略号……", "日期：2026-10-30"]
    for fmt in ("txt", "docx", "pdf"):
        for number in range(1, 21):
            lines = [f"合成验收文档 {number:02d}（不对应真实个人或账户）"]
            truth = []
            for rule_id, label, value in fields:
                if rule_id == "phone" and number % 2 == 0:
                    value = "".join(chr(ord(c) + 0xFEE0) for c in value)
                index = len(lines)
                lines.append(label + value)
                truth.append({"block_order": index, "start": len(label), "end": len(label) + len(value), "rule_id": rule_id})
            lines.extend(negatives)
            path = target / f"synthetic-{fmt}-{number:02d}.{fmt}"
            if fmt == "txt":
                path.write_text("\n".join(lines), encoding="utf-8")
            elif fmt == "docx":
                doc = Document()
                for line in lines:
                    doc.add_paragraph(line)
                doc.save(path)
            else:
                write_pdf(path, [("body", line) for line in lines])
            annotations.append({"path": str(path.relative_to(ROOT / "fixtures")), "sha256": file_hash(path),
                                "format": fmt, "truth": truth})
    config = {"words": [{"id": "acceptance-secret", "word": "内部资料", "category": "保密标记", "risk": "高", "case_sensitive": True, "enabled": True}]}
    (ROOT / "fixtures" / "annotations.json").write_text(dumps({"version": 1, "scope": "固定模板合成回归集，不代表真实世界准确率；尚未经独立人员复核",
        "config": config, "documents": annotations}), encoding="utf-8")
    (ROOT / "fixtures" / "dictionary-template.csv").write_text("word,category,risk,case_sensitive,suggestion,enabled\n内部资料,保密标记,高,true,确认后替换,true\n", encoding="utf-8")
    (ROOT / "fixtures" / "演示文档.txt").write_text("明鉴合成演示\n联系电话：13800138000\n身份证号：11010519491231002X\n邮箱：review@example.org\n银行卡号：4111 1111 1111 1111\n文档标记：内部资料\n请检查重复标点！！\n此处括号（需要人工复核\n", encoding="utf-8")
    print(json.dumps({"documents": len(annotations), "labelled_instances": sum(len(d["truth"]) for d in annotations)}))


if __name__ == "__main__":
    generate()
