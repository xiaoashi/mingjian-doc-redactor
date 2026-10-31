import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from mingjian.domain.models import MingjianError
from mingjian.services.export import export_project
from mingjian.services.project import Project
from conftest import confirm_all


def test_committed_review_survives_forced_process_exit(tmp_path):
    project = Project.create(tmp_path, "强制结束合成验证")
    source = tmp_path / "source.txt"
    source.write_text("联系电话13800138000", encoding="utf-8")
    identifier = project.import_files(project.import_preview([source]))["imported"][0]
    project.parse(identifier); project.scan(identifier)
    path = project.root
    project.close()
    script = """
import os,sys
from mingjian.services.project import Project
p=Project(sys.argv[1]);d=sys.argv[2]
f=p.findings(d)[0]
p.review_many(d,[{'id':f['id'],'revision':f['revision'],'status':'confirmed'}])
os._exit(23)
"""
    result = subprocess.run([sys.executable, "-c", script, str(path), identifier], timeout=10, env=os.environ.copy())
    assert result.returncode == 23
    restored = Project(path)
    try:
        assert restored.findings(identifier)[0]["status"] == "confirmed"
        assert restored.db.one("PRAGMA integrity_check")["integrity_check"] == "ok"
    finally:
        restored.close()


def test_partial_coverage_confirmation_and_undo(project, tmp_path):
    from reportlab.pdfgen import canvas
    path = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(path)); c.showPage(); c.save()
    identifier = project.import_files(project.import_preview([path]))["imported"][0]
    project.parse(identifier)
    assert project.document(identifier)["parse_status"] == "partial"
    with pytest.raises(MingjianError):
        project.scan(identifier)
    project.acknowledge_coverage(identifier)
    project.undo(identifier)
    assert not project.parsed(identifier)[0]["coverage_ack"]
    project.undo(identifier, redo=True)
    assert project.parsed(identifier)[0]["coverage_ack"]
    project.scan(identifier); project.complete(identifier)
    result = export_project(project, [identifier])
    text = (Path(result["path"]) / "copies/document-001.txt").read_text(encoding="utf-8")
    assert "部分覆盖" in text and "无可提取文本" in text


def test_export_freezes_edits(project, imported, monkeypatch):
    import mingjian.services.export as module
    identifier = imported()
    project.scan(identifier); confirm_all(project, identifier); project.complete(identifier)
    original = module.WRITERS["txt"]
    attempted = []
    def writer(*args):
        with pytest.raises(MingjianError):
            project.acknowledge_coverage(identifier)
        attempted.append(True)
        return original(*args)
    monkeypatch.setitem(module.WRITERS, "txt", writer)
    result = export_project(project, [identifier], formats=["txt"])
    assert result["validation"]["passed"] and attempted and not project.frozen


def test_second_process_project_lock(project):
    script = """
import sys
from mingjian.services.project import Project
from mingjian.domain.models import MingjianError
try: Project(sys.argv[1])
except MingjianError: sys.exit(13)
sys.exit(1)
"""
    result = subprocess.run([sys.executable, "-c", script, str(project.root)], timeout=10)
    assert result.returncode == 13


def test_export_cancel_cleans_temporary_directory(project, imported):
    identifier = imported()
    project.scan(identifier); confirm_all(project, identifier); project.complete(identifier)
    cancel = threading.Event(); cancel.set()
    with pytest.raises(MingjianError):
        export_project(project, [identifier], cancel=cancel)
    assert not project.frozen
    assert not list((project.root / "exports").iterdir())


def test_document_parallelism_and_mutual_exclusion(project, imported, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import mingjian.services.project as module
    one = imported("第一份 电话13800138000", "one.txt")
    two = imported("第二份 电话13900139000", "two.txt")
    barrier = threading.Barrier(2)
    original = module.scan
    observed = []
    def wait_scan(*args, **kwargs):
        barrier.wait(timeout=3)
        observed.append(project.active_operations)
        with pytest.raises(MingjianError):
            project.rename("任务中不能修改")
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "scan", wait_scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(project.scan, [one, two]))
    assert len(results) == 2 and 2 in observed
    assert project.active_operations == 0
    with project.document_operation(one):
        with pytest.raises(MingjianError):
            project.scan(one)
