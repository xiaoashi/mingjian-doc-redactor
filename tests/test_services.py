import json
import sqlite3
import threading
import zipfile
from pathlib import Path

import pytest

from mingjian.domain.models import Cancelled, Limits, MingjianError
from mingjian.infrastructure.maintenance import backup_project, restore_project
from mingjian.services.export import export_project
from mingjian.services.project import Project, file_hash
from conftest import confirm_all


def test_import_dedupe_same_name_remove_restore(project, tmp_path):
    left, right = tmp_path / "a", tmp_path / "b"
    left.mkdir(); right.mkdir()
    a, b = left / "same.txt", right / "same.txt"
    a.write_text("第一份", encoding="utf-8")
    b.write_text("第二份", encoding="utf-8")
    preview = project.import_preview([a, b, a])
    assert [p["status"] for p in preview] == ["ready", "ready", "duplicate"]
    result = project.import_files(preview)
    assert len(result["imported"]) == 2
    identifier = result["imported"][0]
    project.remove(identifier)
    assert len(project.documents()) == 1 and a.exists()
    project.remove(identifier, restore=True)
    assert len(project.documents()) == 2


def test_batch_parse_failures_and_encoding(project, tmp_path):
    bad, good = tmp_path / "bad.pdf", tmp_path / "good.txt"
    bad.write_bytes(b"%PDF-1.7\ngarbage")
    good.write_text("你好", encoding="gb18030")
    ids = project.import_files(project.import_preview([bad, good]))["imported"]
    for identifier in ids:
        with pytest.raises(MingjianError):
            project.parse(identifier)
    assert all(d["parse_status"] == "failed" for d in project.documents())
    project.parse(ids[1], encoding="gb18030")
    assert project.parsed(ids[1])[1][0].text == "你好"


def test_review_undo_redo_restart_and_optimistic_lock(project, imported):
    identifier = imported()
    project.scan(identifier)
    finding = project.findings(identifier)[0]
    with pytest.raises(MingjianError):
        project.review_many(identifier, [{"id": finding["id"], "revision": finding["revision"], "status": "ignored", "reason": ""}])
    project.review_many(identifier, [{"id": finding["id"], "revision": finding["revision"], "status": "ignored", "reason": "合成测试明确保留", "note": "复核备注"}])
    assert project.findings(identifier)[0]["status"] == "ignored"
    with pytest.raises(MingjianError):
        project.review_many(identifier, [{"id": finding["id"], "revision": finding["revision"], "status": "confirmed"}])
    project.undo(identifier)
    assert project.findings(identifier)[0]["status"] == "pending"
    project.undo(identifier, redo=True)
    assert project.findings(identifier)[0]["reason"] == "合成测试明确保留"
    path = project.root
    project.close()
    reopened = Project(path)
    try:
        assert reopened.findings(identifier)[0]["note"] == "复核备注"
        assert reopened.db.rows("SELECT * FROM review_actions")
    finally:
        reopened.close()


def test_complete_gates_and_zero_hits(project, imported):
    identifier = imported("无候选问题的文档")
    with pytest.raises(MingjianError):
        project.complete(identifier)
    project.scan(identifier)
    assert project.document(identifier)["review_status"] == "in_review"
    project.complete(identifier)
    assert project.document(identifier)["review_status"] == "completed"
    project.scan(identifier)
    assert project.document(identifier)["review_status"] == "in_review"
    other = imported()
    project.scan(other)
    with pytest.raises(MingjianError):
        project.complete(other)


def test_rules_history_is_immutable(project, imported):
    identifier = imported()
    first, config = project.rules()
    project.scan(identifier)
    config["rules"]["phone"]["enabled"] = False
    project.publish_rules(config)
    assert project.rules()[0]["version"] == 2
    run = project.db.one("SELECT * FROM scan_runs WHERE id=?", (project.document(identifier)["current_run_id"],))
    assert run["snapshot_id"] == first["id"]
    with pytest.raises(sqlite3.IntegrityError):
        project.db.write(lambda db: db.execute("UPDATE rule_snapshots SET name='changed' WHERE id=?", (first["id"],)))


