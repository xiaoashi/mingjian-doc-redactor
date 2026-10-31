"""One dedicated writer per project; readers never share SQLite connections."""
from __future__ import annotations

import queue
import sqlite3
import threading
from concurrent.futures import Future

from mingjian.domain.models import MingjianError, SCHEMA_VERSION

SCHEMA = """
CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 80),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, schema_version INTEGER NOT NULL, archived INTEGER DEFAULT 0);
CREATE TABLE documents(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), display_name TEXT NOT NULL,
 original_path TEXT NOT NULL, source_path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, format TEXT NOT NULL,
 imported_at TEXT NOT NULL, parse_status TEXT DEFAULT 'pending', review_status TEXT DEFAULT 'not_started',
 current_parse_id TEXT, current_run_id TEXT, revision INTEGER DEFAULT 0, error TEXT, removed INTEGER DEFAULT 0,
 UNIQUE(project_id, sha256));
CREATE TABLE parse_versions(id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id), parser_version TEXT NOT NULL,
 text_hash TEXT NOT NULL, coverage_status TEXT NOT NULL, warnings_json TEXT NOT NULL, metadata_json TEXT NOT NULL,
 created_at TEXT NOT NULL, coverage_ack INTEGER DEFAULT 0);
CREATE TABLE text_blocks(id TEXT PRIMARY KEY, parse_id TEXT NOT NULL REFERENCES parse_versions(id), kind TEXT NOT NULL,
 order_index INTEGER NOT NULL, locator_json TEXT NOT NULL, text TEXT NOT NULL, text_hash TEXT NOT NULL, UNIQUE(parse_id,order_index));
CREATE TABLE rule_snapshots(id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL, content_hash TEXT NOT NULL,
 config_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TRIGGER immutable_rules BEFORE UPDATE ON rule_snapshots BEGIN SELECT RAISE(ABORT,'immutable snapshot'); END;
CREATE TABLE scan_runs(id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id), parse_id TEXT NOT NULL REFERENCES parse_versions(id),
 snapshot_id TEXT NOT NULL REFERENCES rule_snapshots(id), status TEXT NOT NULL, started_at TEXT NOT NULL,
 finished_at TEXT, errors_json TEXT DEFAULT '[]', excluded_count INTEGER DEFAULT 0);
CREATE UNIQUE INDEX active_scan ON scan_runs(document_id) WHERE status='running';
CREATE TABLE findings(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES scan_runs(id), block_id TEXT NOT NULL REFERENCES text_blocks(id),
 start_offset INTEGER NOT NULL CHECK(start_offset>=0), end_offset INTEGER NOT NULL CHECK(end_offset>start_offset), group_id TEXT NOT NULL,
 rule_id TEXT NOT NULL, category TEXT NOT NULL, risk TEXT NOT NULL, evidence TEXT NOT NULL, suggestion TEXT NOT NULL,
 status TEXT DEFAULT 'pending', strategy_json TEXT DEFAULT '{"mode":"full"}', note TEXT DEFAULT '', reason TEXT DEFAULT '',
 revision INTEGER DEFAULT 0, fingerprint TEXT NOT NULL, priority INTEGER NOT NULL, UNIQUE(run_id,fingerprint));
CREATE TABLE review_actions(id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id), finding_id TEXT,
 action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE tasks(id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, progress INTEGER DEFAULT 0,
 total INTEGER DEFAULT 0, failures INTEGER DEFAULT 0, created_at TEXT NOT NULL, finished_at TEXT, detail_json TEXT DEFAULT '{}');
CREATE TABLE export_records(id TEXT PRIMARY KEY, status TEXT NOT NULL, output_path TEXT, created_at TEXT NOT NULL,
 manifest_json TEXT, validation_json TEXT, error TEXT);
CREATE TABLE settings(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE INDEX document_state ON documents(parse_status,review_status);
CREATE INDEX findings_run ON findings(run_id,status);
CREATE INDEX blocks_parse ON text_blocks(parse_id,order_index);
"""


class Database:
    def __init__(self, path, create=False):
        self.path = str(path)
        self.queue = queue.Queue()
        self.closed = False
        if create:
            with self.connect() as db:
                db.executescript("BEGIN IMMEDIATE;" + SCHEMA + f"PRAGMA user_version={SCHEMA_VERSION};COMMIT;")
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise MingjianError("项目数据库版本不受支持，请使用匹配版本的软件")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise MingjianError("项目数据库一致性检查失败，请从备份恢复")
        self.thread = threading.Thread(target=self._writer, daemon=True, name="mingjian-db-writer")
        self.thread.start()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _writer(self):
        db = self.connect()
        try:
            while True:
                item = self.queue.get()
                if item is None:
                    break
                func, future = item
                try:
                    db.execute("BEGIN IMMEDIATE")
                    value = func(db)
                    db.commit()
                    future.set_result(value)
                except Exception as exc:
                    db.rollback()
                    future.set_exception(exc)
        finally:
            db.close()

    def write(self, func):
        if self.closed:
            raise MingjianError("项目数据库已关闭")
        future = Future()
        self.queue.put((func, future))
        return future.result()

    def rows(self, sql, params=()):
        db = self.connect()
        try:
            return [dict(row) for row in db.execute(sql, params)]
        finally:
            db.close()

    def one(self, sql, params=()):
        rows = self.rows(sql, params)
        return rows[0] if rows else None

    def backup(self, target):
        def run(db):
            # Separate read connection; backup must not run inside writer transaction.
            return None
        self.write(run)
        source = self.connect()
        dest = sqlite3.connect(str(target))
        try:
            source.backup(dest)
        finally:
            source.close()
            dest.close()

    def close(self):
        if not self.closed:
            self.closed = True
            self.queue.put(None)
            self.thread.join()
