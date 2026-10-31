from __future__ import annotations

import csv
import io
import re
from datetime import date

from mingjian.domain.models import Candidate, MingjianError, RULE_NAMES, default_config, uid
from mingjian.engines.aho import Automaton
from mingjian.parsers.documents import checkpoint
from mingjian.parsers.mapping import normalize


def identity_valid(value):
    if not re.fullmatch(r"[1-9][0-9]{16}[0-9Xx]", value):
        return False
    try:
        birthday = date(int(value[6:10]), int(value[10:12]), int(value[12:14]))
        if birthday > date.today() or birthday.year < 1800:
            return False
    except ValueError:
        return False
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    return "10X98765432"[sum(int(n) * w for n, w in zip(value[:17], weights)) % 11] == value[-1].upper()


def luhn(value):
    numbers = [int(c) for c in value]
    total = 0
    for i, number in enumerate(reversed(numbers)):
        if i % 2:
            number *= 2
            number = number - 9 if number > 9 else number
        total += number
    return total % 10 == 0 and len(set(value)) > 1


def validate_config(config):
    if not isinstance(config, dict) or not all(k in config for k in ("rules", "words", "whitelist")):
        raise MingjianError("规则方案结构错误")
    if not 12 <= int(config.get("bank_min", 16)) <= int(config.get("bank_max", 19)) <= 19:
        raise MingjianError("银行卡长度范围须为 12—19")
    # Do not accept arbitrary regular expressions from imported rule schemes.
    if config.get("phone_pattern", default_config()["phone_pattern"]) != default_config()["phone_pattern"]:
        raise MingjianError("V1.0 仅支持内置的有界手机号候选规则")
    prefixes = config.get("phone_prefixes", default_config()["phone_prefixes"])
    if not isinstance(prefixes, list) or not prefixes or len(prefixes) > 200 or any(not isinstance(p, str) or not re.fullmatch(r"1[0-9]{1,3}", p) for p in prefixes):
        raise MingjianError("手机号前缀须为 2—4 位、以 1 开头的数字字符串，最多 200 项")
    for rule_id, rule in config["rules"].items():
        if rule_id not in RULE_NAMES or rule.get("risk") not in ("高", "中", "低"):
            raise MingjianError("规则类型或风险等级无效")
        for key in ("enabled", "fullwidth", "lower", "remove_spaces"):
            if key in rule and not isinstance(rule[key], bool):
                raise MingjianError("规则开关须为布尔值")
    seen = set()
    for word in config["words"]:
        value = str(word.get("word", "")).strip()
        if not value or len(value) > 500 or word.get("risk") not in ("高", "中", "低"):
            raise MingjianError("词条须非空且不超过 500 字，风险为高/中/低")
        for key in ("case_sensitive", "enabled"):
            if not isinstance(word.get(key, True), bool):
                raise MingjianError("词条开关须为布尔值")
        key = (value if word.get("case_sensitive", True) else value.lower(), word.get("category", "敏感词"),
               word.get("case_sensitive", True))
        if key in seen:
            raise MingjianError("方案中存在重复词条")
        seen.add(key)
        word["word"] = value
        word.setdefault("id", uid())
    for entry in config["whitelist"]:
        if entry.get("scope") not in ("project", "document"):
            raise MingjianError("白名单范围无效")
        if entry["scope"] == "document" and not entry.get("document_id"):
            raise MingjianError("单文档白名单必须指定文档 ID")
        if not str(entry.get("value", "")).strip() and not str(entry.get("rule_id", "")).strip():
            raise MingjianError("白名单必须填写明确值或规则 ID")
        if not 1 <= len(str(entry.get("reason", "")).strip()) <= 500:
            raise MingjianError("白名单原因须为 1—500 字")
    return config


CSV_COLUMNS = ["word", "category", "risk", "case_sensitive", "suggestion", "enabled"]


def dictionary_csv_preview(text, existing):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames != CSV_COLUMNS:
        raise MingjianError("CSV 列名须严格为：" + ",".join(CSV_COLUMNS))
    words, duplicates, errors = [], [], []
    known = {(w["word"] if w.get("case_sensitive", True) else w["word"].lower(),
              w.get("category", "敏感词"), w.get("case_sensitive", True)) for w in existing}
    for number, row in enumerate(reader, 2):
        try:
            if None in row or any(v is None for v in row.values()):
                raise ValueError("列数不正确")
            if row["case_sensitive"].lower() not in ("true", "false", "1", "0") or row["enabled"].lower() not in ("true", "false", "1", "0"):
                raise ValueError("布尔值仅允许 true/false/1/0")
            item = {**row, "id": uid(), "word": row["word"].strip(),
                    "case_sensitive": row["case_sensitive"].lower() in ("true", "1"),
                    "enabled": row["enabled"].lower() in ("true", "1")}
            validate_config({**default_config(), "words": [item]})
            key = (item["word"] if item["case_sensitive"] else item["word"].lower(), item["category"], item["case_sensitive"])
            if key in known:
                duplicates.append(number)
            else:
                words.append(item)
                known.add(key)
        except (ValueError, MingjianError) as exc:
            errors.append({"line": number, "error": str(exc)})
    return {"words": words, "duplicates": duplicates, "errors": errors}


