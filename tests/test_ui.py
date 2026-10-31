

from mingjian.ui.window import MainWindow


def test_ui_unicode_location_review_and_restore(qtbot, tmp_path, project, imported):
    identifier = imported("😀联系电话：１３８００１３８０００\r\n第二行邮箱：a@example.org")
    project.scan(identifier)
    window = MainWindow(tmp_path / "config")
    qtbot.addWidget(window)
    window.set_project(project)
    window.show()
    window.finding_list.setCurrentRow(0)
    assert window.reader.textCursor().selectedText() == "１３８００１３８０００"
    window.finding_list.setFocus()
    window.review("confirmed")
    assert project.findings(identifier)[0]["status"] == "confirmed"
    window.undo(False)
    assert project.findings(identifier)[0]["status"] == "pending"
    window.undo(True)
    assert project.findings(identifier)[0]["status"] == "confirmed"
    window.finding_list.setCurrentRow(1)
    assert window.reader.textCursor().selectedText() == "a@example.org"


def test_ui_background_worker(qtbot, tmp_path, project):
    window = MainWindow(tmp_path / "config")
    qtbot.addWidget(window)
    window.set_project(project)
    outcome = []
    window.start_task("后台测试", lambda cancel, progress: progress(1, 1, "完成") or {"ok": True}, outcome.append)
    qtbot.waitUntil(lambda: window.worker is None, timeout=5000)
    assert outcome == [{"ok": True}]
    assert project.db.one("SELECT state FROM tasks")["state"] == "success"


def test_ui_unsaved_note_is_flushed_before_switch(qtbot, tmp_path, project, imported):
    one = imported("联系电话13800138000", "one.txt")
    two = imported("联系电话13900139000", "two.txt")
    project.scan(one); project.scan(two)
    window = MainWindow(tmp_path / "config")
    qtbot.addWidget(window)
    window.set_project(project)
    window.note.setFocus()
    qtbot.keyClicks(window.note, "saved note")
    assert window.detail_dirty
    window.document_list.setCurrentRow(1)
    assert project.findings(one)[0]["note"] == "saved note"
    assert window.current_id == two
