from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QThread, Signal

from mingjian.domain.models import Cancelled, MingjianError, dumps, now, uid
from mingjian.parsers.documents import checkpoint


def run_documents(ids, operation, workers, cancel, progress, stage):
    failures = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mingjian-document") as pool:
        futures = {pool.submit(operation, document_id): document_id for document_id in ids}
        for index, future in enumerate(as_completed(futures), 1):
            try:
                future.result()
            except Exception as exc:
                failures.append({"id": futures[future], "reason": str(exc) if isinstance(exc, MingjianError) else type(exc).__name__})
            progress(index, len(ids), stage)
    checkpoint(cancel)
    return failures


class Worker(QThread):
    progress = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, title, operation, project=None, parent=None):
        super().__init__(parent)
        self.title, self.operation, self.project = title, operation, project
        self.cancel = threading.Event()
        self.task_id = uid()

    def request_cancel(self):
        self.cancel.set()

    def run(self):
        if self.project:
            self.project.db.write(lambda db: db.execute("INSERT INTO tasks(id,kind,state,created_at) VALUES(?,?,'running',?)",
                                                        (self.task_id, self.title, now())))
        try:
            def progress(done, total, stage):
                self.progress.emit(done, total, stage)
                if self.project:
                    self.project.db.write(lambda db: db.execute("UPDATE tasks SET progress=?,total=? WHERE id=?", (done, total, self.task_id)))
            result = self.operation(self.cancel, progress)
            checkpoint(self.cancel)
            state = "partial_failure" if isinstance(result, dict) and result.get("failures") else "success"
            if self.project:
                self.project.db.write(lambda db: db.execute("UPDATE tasks SET state=?,finished_at=?,failures=?,detail_json=? WHERE id=?",
                    (state, now(), len(result.get("failures", [])) if isinstance(result, dict) else 0,
                     dumps({"message": "部分文件失败，请检查文件列表"}) if state != "success" else "{}", self.task_id)))
            self.succeeded.emit(result)
        except Exception as exc:
            message = str(exc) if isinstance(exc, MingjianError) else f"任务失败（{type(exc).__name__}），请检查文件、可用空间及权限"
            state = "cancelled" if isinstance(exc, Cancelled) else "failed"
            details = dumps({"error_type": type(exc).__name__})
            if self.project:
                try:
                    self.project.db.write(lambda db: db.execute("UPDATE tasks SET state=?,finished_at=?,detail_json=? WHERE id=?",
                        (state, now(), details, self.task_id)))
                except Exception:
                    message += "；任务状态也未能保存，请检查磁盘空间和数据库权限"
            self.failed.emit(message)
