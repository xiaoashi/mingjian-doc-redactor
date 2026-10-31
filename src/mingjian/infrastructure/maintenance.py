from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from mingjian.domain.models import MingjianError, SCHEMA_VERSION, dumps, now, uid
from mingjian.services.project import file_hash


def backup_project(project, output):
    target = Path(output).resolve()
    if target.exists():
        raise MingjianError("备份目标已存在，请使用新文件名")
    if target.is_relative_to(project.root):
        raise MingjianError("备份请保存到项目目录之外")
    with project.lock, tempfile.TemporaryDirectory(prefix="mingjian-backup-") as tmp:
        project.ensure_editable()
        folder = Path(tmp)
        project.db.backup(folder / "project.sqlite3")
        for name in ("project.json", "sources", "rules", "parsed", "trash"):
            source = project.root / name
            if source.is_dir():
                shutil.copytree(source, folder / name)
            elif source.exists():
                shutil.copyfile(source, folder / name)
        files = {str(p.relative_to(folder)): file_hash(p) for p in folder.rglob("*") if p.is_file()}
        (folder / "backup-manifest.json").write_text(dumps({"schema": SCHEMA_VERSION, "created_at": now(), "files": files}), encoding="utf-8")
        pending = target.with_name(".pending-" + uid() + ".zip")
        try:
            with zipfile.ZipFile(pending, "w", zipfile.ZIP_DEFLATED) as archive:
                for p in folder.rglob("*"):
                    if p.is_file():
                        archive.write(p, str(p.relative_to(folder)))
            pending.rename(target)
        finally:
            pending.unlink(missing_ok=True)
    return str(target)


def restore_project(archive_path, parent):
    target = Path(parent).resolve() / ("mingjian-project-" + uid())
    pending = target.with_name(".restore-" + uid())
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) > 100_000 or sum(e.file_size for e in entries) > 20 * 1024**3:
                raise MingjianError("备份解压资源超限")
            names = set()
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in entry.filename or ":" in entry.filename:
                    raise MingjianError("备份包含不安全路径")
                if entry.filename in names or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise MingjianError("备份包含重复路径或符号链接")
                names.add(entry.filename)
            manifest = json.loads(archive.read("backup-manifest.json"))
            if manifest["schema"] != SCHEMA_VERSION:
                raise MingjianError("备份 schema 版本不受支持")
            declared = set(manifest["files"])
            actual = {e.filename for e in entries if not e.is_dir()} - {"backup-manifest.json"}
            if declared != actual or not {"project.sqlite3", "project.json"} <= declared:
                raise MingjianError("备份清单与文件不一致")
            pending.mkdir(parents=True)
            archive.extractall(pending)
        for relative, sha in manifest["files"].items():
            if file_hash(pending / relative) != sha:
                raise MingjianError("备份摘要校验失败")
        db = sqlite3.connect(pending / "project.sqlite3")
        try:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise MingjianError("备份数据库完整性或版本检查失败")
        finally:
            db.close()
        (pending / "backup-manifest.json").unlink()
        for name in ("cache", "exports", "backups", "logs"):
            (pending / name).mkdir(exist_ok=True)
        pending.rename(target)
        return str(target)
    except Exception:
        shutil.rmtree(pending, ignore_errors=True)
        raise


def clear_cache(project):
    with project.lock:
        project.ensure_editable()
        cache = project.root / "cache"
        shutil.rmtree(cache, ignore_errors=True)
        cache.mkdir(exist_ok=True)
