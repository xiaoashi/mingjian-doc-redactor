from __future__ import annotations

import codecs
import zipfile
from pathlib import Path, PurePosixPath

from defusedxml import ElementTree as SafeET

from mingjian.domain.models import Block, Cancelled, Limits, MingjianError, Parsed

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def checkpoint(cancel):
    if cancel and cancel.is_set():
        raise Cancelled("任务已取消")


def check_zip(path, limits):
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > limits.max_zip_entries or sum(i.file_size for i in entries) > limits.max_zip_bytes:
                raise MingjianError("DOCX 解压资源超限")
            for item in entries:
                name = PurePosixPath(item.filename)
                if name.is_absolute() or ".." in name.parts or "\\" in item.filename or ":" in item.filename:
                    raise MingjianError("DOCX 含不安全的 ZIP 路径")
                if item.file_size > limits.max_entry_bytes:
                    raise MingjianError("DOCX 单条目解压大小超限")
                if item.flag_bits & 1:
                    raise MingjianError("不支持加密 DOCX")
            if "word/document.xml" not in archive.namelist():
                raise MingjianError("文件不是 DOCX：缺少正文 XML")
            for item in entries:
                if item.filename.endswith(".xml"):
                    SafeET.fromstring(archive.read(item))
    except (zipfile.BadZipFile, SafeET.ParseError) as exc:
        raise MingjianError("DOCX 压缩包或 XML 损坏") from exc


