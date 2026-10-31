"""Domain values. Offsets are half-open Python Unicode code point indices."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

APP_NAME = "明鉴文档敏感信息识别与脱敏系统"
VERSION = "1.2.0"
PARSER_VERSION = "1.0.0"
SCHEMA_VERSION = 1


class MingjianError(Exception):
    """A safe, actionable error without document content."""


class Cancelled(MingjianError):
    pass


def uid() -> str:
    return str(uuid.uuid4())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


@dataclass
class Limits:
    max_files: int = 200
    max_mib: int = 50
    max_pages: int = 300
    max_chars: int = 2_000_000
    max_zip_bytes: int = 500 * 1024 * 1024
    max_zip_entries: int = 10_000
    max_entry_bytes: int = 100 * 1024 * 1024
    parse_workers: int = 2
    scan_workers: int = 2

    def validate(self):
        if not 5 <= self.max_mib <= 200:
            raise MingjianError("单文件限制须为 5—200 MiB")
        if not 1 <= self.parse_workers <= 4 or not 1 <= self.scan_workers <= 4:
            raise MingjianError("解析和识别并发须为 1—4")


@dataclass
class Block:
    text: str
    kind: str = "paragraph"
    locator: dict = field(default_factory=dict)
    id: str = field(default_factory=uid)

    def to_dict(self):
        return asdict(self)


@dataclass
class Parsed:
    blocks: list[Block]
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def coverage(self):
        return "partial" if self.warnings else "complete"


@dataclass
class Candidate:
    block_id: str
    start: int
    end: int
    rule_id: str
    category: str
    risk: str
    evidence: str
    suggestion: str = "复核后确认替换，或填写原因后忽略"
    priority: int = 100


DEFAULT_RULES = {
    "phone": {"enabled": True, "risk": "高", "fullwidth": True, "lower": False, "remove_spaces": False},
    "identity": {"enabled": True, "risk": "高", "fullwidth": True, "lower": False, "remove_spaces": False},
    "email": {"enabled": True, "risk": "中", "fullwidth": False, "lower": False, "remove_spaces": False},
    "bank": {"enabled": True, "risk": "高", "fullwidth": True, "lower": False, "remove_spaces": False},
    "punctuation": {"enabled": True, "risk": "低"},
    "pairs": {"enabled": True, "risk": "低"},
    "numbering": {"enabled": True, "risk": "低"},
}
RULE_NAMES = {"phone": "手机号", "identity": "身份证号", "email": "邮箱", "bank": "疑似银行卡号",
              "punctuation": "重复标点", "pairs": "符号配对", "numbering": "章节编号"}


def default_config():
    return {"rules": json.loads(dumps(DEFAULT_RULES)), "words": [], "whitelist": [],
            "phone_pattern": r"1[3-9][0-9]{9}", "phone_version": "mainland-2026-baseline",
            "phone_prefixes": ["13", "14", "15", "16", "17", "18", "19"],
            "bank_min": 16, "bank_max": 19}