def scan(blocks, config, document_id, cancel=None):
    validate_config(config)
    findings, errors, excluded = [], [], 0
    words = [w for w in config["words"] if w.get("enabled", True)]
    trees = [(cs, Automaton([(w["word"] if cs else w["word"].lower(), w)
              for w in words if w.get("case_sensitive", True) == cs])) for cs in (True, False)]
    previous_number = None

    def accept(candidate, text):
        nonlocal excluded
        value = text[candidate.start:candidate.end]
        for entry in config["whitelist"]:
            if not entry.get("enabled", True):
                continue
            if entry["scope"] == "document" and entry.get("document_id") != document_id:
                continue
            if entry.get("rule_id") and entry["rule_id"] != candidate.rule_id:
                continue
            if entry.get("value") and entry["value"] != value:
                continue
            excluded += 1
            return
        findings.append(candidate)

    for block in blocks:
        checkpoint(cancel)
        for rule_id, options in config["rules"].items():
            if not options.get("enabled", True):
                continue
            try:
                mapped = normalize(block.text, **{k: options.get(k, False) for k in ("fullwidth", "lower", "remove_spaces")})
                text = mapped.text
                ranges = []
                if rule_id == "phone":
                    for match in re.finditer(r"(?<![0-9])1[3-9][0-9]{9}(?![0-9])", text):
                        if not match.group().startswith(tuple(config.get("phone_prefixes", default_config()["phone_prefixes"]))):
                            continue
                        context = text[max(0, match.start()-10):match.start()]
                        if re.search(r"(?:订单|流水|编号|样例|示例|代码|order|example|sample)\s*[:：=]?\s*$", context, re.I):
                            continue
                        ranges.append((*match.span(), "11 位大陆手机号候选；数字边界和号段结构通过；不验证实际归属"))
                elif rule_id == "identity":
                    for match in re.finditer(r"(?<![0-9A-Za-z])[1-9][0-9]{16}[0-9Xx](?![0-9A-Za-z])", text):
                        if identity_valid(match.group()):
                            ranges.append((*match.span(), "18 位结构、出生日期及校验码通过；不验证身份真实性"))
                elif rule_id == "email":
                    pattern = r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~@-])[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}(?![A-Za-z0-9_@-])"
                    for match in re.finditer(pattern, text):
                        if len(match.group()) <= 254 and len(match.group().split("@")[0]) <= 64:
                            ranges.append((*match.span(), "本地部分、域名、长度与边界结构通过；未联网验证存在性"))
                elif rule_id == "bank":
                    for match in re.finditer(r"(?<![0-9])[0-9](?:[ -]?[0-9]){11,18}(?![ -]?[0-9])", text):
                        digits = re.sub("[ -]", "", match.group())
                        context = text[max(0, match.start()-16):match.start()]
                        if (config.get("bank_min", 16) <= len(digits) <= config.get("bank_max", 19)
                                and luhn(digits) and re.search(r"银行卡|卡号|银行账户|收款账户|bank|card", context, re.I)):
                            ranges.append((*match.span(), "长度与 Luhn 校验通过且邻近银行卡上下文；仅为疑似卡号"))
                elif rule_id == "punctuation":
                    if "```" not in text and not text.lstrip().startswith(("http://", "https://", "def ", "//")):
                        ranges.extend((m.start(), m.end(), "限定连续相同标点；省略号和句点不参与")
                                      for m in re.finditer(r"([，。！？；：、,!?:;])\1+", text))
                elif rule_id == "pairs":
                    stack = []
                    pairs = {"（": "）", "(": ")", "[": "]", "【": "】", "{": "}", "“": "”", "‘": "’", "《": "》"}
                    for i, char in enumerate(text):
                        if char in pairs:
                            stack.append((char, i))
                        elif char in pairs.values():
                            if stack and pairs[stack[-1][0]] == char:
                                stack.pop()
                            else:
                                ranges.append((i, i + 1, "段落内限定符号未匹配；不自动修正"))
                    ranges.extend((i, i + 1, "段落内限定符号未匹配；不自动修正") for _, i in stack)
                elif rule_id == "numbering":
                    # Only explicit top-level Arabic n. / n、 lists; auto-numbering excluded.
                    match = re.match(r"^\s*([1-9][0-9]{0,3})([.、])\s+(?![0-9])", text)
                    if block.locator.get("story", "正文") != "正文" or block.kind in ("table", "header", "footer"):
                        continue
                    if match and not block.locator.get("auto_numbered"):
                        number = int(match[1])
                        if number != 1 and previous_number and previous_number[0] == match[2] and number != previous_number[1] + 1:
                            ranges.append((match.start(1), match.end(1), f"限定同级编号：上项为 {previous_number[1]}，请核实跳跃或重复"))
                        previous_number = (match[2], number)
                    elif text.strip() and not match:
                        previous_number = None
                for start, end, evidence in ranges:
                    start, end = mapped.original_span(start, end)
                    category = RULE_NAMES[rule_id] if rule_id in ("phone", "identity", "email", "bank") else "文本规范"
                    accept(Candidate(block.id, start, end, rule_id, category, options["risk"], evidence,
                                     priority=10 if rule_id == "identity" else 100), block.text)
            except Exception as exc:
                errors.append({"rule_id": rule_id, "block_id": block.id, "error_type": type(exc).__name__})
        for case_sensitive, tree in trees:
            mapped = normalize(block.text, lower=not case_sensitive)
            for start, end, word in tree.search(mapped.text):
                start, end = mapped.original_span(start, end)
                accept(Candidate(block.id, start, end, "word:" + word["id"], "敏感词", word["risk"],
                    "AC 精确子串匹配；" + ("区分大小写" if case_sensitive else "忽略大小写"),
                    word.get("suggestion", "复核后替换")), block.text)
    return findings, errors, excluded
