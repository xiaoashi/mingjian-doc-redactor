from mingjian.domain.models import MingjianError, digest


def validate_strategy(strategy):
    mode = strategy.get("mode", "full")
    if mode not in ("full", "partial", "fixed"):
        raise MingjianError("不支持的脱敏策略")
    if mode == "partial":
        for key in ("prefix", "suffix"):
            if type(strategy.get(key, 0)) is not int or not 0 <= strategy.get(key, 0) <= 100:
                raise MingjianError("保留字符数须为 0—100 整数")
    if mode == "fixed" and not 1 <= len(strategy.get("text", "")) <= 500:
        raise MingjianError("固定替换文本须为 1—500 字")


def replacement(value, category, strategy):
    validate_strategy(strategy)
    mode = strategy.get("mode", "full")
    if mode == "fixed":
        return strategy["text"]
    if mode == "partial":
        prefix, suffix = strategy.get("prefix", 0), strategy.get("suffix", 0)
        if prefix + suffix >= len(value):
            raise MingjianError("部分隐藏至少需要隐藏一个字符，请减少保留字符数")
        return value[:prefix] + "*" * (len(value) - prefix - suffix) + (value[-suffix:] if suffix else "")
    return f"[{category}已脱敏]"


def make_plan(blocks, findings):
    result, plan, conflicts = {}, [], []
    for block in blocks:
        candidates = []
        for item in findings:
            if item["block_id"] != block.id or item["status"] != "confirmed":
                continue
            start, end = item["start_offset"], item["end_offset"]
            if not 0 <= start < end <= len(block.text):
                raise MingjianError("命中位置失效，请重新识别")
            if item.get("text_hash") and item["text_hash"] != digest(block.text):
                raise MingjianError("文本版本失效，禁止套用旧定位")
            strategy = item["strategy"]
            candidates.append({"start": start, "end": end, "replacement": replacement(block.text[start:end], item["category"], strategy),
                "mode": strategy.get("mode", "full"), "id": item["id"], "block_id": block.id})
        accepted = []
        priorities = {f["id"]: (f.get("priority", 100), f.get("rule_id", "")) for f in findings}
        for item in sorted(candidates, key=lambda x: (x["start"], -x["end"], x["mode"] != "full", priorities[x["id"]], x["id"])):
            overlaps = [old for old in accepted if item["start"] < old["end"] and old["start"] < item["end"]]
            if not overlaps:
                accepted.append(item)
                continue
            if len(overlaps) == 1 and (item["start"], item["end"]) == (overlaps[0]["start"], overlaps[0]["end"]):
                old = overlaps[0]
                if item["mode"] == "full" and old["mode"] != "full":
                    accepted.remove(old)
                    accepted.append(item)
                elif old["mode"] == "full":
                    continue
                elif item["replacement"] != old["replacement"]:
                    conflicts.append([old["id"], item["id"]])
            else:
                conflicts.append([old["id"] for old in overlaps] + [item["id"]])
        text = block.text
        for item in sorted(accepted, key=lambda x: x["start"], reverse=True):
            text = text[:item["start"]] + item["replacement"] + text[item["end"]:]
        result[block.id] = text
        plan.extend(accepted)
    return {"texts": result, "plan": plan, "conflicts": conflicts}
