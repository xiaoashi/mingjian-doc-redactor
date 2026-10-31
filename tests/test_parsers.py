import codecs
import zipfile

import pytest
from docx import Document

from mingjian.domain.models import Limits, MingjianError
from mingjian.parsers.documents import parse_file
from mingjian.reporting.writers import write_pdf


def test_docx_order_merged_cells_headers_and_revisions(tmp_path):
    doc = Document()
    doc.add_paragraph("正文一")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "合并单元格"
    table.cell(1, 0).text = "下一行"
    doc.sections[0].header.paragraphs[0].text = "唯一页眉"
    doc.sections[0].footer.paragraphs[0].text = "唯一页脚"
    doc.add_section()
    doc.add_paragraph("正文二")
    path = tmp_path / "merged.docx"
    doc.save(path)
    result = parse_file(path)
    texts = [b.text for b in result.blocks]
    assert texts.count("合并单元格") == 1
    assert texts.count("唯一页眉") == 1
    assert texts.count("唯一页脚") == 1
    assert texts.index("正文一") < texts.index("合并单元格") < texts.index("正文二")
    other = tmp_path / "revision.docx"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(other, "w") as dest:
        for item in source.infolist():
            value = source.read(item)
            if item.filename == "word/document.xml":
                value = value.replace(b"<w:body>", b'<w:body><w:ins w:id="1"><w:p><w:r><w:t>revision</w:t></w:r></w:p></w:ins>')
            dest.writestr(item.filename, value)
    assert parse_file(other).coverage == "partial"


def test_zip_traversal_and_xml_entity_rejected(tmp_path):
    bad = tmp_path / "bad.docx"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../evil", "x")
        archive.writestr("word/document.xml", "<test/>")
    with pytest.raises(MingjianError):
        parse_file(bad)
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("word/document.xml", '<!DOCTYPE test [<!ENTITY ent "bad">]><test>&ent;</test>')
    with pytest.raises(Exception):
        parse_file(bad)


@pytest.mark.parametrize("encoding,bom", [("utf-8", b""), ("utf-8", codecs.BOM_UTF8),
    ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE)])
def test_txt_bom_exact_line_endings(tmp_path, encoding, bom):
    path = tmp_path / "text.txt"
    text = "😀全角１３８００１３８０００\r\n第二行\r第三行\n"
    path.write_bytes(bom + text.encode(encoding))
    result = parse_file(path)
    assert "".join(b.text for b in result.blocks) == text


def test_pdf_text_positions_blank_and_encrypted(tmp_path):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pdfencrypt import StandardEncryption
    normal = tmp_path / "text.pdf"
    write_pdf(normal, [("body", "联系电话13800138000")])
    parsed = parse_file(normal)
    assert parsed.blocks[0].locator["page"] == 1
    assert parsed.blocks[0].locator["characters"]
    empty = tmp_path / "empty.pdf"
    c = canvas.Canvas(str(empty)); c.showPage(); c.save()
    assert parse_file(empty).coverage == "partial"
    encrypted = tmp_path / "encrypted.pdf"
    c = canvas.Canvas(str(encrypted), encrypt=StandardEncryption("password")); c.drawString(10, 10, "hidden"); c.showPage(); c.save()
    with pytest.raises(MingjianError):
        parse_file(encrypted)


def test_resource_limits(tmp_path):
    path = tmp_path / "text.txt"
    path.write_text("123456", encoding="utf-8")
    with pytest.raises(MingjianError):
        parse_file(path, Limits(max_chars=5))


def test_pdf_non_bmp_roundtrip(tmp_path):
    from mingjian.validation.exports import read_output
    path = tmp_path / "emoji.pdf"
    write_pdf(path, [("body", "😀正常内容与手机号13800138000")])
    assert read_output(path) == "😀正常内容与手机号13800138000"
