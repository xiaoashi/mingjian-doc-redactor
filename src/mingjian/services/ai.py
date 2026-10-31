"""Versioned AI suggestion batches join the existing human review chain."""
import json
from collections import defaultdict

from mingjian.ai.ecnu import EcnuClient, PROMPT_VERSION
from mingjian.domain.models import MingjianError, digest, dumps, now, uid


def augment_with_ai(project, document_id, config, *, consent=False, cancel=None, client=None):
    if not consent:
        raise MingjianError("发送文档文字到大模型前需要本次明确授权")
    with project.document_operation(document_id):
        project.verify_source(document_id)
        doc = project.document(document_id)
        version, blocks = project.parsed(document_id)
        if version["coverage_status"] == "partial" and not version["coverage_ack"]:
            raise MingjianError("请先核对并确认解析/OCR覆盖限制，再请求AI复核")
        old_run = project.db.one("SELECT * FROM scan_runs WHERE id=?", (doc["current_run_id"],))
        if not old_run or old_run["status"] != "completed":
            raise MingjianError("请先完成本地规则识别，再补充AI复核")
        if project.db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_runs'"):
            if project.db.one("SELECT id FROM ai_runs WHERE scan_run_id=?", (old_run["id"],)):
                raise MingjianError("当前批次已有AI建议；如需重跑，请先重新执行本地规则识别")
        provider = client or EcnuClient(config)
        try:
            suggestions = provider.suggest(blocks, cancel=cancel)
            receipt = {"model": config.model, "endpoint": config.base_url, "prompt_version": PROMPT_VERSION,
                       "usage": provider.usage_total, "rejected_suggestions": provider.rejected_suggestions}
        finally:
            if client is None:
                provider.close()
        old = project.findings(document_id)
        new_run_id, ai_id = uid(), uid()
        rows = []
        for item in old:
            rows.append({**item, "id": uid(), "run_id": new_run_id, "revision": 0})
        bmap = {b.id: b for b in blocks}
        for suggestion in suggestions:
            category = suggestion.category
            fingerprint = digest(dumps([version["text_hash"], suggestion.block_id, suggestion.start, suggestion.end,
                                        category, config.model, PROMPT_VERSION]))
            rows.append({"id": uid(), "run_id": new_run_id, "block_id": suggestion.block_id,
                "start_offset": suggestion.start, "end_offset": suggestion.end, "group_id": "", "rule_id": "ai:"+config.model,
                "category": category, "risk": "中", "evidence": "AI辅助建议，需人工确认："+suggestion.reason,
                "suggestion": "核对原文和上下文后确认或忽略；模型判断不是法律风险结论", "status": "pending",
                "strategy_json": dumps({"mode":"full"}), "note":"", "reason":"", "revision":0,
                "fingerprint":fingerprint,"priority":150})
        by_block = defaultdict(list)
        for row in rows:
            by_block[row["block_id"]].append(row)
        for items in by_block.values():
            end, group = -1, None
            for row in sorted(items, key=lambda r:(r["start_offset"],r["end_offset"])):
                if row["start_offset"] >= end:
                    group = uid()
                end = max(end,row["end_offset"])
                row["group_id"] = group
        project.verify_source(document_id)
        def save(db):
            current = db.execute("SELECT revision,current_parse_id,current_run_id FROM documents WHERE id=?",(document_id,)).fetchone()
            if current["revision"] != doc["revision"] or current["current_parse_id"] != version["id"] or current["current_run_id"] != old_run["id"]:
                raise MingjianError("AI请求期间文档版本已变化，已拒绝套用旧建议")
            db.execute("""CREATE TABLE IF NOT EXISTS ai_runs(id TEXT PRIMARY KEY,scan_run_id TEXT NOT NULL REFERENCES scan_runs(id),
                source_run_id TEXT NOT NULL REFERENCES scan_runs(id),document_id TEXT NOT NULL REFERENCES documents(id),
                receipt_json TEXT NOT NULL,created_at TEXT NOT NULL)""")
            db.execute("INSERT INTO scan_runs(id,document_id,parse_id,snapshot_id,status,started_at,finished_at,excluded_count) VALUES(?,?,?,?,'completed',?,?,?)",
                       (new_run_id,document_id,version["id"],old_run["snapshot_id"],now(),now(),old_run["excluded_count"]))
            keys = ("id","run_id","block_id","start_offset","end_offset","group_id","rule_id","category","risk","evidence","suggestion","status","strategy_json","note","reason","revision","fingerprint","priority")
            for row in rows:
                if row["block_id"] not in bmap:
                    raise MingjianError("AI结果包含未知文本块")
                db.execute("INSERT INTO findings("+",".join(keys)+") VALUES("+",".join("?" for _ in keys)+")",tuple(row[k] for k in keys))
            db.execute("INSERT INTO ai_runs VALUES(?,?,?,?,?,?)",(ai_id,new_run_id,old_run["id"],document_id,dumps(receipt),now()))
            db.execute("UPDATE documents SET current_run_id=?,review_status='needs_review',revision=revision+1 WHERE id=?",(new_run_id,document_id))
        project.db.write(save)
        project.undo_stack.pop(document_id,None);project.redo_stack.pop(document_id,None)
        return {"document_id":document_id,"run_id":new_run_id,"suggestions":len(suggestions),"inherited":len(old),"receipt":receipt}


def ai_provenance(project, scan_run_id):
    if not project.db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_runs'"):
        return None
    row = project.db.one("SELECT receipt_json FROM ai_runs WHERE scan_run_id=?",(scan_run_id,))
    return json.loads(row["receipt_json"]) if row else None
