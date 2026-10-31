"""Packaged-app diagnostic using synthetic data, never user documents."""
import json
import platform
import time
from pathlib import Path

from mingjian.domain.models import APP_NAME, VERSION, dumps, now, uid
from mingjian.infrastructure.maintenance import backup_project, restore_project
from mingjian.reporting.writers import write_pdf
from mingjian.services.export import export_project
from mingjian.services.project import Project


def selfcheck(parent):
    from docx import Document
    beginning = time.perf_counter()
    root = Path(parent).resolve() / ("mingjian-selfcheck-" + uid())
    root.mkdir(parents=True)
    input_dir = root / "synthetic-inputs"
    input_dir.mkdir()
    text = "合成诊断文档\n联系电话：13800138000\n邮箱：check@example.org\n身份证号：11010519491231002X\n银行卡号：4111111111111111"
    (input_dir / "synthetic.txt").write_text("😀" + text, encoding="utf-8")
    doc = Document()
    for line in text.splitlines():
        doc.add_paragraph(line)
    doc.save(input_dir / "synthetic.docx")
    write_pdf(input_dir / "synthetic.pdf", [("body", line) for line in text.splitlines()])
    project = Project.create(root, "合成离线自检")
    result = {"software": APP_NAME, "version": VERSION, "executed_at": now(), "environment": platform.platform(), "checks": []}
    try:
        identifiers = project.import_files(project.import_preview(sorted(input_dir.iterdir())))["imported"]
        for document_id in identifiers:
            project.parse(document_id)
            version, _ = project.parsed(document_id)
            if version["coverage_status"] == "partial":
                project.acknowledge_coverage(document_id)
            project.scan(document_id)
            findings = project.findings(document_id)
            if len(findings) != 4:
                raise RuntimeError("合成诊断候选数量不符")
            project.review_many(document_id, [{"id": f["id"], "revision": f["revision"], "status": "confirmed"} for f in findings])
            project.complete(document_id)
        result["checks"].append({"name": "three_format_import_parse_scan_review", "passed": len(identifiers) == 3})
        export = export_project(project, identifiers)
        result["checks"].append({"name": "three_format_export_independent_validation", "passed": export["validation"]["passed"]})
        backup_project(project, root / "backup.zip")
        restored = Project(restore_project(root / "backup.zip", root))
        try:
            recovered = all(restored.document(d)["review_status"] == "completed" for d in identifiers)
            result["checks"].append({"name": "backup_restore", "passed": recovered})
        finally:
            restored.close()
        result["export_path"] = export["path"]
    finally:
        project.close()
    result["passed"] = all(item["passed"] for item in result["checks"])
    result["seconds"] = time.perf_counter() - beginning
    (root / "selfcheck.json").write_text(dumps(result), encoding="utf-8")
    print(json.dumps({"path": str(root), **result}, ensure_ascii=False, indent=2))
    return result["passed"]
