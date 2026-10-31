from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

from mingjian.domain.models import APP_NAME, MingjianError, VERSION, dumps, now, uid
from mingjian.parsers.documents import checkpoint
from mingjian.reporting.writers import WRITERS, write_chart
from mingjian.services.project import file_hash
from mingjian.validation.exports import validate_outputs
from mingjian.services.ai import ai_provenance


def export_project(project, document_ids, formats=("txt", "docx", "pdf"), output_root=None, cancel=None, progress=None):
    if not document_ids or any(f not in WRITERS for f in formats) or not formats:
        raise MingjianError("请选择至少一个文档和有效输出格式")
    export_id = uid()
    root = Path(output_root or project.root / "exports").resolve()
    if root.is_relative_to(project.root / "sources") or root.is_relative_to(project.root / "trash"):
        raise MingjianError("不能导出到源快照或回收区目录")
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / (".pending-" + export_id)
    destination = root / ("mingjian-export-" + export_id)
    with project.lock:
        project.ensure_editable()
        for document_id in document_ids:
            if project.document(document_id)["review_status"] != "completed":
                raise MingjianError("仅能导出当前版本已完成审查的文档")
            # Recheck all gates immediately before freezing.
            project.complete(document_id)
        project.frozen = True
    project.db.write(lambda db: db.execute("INSERT INTO export_records(id,status,created_at) VALUES(?,'running',?)", (export_id, now())))
    try:
        temporary.mkdir()
        (temporary / "copies").mkdir()
        expected, manifest_docs, report_items, sensitive_values = {}, [], [], set()
        category_counts, risk_counts, status_counts = Counter(), Counter(), Counter()
        for index, document_id in enumerate(document_ids):
            checkpoint(cancel)
            doc = project.document(document_id)
            project.verify_source(document_id)
            version, blocks = project.parsed(document_id)
            run = project.db.one("SELECT * FROM scan_runs WHERE id=?", (doc["current_run_id"],))
            snapshot = project.db.one("SELECT * FROM rule_snapshots WHERE id=?", (run["snapshot_id"],))
            findings = project.findings(document_id)
            preview = project.preview(document_id)
            if preview["conflicts"]:
                raise MingjianError("导出存在未解决区间冲突")
            # Never put untrusted original filenames in export metadata or output names.
            alias = f"文档-{index + 1:03d}"
            sections = [("title", alias + " · 脱敏副本"), ("body", f"{APP_NAME} V{VERSION}；已完成当前规则范围内的人工复核；规范重新排版。")]
            warnings = json.loads(version["warnings_json"])
            if version["coverage_status"] == "partial":
                sections.append(("body", "部分覆盖：以下限制始终保留，非文本对象及未覆盖内容未经审查。\n" + "\n".join(warnings)))
            else:
                sections.append(("body", "覆盖范围：受支持的解析文本；规则识别与复核不代表不存在其他敏感内容。"))
            for block in blocks:
                label = {"header": "页眉来源", "footer": "页脚来源", "table": "表格来源"}.get(block.kind)
                if label:
                    loc = block.locator
                    location = f"行 {loc.get('row', '-')} 列 {loc.get('column', '-')}" if block.kind == "table" else "关联节 " + ",".join(map(str, loc.get("sections", [])))
                    sections.append(("body", f"【{label}：{location}】"))
                sections.append(("body", preview["texts"][block.id]))
            expected_text = "\n\n".join(text for _, text in sections)
            for fmt in formats:
                relative = f"copies/document-{index + 1:03d}.{fmt}"
                WRITERS[fmt](temporary / relative, sections)
                expected[relative] = expected_text
            groups = {}
            bmap = {b.id: b for b in blocks}
            for finding in findings:
                groups.setdefault(finding["group_id"], []).append(finding)
                value = bmap[finding["block_id"]].text[finding["start_offset"]:finding["end_offset"]]
                if finding["category"] != "文本规范":
                    sensitive_values.add(value)
                status_counts[finding["status"]] += 1
                # No raw excerpts or notes: free-form text may repeat sensitive values.
                report_items.append({"document": alias, "group_id": finding["group_id"], "rule_id": finding["rule_id"],
                    "category": finding["category"], "risk": finding["risk"], "status": finding["status"],
                    "example": "[已隐藏]", "suggestion": finding["suggestion"], "reason": finding["reason"],
                    "mode": finding["strategy"].get("mode", "full")})
            for group in groups.values():
                primary = sorted(group, key=lambda f: (f["priority"], f["start_offset"], -f["end_offset"]))[0]
                category_counts[primary["category"]] += 1
                risk = min(group, key=lambda f: {"高": 0, "中": 1, "低": 2}[f["risk"]])["risk"]
                risk_counts[risk] += 1
            manifest_docs.append({"alias": alias, "document_id": document_id, "source_sha256": doc["sha256"],
                "parse_id": version["id"], "parser_version": version["parser_version"], "rule_snapshot_id": snapshot["id"],
                "rule_snapshot_sha256": snapshot["content_hash"], "rule_version": snapshot["version"],
                "review_revision": doc["revision"], "coverage": version["coverage_status"], "warnings": warnings,
                "group_count": len(groups), "subfinding_count": len(findings), "whitelist_excluded": run["excluded_count"],
                "replacement_count": len(preview["plan"]),
                "full_replacements": sum(p["mode"] == "full" for p in preview["plan"]),
                "partial_replacements": sum(p["mode"] == "partial" for p in preview["plan"])})
            manifest_docs[-1]["ai_review"] = ai_provenance(project,run["id"])
            if progress:
                progress(index + 1, len(document_ids), "生成副本与报告")
        def sanitize(value):
            if isinstance(value, str):
                for secret in sorted(sensitive_values, key=len, reverse=True):
                    if len(secret.strip()) >= 3:
                        value = value.replace(secret, "[已隐藏]")
                return value
            if isinstance(value, list):
                return [sanitize(v) for v in value]
            if isinstance(value, dict):
                return {k: sanitize(v) for k, v in value.items()}
            return value
        report_items = sanitize(report_items)
        report = {"software": APP_NAME, "version": VERSION, "export_id": export_id, "generated_at": now(),
            "project": "[项目名称不公开]", "project_id": project.info["id"], "documents": manifest_docs,
            "group_count": sum(category_counts.values()), "subfinding_count": len(report_items),
            "category_counts": dict(category_counts), "risk_counts": dict(risk_counts), "status_counts": dict(status_counts),
            "items": report_items, "scope": "规则范围内的人工复核；未覆盖对象未经审查；图表按问题组计数。文件使用匿名序号，避免文件名泄露隐私。"}
        report_sections = [("title", "明鉴 · 文档审查报告"), ("body", f"V{VERSION}　生成时间 {report['generated_at']}"),
            ("body", report["scope"]), ("body", f"文件 {len(document_ids)}；问题组 {report['group_count']}；子命中 {len(report_items)}"),
            ("body", "类别分布：" + dumps(dict(category_counts))), ("body", "风险分布：" + dumps(dict(risk_counts))),
            ("body", "处理数量：" + dumps(dict(status_counts)))]
        for doc in manifest_docs:
            report_sections.append(("body", f"{doc['alias']}：覆盖 {doc['coverage']}；规则版本 {doc['rule_version']}；问题组 {doc['group_count']}；子命中 {doc['subfinding_count']}。" + "\n".join(doc["warnings"])))
        for item in report_items:
            report_sections.append(("body", f"{item['document']} | {item['category']} | {item['risk']} | {item['status']} | [已隐藏] | 建议：{item['suggestion']} | 忽略原因：{item['reason'] or '无'}"))
        (temporary / "report.json").write_text(dumps(report), encoding="utf-8")
        WRITERS["txt"](temporary / "report.txt", report_sections)
        WRITERS["pdf"](temporary / "report.pdf", report_sections)
        expected["report.pdf"] = "\n\n".join(text for _, text in report_sections)
        write_chart(temporary / "category-chart.png", category_counts)
        checkpoint(cancel)
        if progress:
            progress(len(document_ids), len(document_ids), "独立重新解析并回查")
        validation = validate_outputs(temporary, expected, ["report.json", "report.txt", "report.pdf"], sensitive_values)
        (temporary / "validation.json").write_text(dumps(validation), encoding="utf-8")
        if not validation["passed"]:
            project.db.write(lambda db: db.execute("UPDATE export_records SET validation_json=? WHERE id=?", (dumps(validation), export_id)))
            raise MingjianError("输出回查未通过，未发布成品；请在历史中查看失败检查")
        files = [{"path": str(p.relative_to(temporary)), "sha256": file_hash(p)} for p in sorted(temporary.rglob("*")) if p.is_file()]
        manifest = {"software": APP_NAME, "version": VERSION, "export_id": export_id, "created_at": now(), "documents": manifest_docs, "files": files}
        (temporary / "manifest.json").write_text(dumps(manifest), encoding="utf-8")
        checkpoint(cancel)
        temporary.rename(destination)
        project.db.write(lambda db: db.execute("UPDATE export_records SET status='success',output_path=?,manifest_json=?,validation_json=? WHERE id=?",
            (str(destination), dumps(manifest), dumps(validation), export_id)))
        return {"id": export_id, "path": str(destination), "validation": validation}
    except Exception as exc:
        from mingjian.domain.models import Cancelled
        state = "cancelled" if isinstance(exc, Cancelled) else "failed"
        message = str(exc) if isinstance(exc, MingjianError) else type(exc).__name__
        shutil.rmtree(temporary, ignore_errors=True)
        # If final DB commit fails, do not leave a misleading completed directory.
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        project.db.write(lambda db: db.execute("UPDATE export_records SET status=?,error=? WHERE id=?",
            (state, message, export_id)))
        raise
    finally:
        with project.lock:
            project.frozen = False