def test_source_change_blocks_export_and_review(project, imported):
    identifier = imported()
    project.scan(identifier)
    confirm_all(project, identifier)
    project.complete(identifier)
    doc = project.document(identifier)
    (project.root / doc["source_path"]).write_text("changed", encoding="utf-8")
    with pytest.raises(MingjianError):
        export_project(project, [identifier])
    assert project.document(identifier)["review_status"] == "needs_review"


def test_export_all_formats_independent_reparse_and_no_sources(project, imported):
    identifier = imported("联系电话：13800138000\n邮箱：person@example.org\n保留正常正文")
    project.scan(identifier)
    confirm_all(project, identifier)
    project.complete(identifier)
    result = export_project(project, [identifier])
    folder = Path(result["path"])
    assert result["validation"]["passed"]
    assert all((folder / "copies" / f"document-001.{fmt}").is_file() for fmt in ("docx", "pdf", "txt"))
    assert "13800138000" not in (folder / "report.json").read_text(encoding="utf-8")
    assert not list(folder.rglob("*.sqlite3"))
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert all(file_hash(folder / item["path"]) == item["sha256"] for item in manifest["files"])


def test_export_failed_validation_no_final_directory(project, imported, monkeypatch):
    import mingjian.services.export as module
    identifier = imported()
    project.scan(identifier); confirm_all(project, identifier); project.complete(identifier)
    monkeypatch.setattr(module, "validate_outputs", lambda *args: {"passed": False, "checks": []})
    with pytest.raises(MingjianError):
        export_project(project, [identifier])
    assert not list((project.root / "exports").iterdir())
    assert not project.frozen
    assert project.db.one("SELECT status FROM export_records")["status"] == "failed"


def test_ignored_same_value_and_partial_redaction(project, imported):
    identifier = imported("电话13800138000\n电话13800138000\n电话13900139000")
    project.scan(identifier)
    items = project.findings(identifier)
    project.review_many(identifier, [
        {"id": items[0]["id"], "revision": 0, "status": "confirmed"},
        {"id": items[1]["id"], "revision": 0, "status": "ignored", "reason": "授权保留此处合成值"},
        {"id": items[2]["id"], "revision": 0, "status": "confirmed", "strategy": {"mode": "partial", "prefix": 3, "suffix": 4}}])
    project.complete(identifier)
    result = export_project(project, [identifier], formats=["txt"])
    text = (Path(result["path"]) / "copies/document-001.txt").read_text(encoding="utf-8")
    assert text.count("13800138000") == 1
    assert "139****9000" in text


def test_backup_restore_and_tamper(project, imported, tmp_path):
    identifier = imported()
    project.scan(identifier); confirm_all(project, identifier)
    backup = tmp_path / "backup.zip"
    backup_project(project, backup)
    restored = Project(restore_project(backup, tmp_path))
    try:
        assert restored.findings(identifier)[0]["status"] == "confirmed"
        assert restored.verify_source(identifier).is_file()
    finally:
        restored.close()
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../escape", "bad")
    with pytest.raises(MingjianError):
        restore_project(bad, tmp_path)


def test_atomic_review_failure_rollback(project, imported, monkeypatch):
    identifier = imported()
    project.scan(identifier)
    with project.db.connect() as db:
        db.execute("CREATE TRIGGER fail_review BEFORE INSERT ON review_actions BEGIN SELECT RAISE(ABORT,'injected disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        confirm_all(project, identifier)
    assert all(f["status"] == "pending" for f in project.findings(identifier))


def test_cancel_and_limits(project, imported, tmp_path):
    identifier = imported()
    event = threading.Event(); event.set()
    with pytest.raises(Cancelled):
        project.scan(identifier, cancel=event)
    assert project.db.one("SELECT status FROM scan_runs")["status"] == "cancelled"
    with pytest.raises(MingjianError):
        project.import_preview([tmp_path / "x"] * 201)
    with pytest.raises(MingjianError):
        project.save_limits(Limits(max_mib=1))


def test_incomplete_tasks_recovered(project):
    project.db.write(lambda db: db.execute("INSERT INTO tasks(id,kind,state,created_at) VALUES('task','test','running','now')"))
    path = project.root
    project.close()
    p = Project(path)
    try:
        assert p.db.one("SELECT state FROM tasks WHERE id='task'")["state"] == "interrupted"
    finally:
        p.close()