def parse_txt(path, limits, encoding=None, cancel=None):
    raw = Path(path).read_bytes()
    if b"\x00" in raw and not raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        raise MingjianError("TXT 含 NUL 字节；请检查格式或使用带 BOM 的 UTF-16")
    if encoding not in (None, "utf-8", "utf-8-sig", "utf-16", "gb18030"):
        raise MingjianError("不支持所选编码")
    chosen = encoding or ("utf-16" if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8-sig")
    try:
        text = raw.decode(chosen, errors="strict")
    except UnicodeError as exc:
        raise MingjianError("TXT 编码无法确定。请在重新解析时选择 GB18030 或正确编码；未静默替换字符。") from exc
    if len(text) > limits.max_chars:
        raise MingjianError("文本字符数超限，未截断审查")
    checkpoint(cancel)
    # Keep exact line endings in immutable blocks, so offsets also map to raw text.
    blocks = [Block(line, "text", {"line": n + 1}) for n, line in enumerate(text.splitlines(keepends=True))]
    if not blocks:
        blocks = [Block("", "text", {"line": 1})]
    return Parsed(blocks, metadata={"encoding": chosen, "crlf": text.count("\r\n"),
                                    "lf": text.count("\n") - text.count("\r\n")})


def parse_docx(path, limits, cancel=None):
    from docx import Document

    check_zip(path, limits)
    document = Document(path)
    blocks, warnings = [], []

    def paragraph_text(element):
        result = []
        for node in element.iter():
            if node.tag == W + "t":
                result.append(node.text or "")
            elif node.tag == W + "tab":
                result.append("\t")
            elif node.tag in (W + "br", W + "cr"):
                result.append("\n")
        return "".join(result)

    def walk(element, kind, locator):
        for index, child in enumerate(element):
            checkpoint(cancel)
            if child.tag == W + "p":
                text = paragraph_text(child)
                if text:
                    blocks.append(Block(text, kind, {**locator, "paragraph": index + 1,
                        "auto_numbered": child.find(".//" + W + "numPr") is not None}))
            elif child.tag == W + "tbl":
                table_path = child.getroottree().getpath(child)
                for row, tr in enumerate(child.findall(W + "tr")):
                    # Actual XML tc nodes are unique; merged cells are never iterated twice.
                    for col, tc in enumerate(tr.findall(W + "tc")):
                        walk(tc, "table" if kind == "paragraph" else kind,
                             {**locator, "table": index + 1, "table_path": table_path,
                              "row": row + 1, "column": col + 1})
            elif child.tag == W + "sdt":
                warnings.append("存在内容控件：仅支持的正文已读取，请核实覆盖")
                content = child.find(W + "sdtContent")
                if content is not None:
                    walk(content, kind, locator)

    walk(document.element.body, "paragraph", {"story": "正文"})
    story_sections, active_stories = {}, {}
    for section_number, section in enumerate(document.sections, 1):
        for ref in section._sectPr:
            if ref.tag in (W + "headerReference", W + "footerReference"):
                rid = ref.get(R + "id")
                part = document.part.related_parts[rid]
                key = str(part.partname)
                active_stories[(ref.tag, ref.get(W + "type", "default"))] = (key, part)
        for key, part in active_stories.values():
            sections = story_sections.setdefault(key, {"part": part, "sections": []})["sections"]
            if section_number not in sections:
                sections.append(section_number)
    for key, entry in story_sections.items():
        kind = "header" if "header" in key else "footer"
        walk(entry["part"].element, kind, {"story": "页眉" if kind == "header" else "页脚",
                                            "sections": entry["sections"], "part": key})
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                root = SafeET.fromstring(archive.read(name))
                tags = {node.tag.split("}")[-1] for node in root.iter()}
                if tags & {"ins", "del", "moveFrom", "moveTo"}:
                    warnings.append("存在未处理修订语义：修订范围未完整审查")
                if "txbxContent" in tags:
                    warnings.append("存在文本框：不能保证文本框内容完整覆盖")
                if tags & {"object", "oleObject", "pict", "drawing", "altChunk"}:
                    warnings.append("存在图像、绘图、嵌入对象或外部内容：非文本内容未审查")
                if "commentRangeStart" in tags or "commentReference" in tags or "comments" in name:
                    warnings.append("存在批注：批注正文不在首版审查范围")
                if tags & {"fldSimple", "fldChar", "instrText"}:
                    warnings.append("存在域：仅提取已保存的显示文本，未计算域值")
                if tags & {"footnoteReference", "endnoteReference"}:
                    warnings.append("存在脚注或尾注：正文之外的注释内容未覆盖")
    if sum(len(block.text) for block in blocks) > limits.max_chars:
        raise MingjianError("DOCX 文本字符数超限，未截断审查")
    return Parsed(blocks, sorted(set(warnings)), {"sections": len(document.sections)})


def parse_pdf(path, limits, cancel=None):
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    blocks, warnings, count = [], [], 0
    try:
        with pdfplumber.open(path) as pdf:
            if pdf.doc.encryption:
                raise MingjianError("PDF 已加密，请提供未加密副本")
            if len(pdf.pages) > limits.max_pages:
                raise MingjianError("PDF 页数超限，请先分拆文件")
            pages = len(pdf.pages)
            for number, page in enumerate(pdf.pages, 1):
                checkpoint(cancel)
                lines = page.extract_text_lines(return_chars=True)
                if not lines:
                    blocks.append(Block("", "pdf", {"page": number}))
                    warnings.append(f"第 {number} 页无可提取文本：请核实空白或扫描页")
                for line, item in enumerate(lines, 1):
                    text = item["text"]
                    count += len(text)
                    if count > limits.max_chars:
                        raise MingjianError("PDF 文本字符数超限，未截断审查")
                    chars = [{"text": c["text"], "bbox": [c["x0"], c["top"], c["x1"], c["bottom"]]}
                             for c in item.get("chars", [])]
                    blocks.append(Block(text, "pdf", {"page": number, "line": line,
                        "bbox": [item[k] for k in ("x0", "top", "x1", "bottom")], "characters": chars}))
                if page.images or page.curves or page.rects:
                    warnings.append(f"第 {number} 页含图像或矢量绘图：非文本内容未审查")
                if page.annots:
                    warnings.append(f"第 {number} 页存在注释、链接或表单对象：注释与表单值未全面审查")
                if any("\ufffd" in b.text or "(cid:" in b.text for b in blocks if b.locator.get("page") == number):
                    warnings.append(f"第 {number} 页疑似字符映射异常")
                if lines and any(len(item["text"]) > 100 and "   " in item["text"] for item in lines):
                    warnings.append(f"第 {number} 页疑似多栏：请人工核实阅读顺序")
                page.close()
    except PDFPasswordIncorrect as exc:
        raise MingjianError("PDF 已加密，请先提供未加密副本") from exc
    except Exception as exc:
        if isinstance(exc.__cause__, PDFPasswordIncorrect) or any(isinstance(arg, PDFPasswordIncorrect) for arg in exc.args):
            raise MingjianError("PDF 已加密，请先提供未加密副本") from exc
        raise
    return Parsed(blocks, warnings, {"pages": pages, "requires_ocr": count == 0,
                                    "position_note": "字符字形边界与行内原文分别保存；空格可能为提取器插入"})


def parse_file(path, limits=None, encoding=None, cancel=None):
    limits = limits or Limits()
    path = Path(path)
    checkpoint(cancel)
    if path.stat().st_size > limits.max_mib * 1024 * 1024:
        raise MingjianError("文件大小超限")
    ext = path.suffix.lower()
    if ext == ".txt":
        return parse_txt(path, limits, encoding, cancel)
    if ext == ".docx":
        return parse_docx(path, limits, cancel)
    if ext == ".pdf":
        if not path.read_bytes()[:1024].lstrip().startswith(b"%PDF-"):
            raise MingjianError("文件实际类型不是 PDF")
        return parse_pdf(path, limits, cancel)
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"):
        from PIL import Image
        with Image.open(path) as img:
            if img.width * img.height > 60_000_000:
                raise MingjianError("图片像素超限")
            metadata = {"requires_ocr": True, "width": img.width, "height": img.height, "frames": getattr(img, "n_frames", 1)}
        return Parsed([Block("", "image", {"page": 1})], ["图片需要 OCR 才能读取文字；当前尚未识别图片中的内容"], metadata)
    raise MingjianError("仅支持 DOCX、文本型 PDF 和 TXT")
