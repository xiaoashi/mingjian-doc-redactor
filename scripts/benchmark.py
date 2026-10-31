"""Frozen-corpus exact span/category comparison; never discards failing samples."""
import json
import platform
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mingjian.domain.models import default_config, dumps, now
from mingjian.engines.rules import scan
from mingjian.parsers.documents import parse_file
from mingjian.services.project import file_hash


def benchmark():
    root = Path(__file__).resolve().parents[1]
    truth_path = root / "fixtures" / "annotations.json"
    source = json.loads(truth_path.read_text(encoding="utf-8"))
    totals = {r: Counter() for r in ("phone", "identity", "email", "bank", "word:acceptance-secret")}
    config = default_config()
    config["words"] = source["config"]["words"]
    records = []
    start = time.perf_counter()
    for document in source["documents"]:
        path = root / "fixtures" / document["path"]
        if file_hash(path) != document["sha256"]:
            raise RuntimeError("冻结样本摘要变化：" + document["path"])
        beginning = time.perf_counter()
        parsed = parse_file(path)
        findings, errors, excluded = scan(parsed.blocks, config, document["path"])
        orders = {b.id: i for i, b in enumerate(parsed.blocks)}
        actual = {(orders[f.block_id], f.start, f.end, f.rule_id) for f in findings}
        expected = {(f["block_order"], f["start"], f["end"], f["rule_id"]) for f in document["truth"]}
        for rule_id in totals:
            actual_type = {f for f in actual if f[3] == rule_id}
            expected_type = {f for f in expected if f[3] == rule_id}
            totals[rule_id].update(tp=len(actual_type & expected_type), fp=len(actual_type - expected_type), fn=len(expected_type - actual_type))
        records.append({"path": document["path"], "seconds": time.perf_counter()-beginning,
            "expected": len(expected), "actual": len(actual), "exact_match": actual == expected,
            "missing": sorted(expected-actual), "unexpected": sorted(actual-expected), "errors": errors})
    metrics = {}
    for rule_id, count in totals.items():
        precision = count["tp"] / (count["tp"] + count["fp"]) if count["tp"] + count["fp"] else None
        recall = count["tp"] / (count["tp"] + count["fn"]) if count["tp"] + count["fn"] else None
        metrics[rule_id] = {**count, "precision": precision, "recall": recall}
    result = {"executed_at": now(), "environment": platform.platform(), "python": sys.version,
              "truth_sha256": file_hash(truth_path), "documents": len(records), "total_seconds": time.perf_counter()-start,
              "metrics": metrics, "records": records,
              "limitations": "固定合成模板回归集；标注未经独立人工审定；不外推真实文档准确率；非最终独立验收。"}
    (root / "docs" / "识别回归实测.json").write_text(dumps(result), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "records"}, ensure_ascii=False, indent=2))
    return 0 if all(r["exact_match"] and not r["errors"] for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(benchmark())
