from __future__ import annotations

import json
import shutil
import threading
from dataclasses import asdict
from contextlib import contextmanager
from pathlib import Path

from mingjian.domain.models import (Block, Cancelled, Limits, MingjianError, PARSER_VERSION, SCHEMA_VERSION,
                                    default_config, digest, dumps, now, uid)
from mingjian.engines.redaction import make_plan, validate_strategy
from mingjian.engines.rules import scan, validate_config
from mingjian.infrastructure.lock import ProjectLock
from mingjian.parsers.documents import check_zip, checkpoint, parse_file
from mingjian.repositories.database import Database


def file_hash(path):
    import hashlib
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Project:
    def __init__(self, root, create_name=None):
        self.root = Path(root).resolve()
        self.lock = threading.RLock()
        self.frozen = False
        self.active_operations = 0
        self.document_locks = {}
        self.undo_stack, self.redo_stack = {}, {}
        self.limits = Limits()
        if create_name is not None:
            self.validate_name(create_name)
            self.root.mkdir(parents=True, exist_ok=False)
            self.process_lock = ProjectLock(self.root)
            for name in ("sources", "parsed", "rules", "cache", "exports", "backups", "trash", "logs"):
                (self.root / name).mkdir()
            self.db = Database(self.root / "project.sqlite3", create=True)
            project_id = uid()
            self.db.write(lambda db: db.execute("INSERT INTO projects(id,name,created_at,updated_at,schema_version) VALUES(?,?,?,?,?)",
                                               (project_id, create_name.strip(), now(), now(), SCHEMA_VERSION)))
            (self.root / "project.json").write_text(dumps({"id": project_id, "schema_version": SCHEMA_VERSION}), encoding="utf-8")
            self.publish_rules(default_config())
        else:
            if not (self.root / "project.sqlite3").is_file() or not (self.root / "project.json").is_file():
                raise MingjianError("所选目录不是明鉴项目")
            self.process_lock = ProjectLock(self.root)
            self.db = Database(self.root / "project.sqlite3")
        self.info = self.db.one("SELECT * FROM projects")
        if not self.info:
            raise MingjianError("项目记录缺失")
        self.db.write(lambda db: db.execute("UPDATE projects SET updated_at=?", (now(),)))
        self.db.write(lambda db: db.execute("UPDATE tasks SET state='interrupted',finished_at=? WHERE state IN ('queued','running','cancel_requested')", (now(),)))
        self.db.write(lambda db: db.execute("UPDATE scan_runs SET status='interrupted',finished_at=? WHERE status='running'", (now(),)))
        self.db.write(lambda db: db.execute("UPDATE documents SET parse_status='failed',error='上次解析中断，请重新解析' WHERE parse_status='running'"))
        self.db.write(lambda db: db.execute("UPDATE export_records SET status='interrupted',error='上次导出中断，未发布成品' WHERE status='running'"))
        setting = self.db.one("SELECT value_json FROM settings WHERE key='limits'")
        if setting:
            self.limits = Limits(**json.loads(setting["value_json"]))
        # Recovery never silently continues a partial export.
        for folder in (self.root / "exports").glob(".pending-*"):
            shutil.rmtree(folder, ignore_errors=True)

    @staticmethod
    def create(parent, name):
        return Project(Path(parent) / ("mingjian-project-" + uid()), name)

    @staticmethod
    def validate_name(name):
        if not 1 <= len(name.strip()) <= 80:
            raise MingjianError("项目名称须为 1—80 个非空白字符")

    def ensure_editable(self):
        if self.frozen:
            raise MingjianError("正在导出，项目复核暂时冻结，请等待任务结束")
        if self.active_operations:
            raise MingjianError("正在解析或识别，请等待后台任务结束后修改项目")

    @contextmanager
    def document_operation(self, document_id):
        with self.lock:
            if self.frozen:
                raise MingjianError("导出期间不能解析或识别")
            mutex = self.document_locks.setdefault(document_id, threading.Lock())
            if not mutex.acquire(blocking=False):
                raise MingjianError("同一文档已有解析或识别任务")
            self.active_operations += 1
        try:
            yield
        finally:
            with self.lock:
                self.active_operations -= 1
                mutex.release()

    def rename(self, name):
        self.validate_name(name)
        with self.lock:
            self.ensure_editable()
            self.db.write(lambda db: db.execute("UPDATE projects SET name=?,updated_at=?", (name.strip(), now())))
            self.info["name"] = name.strip()

    def archive(self, archived=True):
        with self.lock:
            self.ensure_editable()
            self.db.write(lambda db: db.execute("UPDATE projects SET archived=?,updated_at=?", (int(archived), now())))

    def documents(self, removed=False):
        return self.db.rows("SELECT * FROM documents WHERE removed=? ORDER BY imported_at,id", (int(removed),))

    def document(self, document_id):
        item = self.db.one("SELECT * FROM documents WHERE id=? AND removed=0", (document_id,))
        if not item:
            raise MingjianError("文档不存在或已移入回收区")
        return item

    def safe_path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise MingjianError("项目路径越界")
        return path

    def verify_source(self, document_id):
        doc = self.document(document_id)
        path = self.safe_path(doc["source_path"])
        if not path.is_file() or file_hash(path) != doc["sha256"]:
            self.db.write(lambda db: db.execute("UPDATE documents SET error='源文件异常',review_status='needs_review' WHERE id=?", (document_id,)))
            raise MingjianError("源快照缺失或摘要变化，禁止使用旧定位；请重新导入正确副本")
        return path

    def import_preview(self, paths):
        if len(paths) > self.limits.max_files:
            raise MingjianError(f"单次最多导入 {self.limits.max_files} 个文件，请分批")
        known = {d["sha256"] for d in self.documents()}
        removed_hashes = {d["sha256"] for d in self.documents(True)}
        items = []
        for path in paths:
            path = Path(path).expanduser().resolve()
            item = {"path": str(path), "name": path.name, "size": 0, "status": "ready", "reason": ""}
            try:
                if path.suffix.lower() not in (".docx", ".pdf", ".txt", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff") or not path.is_file():
                    raise MingjianError("不支持的格式或不是常规文件")
                item["size"] = path.stat().st_size
                if item["size"] > self.limits.max_mib * 1024 * 1024:
                    raise MingjianError("文件大小超限")
                if path.suffix.lower() == ".docx":
                    check_zip(path, self.limits)
                if path.suffix.lower() == ".pdf":
                    with path.open("rb") as f:
                        if not f.read(1024).lstrip().startswith(b"%PDF-"):
                            raise MingjianError("实际文件类型不是 PDF")
                if path.suffix.lower() == ".txt":
                    with path.open("rb") as f:
                        prefix = f.read(4096)
                    if prefix.startswith((b"PK\x03\x04", b"%PDF-")):
                        raise MingjianError("TXT 扩展名与实际格式不符")
                if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"):
                    from PIL import Image
                    with Image.open(path) as image:
                        if image.width * image.height > 60_000_000:
                            raise MingjianError("图片超过 6000 万像素，请缩小后导入")
                        expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".tif": "TIFF", ".tiff": "TIFF"}
                        if image.format != expected[path.suffix.lower()]:
                            raise MingjianError("图片扩展名与实际格式不符")
                        image.verify()
                item["sha256"] = file_hash(path)
                if item["sha256"] in known:
                    item.update(status="duplicate", reason="相同内容已存在，跳过")
                elif item["sha256"] in removed_hashes:
                    item.update(status="duplicate", reason="相同内容在回收区，请先恢复")
                else:
                    known.add(item["sha256"])
            except Exception as exc:
                item.update(status="rejected", reason=str(exc) if isinstance(exc, MingjianError) else "文件无法读取")
            items.append(item)
        return items

    def import_files(self, preview, cancel=None, progress=None):
        with self.lock:
            self.ensure_editable()
            imported, failures = [], []
            for index, item in enumerate(preview):
                checkpoint(cancel)
                if item["status"] != "ready":
                    continue
                document_id = uid()
                folder = self.root / "sources" / document_id
                try:
                    folder.mkdir()
                    path = Path(item["path"])
                    relative = f"sources/{document_id}/original{path.suffix.lower()}"
                    target = self.root / relative
                    shutil.copyfile(path, target)
                    if file_hash(target) != item["sha256"]:
                        raise MingjianError("文件在预览后发生变化，请重新导入")
                    self.db.write(lambda db: db.execute("""INSERT INTO documents
                        (id,project_id,display_name,original_path,source_path,sha256,size,format,imported_at)
                        VALUES(?,?,?,?,?,?,?,?,?)""", (document_id, self.info["id"], item["name"], item["path"], relative,
                        item["sha256"], item["size"], path.suffix[1:].lower(), now())))
                    imported.append(document_id)
                except Exception as exc:
                    shutil.rmtree(folder, ignore_errors=True)
                    failures.append({"name": item["name"], "reason": str(exc) if isinstance(exc, MingjianError) else "快照复制或保存失败"})
                if progress:
                    progress(index + 1, len(preview), "导入快照")
            return {"imported": imported, "failures": failures, "skipped": [p for p in preview if p["status"] != "ready"]}

    def remove(self, document_id, restore=False):
        with self.lock:
            self.ensure_editable()
            doc = self.db.one("SELECT * FROM documents WHERE id=?", (document_id,))
            if not doc:
                raise MingjianError("文档不存在")
            source = self.safe_path(doc["source_path"]).parent
            target = self.root / ("sources" if restore else "trash") / document_id
            target.parent.mkdir(exist_ok=True)
            if source != target:
                shutil.move(str(source), str(target))
            try:
                relative = str((target / Path(doc["source_path"]).name).relative_to(self.root))
                self.db.write(lambda db: db.execute("UPDATE documents SET removed=?,source_path=? WHERE id=?", (0 if restore else 1, relative, document_id)))
            except Exception:
                if source != target:
                    shutil.move(str(target), str(source))
                raise

    def parse(self, document_id, encoding=None, cancel=None, parser=None, parser_version=PARSER_VERSION):
        with self.document_operation(document_id):
            path = self.verify_source(document_id)
            self.db.write(lambda db: db.execute("UPDATE documents SET parse_status='running',review_status='needs_review',error=NULL WHERE id=?", (document_id,)))
            try:
                parsed = parser(path, self.limits, cancel) if parser else parse_file(path, self.limits, encoding, cancel)
                checkpoint(cancel)
                parse_id = uid()
                text_hash = digest(dumps([b.text for b in parsed.blocks]))
                def save(db):
                    db.execute("INSERT INTO parse_versions VALUES(?,?,?,?,?,?,?,?,0)", (parse_id, document_id, parser_version,
                        text_hash, parsed.coverage, dumps(parsed.warnings), dumps(parsed.metadata), now()))
                    for i, block in enumerate(parsed.blocks):
                        db.execute("INSERT INTO text_blocks VALUES(?,?,?,?,?,?,?)", (block.id, parse_id, block.kind, i,
                                   dumps(block.locator), block.text, digest(block.text)))
                    db.execute("UPDATE documents SET current_parse_id=?,current_run_id=NULL,parse_status=?,review_status='not_started',revision=revision+1,error=NULL WHERE id=?",
                               (parse_id, "partial" if parsed.warnings else "success", document_id))
                self.db.write(save)
                folder = self.root / "parsed" / document_id
                folder.mkdir(exist_ok=True)
                (folder / f"{parse_id}.json").write_text(dumps({"id": parse_id, "text_hash": text_hash,
                    "blocks": [b.to_dict() for b in parsed.blocks], "warnings": parsed.warnings}), encoding="utf-8")
                self.undo_stack.pop(document_id, None)
                self.redo_stack.pop(document_id, None)
                return parse_id
            except Exception as exc:
                error = str(exc) if isinstance(exc, MingjianError) else f"解析失败（{type(exc).__name__}），请检查文件完整性"
                state = "cancelled" if isinstance(exc, Cancelled) else "failed"
                self.db.write(lambda db: db.execute("UPDATE documents SET parse_status=?,error=?,current_run_id=NULL WHERE id=?", (state, error, document_id)))
                raise MingjianError(error) from exc

    def parsed(self, document_id):
        doc = self.document(document_id)
        if doc["parse_status"] not in ("success", "partial"):
            raise MingjianError(doc["error"] or "请先成功解析文档")
        version = self.db.one("SELECT * FROM parse_versions WHERE id=?", (doc["current_parse_id"],))
        rows = self.db.rows("SELECT * FROM text_blocks WHERE parse_id=? ORDER BY order_index", (version["id"],))
        blocks = [Block(row["text"], row["kind"], json.loads(row["locator_json"]), row["id"]) for row in rows]
        for block, row in zip(blocks, rows):
            if digest(block.text) != row["text_hash"]:
                raise MingjianError("解析正文摘要异常，请重新解析")
        if digest(dumps([b.text for b in blocks])) != version["text_hash"]:
            raise MingjianError("解析版本摘要异常，请重新解析")
        return version, blocks

    def acknowledge_coverage(self, document_id, acknowledge=True):
        with self.lock:
            self.ensure_editable()
            doc = self.document(document_id)
            version, _ = self.parsed(document_id)
            before = {"coverage_ack": version["coverage_ack"]}
            after = {"coverage_ack": int(acknowledge)}
            def save(db):
                db.execute("UPDATE parse_versions SET coverage_ack=? WHERE id=?", (int(acknowledge), version["id"]))
                db.execute("UPDATE documents SET review_status=CASE WHEN review_status='completed' THEN 'needs_review' ELSE review_status END,revision=revision+1 WHERE id=?", (document_id,))
                db.execute("INSERT INTO review_actions VALUES(?,?,?,?,?,?,?)", (uid(), document_id, None, "coverage", dumps(before), dumps(after), now()))
            self.db.write(save)
            self._remember(document_id, {"kind": "coverage", "parse_id": doc["current_parse_id"], "before": before, "after": after})

    def publish_rules(self, config, name="本地规则方案"):
        with self.lock:
            self.ensure_editable()
            validate_config(config)
            snapshot_id = uid()
            version = (self.db.one("SELECT max(version) AS version FROM rule_snapshots")["version"] or 0) + 1
            text = dumps(config)
            self.db.write(lambda db: db.execute("INSERT INTO rule_snapshots VALUES(?,?,?,?,?,?)", (snapshot_id, name, version, digest(text), text, now())))
            (self.root / "rules" / f"{snapshot_id}.json").write_text(text, encoding="utf-8")
            return snapshot_id

    def rules(self):
        row = self.db.one("SELECT * FROM rule_snapshots ORDER BY version DESC LIMIT 1")
        if digest(row["config_json"]) != row["content_hash"]:
            raise MingjianError("规则快照摘要异常")
        return row, json.loads(row["config_json"])

    def scan(self, document_id, snapshot_id=None, cancel=None):
        with self.document_operation(document_id):
            self.verify_source(document_id)
            version, blocks = self.parsed(document_id)
            if version["coverage_status"] == "partial" and not version["coverage_ack"]:
                raise MingjianError("存在覆盖限制，请先逐文档确认覆盖说明")
            snapshot = self.db.one("SELECT * FROM rule_snapshots WHERE id=?", (snapshot_id,)) if snapshot_id else self.rules()[0]
            if not snapshot or digest(snapshot["config_json"]) != snapshot["content_hash"]:
                raise MingjianError("规则快照异常")
            run_id = uid()
            self.db.write(lambda db: db.execute("INSERT INTO scan_runs(id,document_id,parse_id,snapshot_id,status,started_at) VALUES(?,?,?,?,'running',?)",
                                               (run_id, document_id, version["id"], snapshot["id"], now())))
            self.db.write(lambda db: db.execute("UPDATE documents SET current_run_id=?,review_status='needs_review' WHERE id=?", (run_id, document_id)))
            try:
                candidates, errors, excluded = scan(blocks, json.loads(snapshot["config_json"]), document_id, cancel)
                checkpoint(cancel)
                groups = {}
                for block in blocks:
                    ends, group = -1, None
                    for item in sorted([c for c in candidates if c.block_id == block.id], key=lambda c: (c.start, c.end)):
                        if item.start >= ends:
                            group = uid()
                        ends = max(ends, item.end)
                        groups[id(item)] = group
                block_map = {b.id: b for b in blocks}
                def save(db):
                    for item in candidates:
                        fingerprint = digest(dumps([version["text_hash"], item.block_id, item.start, item.end,
                            digest(block_map[item.block_id].text[item.start:item.end]), item.rule_id, snapshot["content_hash"]]))
                        db.execute("""INSERT OR IGNORE INTO findings
                            (id,run_id,block_id,start_offset,end_offset,group_id,rule_id,category,risk,evidence,suggestion,fingerprint,priority)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (uid(), run_id, item.block_id, item.start, item.end, groups[id(item)],
                            item.rule_id, item.category, item.risk, item.evidence, item.suggestion, fingerprint, item.priority))
                    db.execute("UPDATE scan_runs SET status=?,finished_at=?,errors_json=?,excluded_count=? WHERE id=?",
                               ("partial_failure" if errors else "completed", now(), dumps(errors), excluded, run_id))
                    db.execute("UPDATE documents SET current_run_id=?,review_status='in_review',revision=revision+1 WHERE id=?", (run_id, document_id))
                self.db.write(save)
                self.undo_stack.pop(document_id, None)
                self.redo_stack.pop(document_id, None)
                return run_id
            except Exception as exc:
                state = "cancelled" if isinstance(exc, Cancelled) else "failed"
                details = dumps([{"error_type": type(exc).__name__}])
                self.db.write(lambda db: db.execute("UPDATE scan_runs SET status=?,finished_at=?,errors_json=? WHERE id=?", (state, now(), details, run_id)))
                raise

    def findings(self, document_id):
        doc = self.document(document_id)
        rows = self.db.rows("""SELECT f.*,b.text_hash,b.order_index,b.locator_json FROM findings f JOIN text_blocks b ON b.id=f.block_id
            WHERE run_id=? ORDER BY b.order_index,f.start_offset,f.end_offset,f.priority""", (doc["current_run_id"],))
        for row in rows:
            row["strategy"] = json.loads(row["strategy_json"])
        return rows

    def _remember(self, document_id, action):
        stack = self.undo_stack.setdefault(document_id, [])
        stack.append(action)
        del stack[:-100]
        self.redo_stack[document_id] = []

    def review_many(self, document_id, updates):
        with self.lock:
            self.ensure_editable()
            self.verify_source(document_id)
            doc = self.document(document_id)
            current = {f["id"]: f for f in self.findings(document_id)}
            changes = []
            keys = ("status", "strategy", "note", "reason")
            for update in updates:
                item = current.get(update["id"])
                if not item or update.get("revision") != item["revision"]:
                    raise MingjianError("复核版本冲突，请刷新后重新操作")
                after = {key: update.get(key, item[key]) for key in keys}
                if after["status"] not in ("pending", "confirmed", "ignored"):
                    raise MingjianError("复核状态无效")
                if after["status"] == "ignored" and not 1 <= len(after["reason"].strip()) <= 500:
                    raise MingjianError("忽略原因须为 1—500 字")
                if len(after["note"]) > 500:
                    raise MingjianError("备注最多 500 字")
                validate_strategy(after["strategy"])
                if after["strategy"].get("mode") == "partial" and (after["strategy"].get("prefix", 0) + after["strategy"].get("suffix", 0) >= item["end_offset"] - item["start_offset"]):
                    raise MingjianError("部分隐藏至少需要隐藏一个字符，请减少保留字符数")
                changes.append({"id": item["id"], "revision": item["revision"], "before": {k: item[k] for k in keys}, "after": after})
            self._save_changes(document_id, changes, "review")
            self._remember(document_id, {"kind": "findings", "run_id": doc["current_run_id"], "changes": changes})

    def _save_changes(self, document_id, changes, action):
        def save(db):
            for change in changes:
                value = change["after"]
                cursor = db.execute("UPDATE findings SET status=?,strategy_json=?,note=?,reason=?,revision=revision+1 WHERE id=? AND revision=?",
                    (value["status"], dumps(value["strategy"]), value["note"], value["reason"], change["id"], change["revision"]))
                if cursor.rowcount != 1:
                    raise MingjianError("复核版本冲突，请刷新")
                db.execute("INSERT INTO review_actions VALUES(?,?,?,?,?,?,?)", (uid(), document_id, change["id"], action,
                    dumps(change["before"]), dumps(value), now()))
            db.execute("UPDATE documents SET review_status=CASE WHEN review_status='completed' THEN 'needs_review' ELSE 'in_review' END,revision=revision+1 WHERE id=?", (document_id,))
        self.db.write(save)

    def undo(self, document_id, redo=False):
        with self.lock:
            self.ensure_editable()
            source = self.redo_stack if redo else self.undo_stack
            target = self.undo_stack if redo else self.redo_stack
            stack = source.setdefault(document_id, [])
            if not stack:
                raise MingjianError("没有可重做的操作" if redo else "没有可撤销的操作")
            action = stack[-1]
            doc = self.document(document_id)
            if action["kind"] == "coverage":
                if action["parse_id"] != doc["current_parse_id"]:
                    raise MingjianError("解析版本已变更")
                value = action["after"] if redo else action["before"]
                def save(db):
                    db.execute("UPDATE parse_versions SET coverage_ack=? WHERE id=?", (value["coverage_ack"], action["parse_id"]))
                    db.execute("UPDATE documents SET review_status='needs_review',revision=revision+1 WHERE id=?", (document_id,))
                    db.execute("INSERT INTO review_actions VALUES(?,?,?,?,?,?,?)", (uid(), document_id, None, "redo" if redo else "undo", dumps(action), dumps(value), now()))
                self.db.write(save)
            else:
                if action["run_id"] != doc["current_run_id"]:
                    raise MingjianError("识别批次已变更，无法撤销历史批次")
                current = {f["id"]: f for f in self.findings(document_id)}
                changes = [{"id": c["id"], "revision": current[c["id"]]["revision"],
                    "before": {k: current[c["id"]][k] for k in ("status", "strategy", "note", "reason")},
                    "after": c["after"] if redo else c["before"]} for c in action["changes"]]
                self._save_changes(document_id, changes, "redo" if redo else "undo")
            stack.pop()
            target.setdefault(document_id, []).append(action)

    def preview(self, document_id):
        self.verify_source(document_id)
        _, blocks = self.parsed(document_id)
        return make_plan(blocks, self.findings(document_id))

    def complete(self, document_id):
        with self.lock:
            self.ensure_editable()
            self.verify_source(document_id)
            doc = self.document(document_id)
            version, _ = self.parsed(document_id)
            run = self.db.one("SELECT * FROM scan_runs WHERE id=?", (doc["current_run_id"],))
            if not run or run["status"] != "completed" or run["parse_id"] != version["id"] or json.loads(run["errors_json"]):
                raise MingjianError("识别尚未完整成功，请先执行识别并处理规则失败")
            if version["coverage_status"] == "partial" and not version["coverage_ack"]:
                raise MingjianError("覆盖限制尚未确认")
            if any(f["status"] == "pending" for f in self.findings(document_id)):
                raise MingjianError("仍有待复核问题，请逐项处理")
            if self.preview(document_id)["conflicts"]:
                raise MingjianError("脱敏区间存在冲突，请保留一个确认项并对其他项填写忽略原因")
            self.db.write(lambda db: db.execute("UPDATE documents SET review_status='completed',revision=revision+1 WHERE id=?", (document_id,)))

    def save_limits(self, limits):
        limits.validate()
        with self.lock:
            self.ensure_editable()
            self.db.write(lambda db: db.execute("INSERT OR REPLACE INTO settings VALUES('limits',?)", (dumps(asdict(limits)),)))
            self.limits = limits

    def close(self):
        self.db.close()
        self.process_lock.close()
