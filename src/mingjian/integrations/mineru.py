"""MinerU adapter, based on the supplied mineru3 HTTP contract.

No source documents are sent until the caller supplies consent for this endpoint.
POST is never retried automatically. Responses cannot redirect file uploads.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

import httpx

from mingjian.domain.models import Block, Limits, MingjianError, Parsed
from mingjian.parsers.documents import checkpoint

DEFAULT_ENDPOINT = "http://127.0.0.1:8003"
MAX_RESPONSE = 100 * 1024**2


def validate_endpoint(value):
    value = value.strip().rstrip("/")
    url = urlsplit(value)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise MingjianError("OCR 地址须为不含账号、查询串的 http/https 服务根地址")
    return value


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in ("tr", "p", "div"):
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("\t")


def plain_html(value):
    parser = TextHTML()
    parser.feed(value)
    return "".join(parser.parts).strip()


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def _rich_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_rich_text(v) for v in value)
    if isinstance(value, dict):
        for key in ("content", "text", "spans", "paragraph_content", "title_content"):
            if key in value:
                return _rich_text(value[key])
    return ""


def normalize_result(payload, limits=None):
    """Accept API envelope, v1/v2 content list, middle JSON, or bounded ZIP."""
    limits = limits or Limits()
    if isinstance(payload, bytes):
        if payload.startswith(b"PK"):
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                if len(archive.infolist()) > 10000 or sum(e.file_size for e in archive.infolist()) > MAX_RESPONSE:
                    raise MingjianError("OCR 返回压缩包资源超限")
                for entry in archive.infolist():
                    path = PurePosixPath(entry.filename)
                    if path.is_absolute() or ".." in path.parts or "\\" in entry.filename:
                        raise MingjianError("OCR 返回压缩包路径不安全")
                names = sorted(archive.namelist(), key=lambda n: (not n.endswith("_content_list.json"), n))
                for name in names:
                    if name.endswith(("_content_list.json", "_content_list_v2.json", "_middle.json")):
                        return normalize_result(json.loads(archive.read(name)), limits)
                raise MingjianError("OCR 压缩包缺少结构化文字结果")
        payload = json.loads(payload)
    payload = _json(payload)
    blocks, notices = [], []
    text_count = 0

    def add(text, page, kind="ocr", bbox=None):
        nonlocal text_count
        if not text or not text.strip():
            return
        if page is not None and not 0 <= int(page) < limits.max_pages:
            raise MingjianError("OCR 返回页码超出限制")
        text = text.replace("\x00", "")
        text_count += len(text)
        if text_count > limits.max_chars:
            raise MingjianError("OCR 文本字符数超限，未截断审查")
        locator = {"ocr": True, "engine": "MinerU", "page": int(page)+1 if page is not None else None,
                   "bbox": bbox, "coordinate_system": "MinerU 原始返回坐标"}
        blocks.append(Block(text, kind, locator))

    def walk(value):
        value = _json(value)
        if isinstance(value, dict):
            for key in ("content_list", "content_list_v2", "middle_json"):
                if value.get(key):
                    walk(value[key])
                    if blocks:
                        return
            if "pdf_info" in value:
                for page in value["pdf_info"]:
                    for block in page.get("para_blocks", []):
                        text = "\n".join("".join(span.get("content", "") for span in line.get("spans", [])) for line in block.get("lines", []))
                        add(text, page.get("page_idx"), bbox=block.get("bbox"))
                return
            if "results" in value:
                for item in value["results"].values():
                    walk(item)
                return
            for key in ("result", "data"):
                if key in value:
                    walk(value[key])
                    if blocks:
                        return
            md = value.get("md_content") or value.get("markdown")
            if isinstance(md, str) and md.strip():
                notices.append("OCR 只返回 Markdown，无法保证页码和坐标；请核对原件")
                for text in re.split(r"\n\s*\n", md):
                    add(text, None)
            return
        if isinstance(value, list):
            for page_index, item in enumerate(value):
                page_items = item if isinstance(item, list) else [item]
                for block in page_items:
                    if not isinstance(block, dict):
                        continue
                    kind = block.get("type", "text")
                    page = block.get("page_idx", page_index if isinstance(item, list) else None)
                    content = block.get("content", {})
                    if kind == "table":
                        html = block.get("table_body") or (content.get("html", "") if isinstance(content, dict) else "")
                        add(plain_html(html), page, "table", block.get("bbox"))
                    elif kind in ("image", "chart"):
                        for key in ("image_caption", "image_footnote", "table_caption", "table_footnote"):
                            add(_rich_text(block.get(key)), page, bbox=block.get("bbox"))
                        notices.append("OCR 返回中仍含图像/图表对象，非文字内容不在审查范围")
                    else:
                        text = block.get("text") or _rich_text(content) or "\n".join(block.get("list_items", []))
                        add(text, page, bbox=block.get("bbox"))
    walk(payload)
    if not blocks:
        raise MingjianError("OCR 未返回可审查文字，不能将该文件认定为零风险")
    warnings = ["OCR 提取文字可能存在漏字、错字和数字混淆；请逐页对照原件核对，覆盖范围仅为识别出的文字"]
    return Parsed(blocks, warnings + sorted(set(notices)), {"engine": "MinerU", "ocr": True,
                  "requires_ocr": False, "pages": max((b.locator.get("page") or 1 for b in blocks), default=1),
                  "pages_with_text": len({b.locator.get("page") for b in blocks})})


class MinerUClient:
    def __init__(self, endpoint=DEFAULT_ENDPOINT, client=None, max_wait=900):
        self.endpoint = validate_endpoint(endpoint)
        headers = {}
        if os.environ.get("MINERU_API_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["MINERU_API_TOKEN"]
        self.client = client or httpx.Client(timeout=httpx.Timeout(45, connect=5), follow_redirects=False,
                                             trust_env=False, headers=headers)
        self.owned = client is None
        self.max_wait = max_wait

    def close(self):
        if self.owned:
            self.client.close()

    def health(self):
        try:
            response = self.client.get(self.endpoint + "/health", timeout=5)
            response.raise_for_status()
            return {"available": True, "endpoint": self.endpoint}
        except httpx.HTTPError:
            return {"available": False, "endpoint": self.endpoint, "message": "服务暂时无法连接，请检查地址、网络或服务是否启动"}

    def _get_json(self, path):
        with self.client.stream("GET", self.endpoint + path, follow_redirects=False) as response:
            response.raise_for_status()
            if response.is_redirect:
                raise MingjianError("OCR 返回重定向，已停止访问其他地址")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise MingjianError("OCR 返回结果超限")
                chunks.append(chunk)
            content = b"".join(chunks)
            return response, json.loads(content) if "json" in response.headers.get("content-type", "") else content

    def parse(self, path, limits=None, cancel=None, consent_endpoint=None):
        if consent_endpoint != self.endpoint:
            raise MingjianError("发送 OCR 前必须明确同意将当前文件上传到指定服务地址")
        checkpoint(cancel)
        limits = limits or Limits()
        if path.stat().st_size > limits.max_mib * 1024**2:
            raise MingjianError("文件大小超过 OCR 限制")
        if path.suffix.lower() == ".pdf":
            import pdfplumber
            try:
                with pdfplumber.open(path) as pdf:
                    if pdf.doc.encryption:
                        raise MingjianError("请先提供未加密的 PDF 副本")
                    if len(pdf.pages) > limits.max_pages:
                        raise MingjianError("PDF 页数超过 OCR 限制，请先分拆；不会静默截断上传")
            except MingjianError:
                raise
            except Exception as exc:
                raise MingjianError("PDF 无法打开或已加密，请先提供可读取的副本") from exc
        data = {"backend": "hybrid-auto-engine", "parse_method": "auto", "lang_list": "ch", "return_md": "true",
                "return_content_list": "true", "return_middle_json": "false", "return_model_output": "false",
                "return_images": "false", "return_original_file": "false", "response_format_zip": "false",
                "formula_enable": "true", "table_enable": "true", "start_page_id": "0", "end_page_id": str(limits.max_pages-1)}
        try:
            # Use a neutral filename: original user filenames are never sent as metadata.
            with open(path, "rb") as file:
                response = self.client.post(self.endpoint + "/tasks", data=data,
                    files={"files": ("document" + path.suffix, file, "application/octet-stream")}, follow_redirects=False)
            response.raise_for_status()
            if response.is_redirect:
                raise MingjianError("OCR 服务返回重定向，已停止文件传输")
            submission = response.json()
            if not submission.get("task_id"):
                return normalize_result(submission, limits)
            task_id = quote(str(submission["task_id"]), safe="")
            deadline = time.monotonic() + self.max_wait
            while time.monotonic() < deadline:
                checkpoint(cancel)
                _, status = self._get_json(f"/tasks/{task_id}")
                if isinstance(status, dict) and status.get("status") in ("failed", "cancelled"):
                    raise MingjianError("OCR 服务处理失败，请检查原件后重试")
                response, result = self._get_json(f"/tasks/{task_id}/result")
                if response.status_code == 200:
                    return normalize_result(result, limits)
                if cancel:
                    cancel.wait(1.5)
                else:
                    time.sleep(1.5)
            raise MingjianError("OCR 处理等待超时；远端任务可能仍在执行，可稍后重试")
        except httpx.HTTPError as exc:
            raise MingjianError("OCR 服务无法连接或返回错误，请检查服务状态后重试；未把该文件标为审查通过") from exc

