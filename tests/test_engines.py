import random

import pytest

from mingjian.domain.models import Block, MingjianError, default_config
from mingjian.engines.aho import Automaton
from mingjian.engines.redaction import make_plan, replacement
from mingjian.engines.rules import dictionary_csv_preview, identity_valid, luhn, scan, validate_config
from mingjian.parsers.mapping import normalize, utf16_offset


def test_ac_matches_naive_random_and_overlaps():
    rng = random.Random(571)
    for _ in range(50):
        words = {"".join(rng.choice("明鉴ABC") for _ in range(rng.randint(1, 5))) for _ in range(25)}
        text = "".join(rng.choice("明鉴ABC") for _ in range(200))
        actual = sorted(Automaton([(w, w) for w in words]).search(text))
        expected = sorted((i, i + len(w), w) for w in words for i in range(len(text)) if text.startswith(w, i))
        assert actual == expected
    with pytest.raises(ValueError):
        Automaton([("", "empty")])


def test_unicode_mapping():
    text = "😀电 话：１３８ ００１３８０００ İ"
    mapped = normalize(text, fullwidth=True, lower=True, remove_spaces=True)
    start = mapped.text.index("13800138000")
    a, b = mapped.original_span(start, start + 11)
    assert text[a:b] == "１３８ ００１３８０００"
    assert mapped.text.endswith("i\u0307")
    assert mapped.original_span(len(mapped.text)-2, len(mapped.text)) == (len(text)-1, len(text))
    assert utf16_offset("😀abc", 1) == 2


@pytest.mark.parametrize("value,expected", [("11010519491231002X", True), ("110105194912310021", False),
    ("11010519490231002X", False), ("00000019491231002X", False)])
def test_identity(value, expected):
    assert identity_valid(value) is expected


def test_luhn_and_counterexamples():
    assert luhn("4111111111111111")
    assert not luhn("4111111111111112")
    assert not luhn("0000000000000000")
    text = "订单：13800138000 日期：20261030 卡号：4111 1111 1111 1111 随机：4111111111111111 身份：110105194912310021"
    items, errors, _ = scan([Block(text)], default_config(), "doc")
    assert not errors
    assert [i.category for i in items] == ["疑似银行卡号"]


def test_fullwidth_and_case_sensitive_dictionary():
    config = default_config()
    config["words"] = [{"id": "a", "word": "Secret", "risk": "高", "category": "项目", "case_sensitive": False},
                       {"id": "b", "word": "Secret", "risk": "中", "category": "项目", "case_sensitive": True}]
    block = Block("😀电话１３８００１３８０００ secret Secret")
    findings, errors, _ = scan([block], config, "doc")
    assert not errors
    assert len([f for f in findings if f.rule_id.startswith("word:")]) == 3
    phone = next(f for f in findings if f.rule_id == "phone")
    assert block.text[phone.start:phone.end] == "１３８００１３８０００"


def test_whitelist_scope_and_blank_rejection():
    config = default_config()
    config["whitelist"] = [{"scope": "document", "document_id": "one", "value": "13800138000", "reason": "已授权"}]
    assert scan([Block("13800138000")], config, "one")[2] == 1
    assert scan([Block("13800138000")], config, "two")[2] == 0
    config["whitelist"] = [{"scope": "project", "reason": "空值"}]
    with pytest.raises(MingjianError):
        validate_config(config)


def test_csv_validation_and_duplicates():
    text = "word,category,risk,case_sensitive,suggestion,enabled\nSecret,机密,高,false,替换,true\nsecret,机密,高,false,替换,true\n,机密,高,true,替换,true\nabc,机密,高,perhaps,替换,true\n"
    result = dictionary_csv_preview(text, [])
    assert len(result["words"]) == 1
    assert result["duplicates"] == [3]
    assert len(result["errors"]) == 2


def test_limited_text_checks():
    items, errors, _ = scan([Block("正常……"), Block("1. 第一项"), Block("3. 第三项！！（缺少")], default_config(), "doc")
    assert not errors
    assert {i.rule_id for i in items} == {"numbering", "punctuation", "pairs"}


def test_redaction_offsets_and_conflicts():
    block = Block("😀abcdef xyz")
    def finding(identifier, start, end, mode="full"):
        return {"id": identifier, "block_id": block.id, "start_offset": start, "end_offset": end,
                "status": "confirmed", "category": "敏感词", "strategy": {"mode": mode}}
    result = make_plan([block], [finding("one", 1, 4), finding("two", 8, 11)])
    assert result["texts"][block.id] == "😀[敏感词已脱敏]def [敏感词已脱敏]"
    result = make_plan([block], [finding("one", 1, 4), finding("two", 3, 6)])
    assert result["conflicts"]
    result = make_plan([block], [finding("one", 1, 4), finding("two", 1, 4)])
    assert len(result["plan"]) == 1
    assert replacement("13800138000", "手机号", {"mode": "partial", "prefix": 3, "suffix": 4}) == "138****8000"
    with pytest.raises(MingjianError):
        replacement("abc", "敏感词", {"mode": "partial", "prefix": 3})


def test_rule_failure_isolated(monkeypatch):
    import mingjian.engines.rules as rules
    def broken(value):
        raise RuntimeError("injected")
    monkeypatch.setattr(rules, "identity_valid", broken)
    findings, errors, _ = scan([Block("身份证11010519491231002X 邮箱a@example.org")], default_config(), "doc")
    assert errors and errors[0]["rule_id"] == "identity"
    assert any(f.rule_id == "email" for f in findings)


def test_configured_phone_prefixes():
    config = default_config()
    config["phone_prefixes"] = ["138"]
    items, _, _ = scan([Block("联系电话13800138000 联系电话13900139000")], config, "doc")
    assert len(items) == 1
