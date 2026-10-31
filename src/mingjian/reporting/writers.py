from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from xml.sax.saxutils import escape

from mingjian.domain.models import APP_NAME, MingjianError, VERSION

FONT_LOCK = threading.RLock()
FONT_PATH = Path(__file__).resolve().parents[1] / "resources" / "fonts" / "NotoSansSC-Regular.ttf"


def install_unicode_cmap_adapter():
    """ReportLab's numeric >FFFF CMap values need UTF-16 surrogate pairs.

    Keep its CMap construction and repair only destination scalar encodings.
    No third-party source is copied or changed on disk.
    """
    from reportlab.pdfbase import ttfonts
    if getattr(ttfonts.makeToUnicodeCMap, "_mingjian_utf16", False):
        return
    original = ttfonts.makeToUnicodeCMap
    def utf16_cmap(fontname, subset):
        cmap = original(fontname, subset)
        return re.sub(r"<([0-9A-Fa-f]{5,6})>",
                      lambda m: "<" + chr(int(m[1], 16)).encode("utf-16-be").hex().upper() + ">", cmap)
    utf16_cmap._mingjian_utf16 = True
    ttfonts.makeToUnicodeCMap = utf16_cmap


def font_path():
    configured = os.environ.get("MINGJIAN_FONT")
    if configured and Path(configured).is_file():
        return Path(configured)
    if FONT_PATH.is_file():
        return FONT_PATH
    raise MingjianError("缺少离线中文字体 NotoSansSC-Regular.ttf，请重新安装完整发行包")


def write_txt(path, sections):
    Path(path).write_text("\n\n".join(text for _, text in sections), encoding="utf-8")


def write_docx(path, sections):
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    doc = Document()
    for rel_id, rel in list(doc.part.rels.items()):
        if rel.reltype.endswith("/customXml"):
            del doc.part.rels[rel_id]
    style = doc.styles["Normal"]
    style.font.name = "Noto Sans SC"
    style.font.size = Pt(10.5)
    rpr = style.element.get_or_add_rPr()
    fonts = OxmlElement("w:rFonts")
    fonts.set(qn("w:eastAsia"), "Noto Sans SC")
    rpr.append(fonts)
    for kind, text in sections:
        doc.add_paragraph(text, style="Heading 1" if kind == "title" else "Normal")
    doc.core_properties.title = "脱敏审查输出"
    doc.core_properties.author = "明鉴"
    doc.core_properties.comments = f"{APP_NAME} V{VERSION}；重新排版输出"
    doc.save(path)


def write_pdf(path, sections):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    with FONT_LOCK:
        install_unicode_cmap_adapter()
        if "MingjianSC" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("MingjianSC", str(font_path())))
        if "MingjianEmoji" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("MingjianEmoji", str(FONT_PATH.with_name("NotoEmoji-Regular.ttf"))))
        primary_chars = pdfmetrics.getFont("MingjianSC").face.charToGlyph
        emoji_chars = pdfmetrics.getFont("MingjianEmoji").face.charToGlyph
        def pdf_text(line):
            result = []
            for char in line:
                code = ord(char)
                if char in "\t\r\n":
                    result.append("    ")
                elif code in primary_chars:
                    result.append(escape(char))
                elif code in emoji_chars:
                    result.append('<font name="MingjianEmoji">' + escape(char) + '</font>')
                else:
                    raise MingjianError(f"离线字体不支持字符 U+{code:04X}，无法保证 PDF 无损文本输出；请选择 TXT/DOCX 导出或补充匹配字体")
            return "".join(result)
        normal = ParagraphStyle("body", fontName="MingjianSC", fontSize=10, leading=17,
                                alignment=TA_LEFT, wordWrap="CJK", textColor=colors.HexColor("#243535"))
        title = ParagraphStyle("title", parent=normal, fontSize=16, leading=24, spaceAfter=10)
        story = []
        for kind, text in sections:
            # Bound Paragraph work and long unbroken values; preserve visible text on reread.
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                if line:
                    story.append(Paragraph(pdf_text(line), title if kind == "title" else normal))
                else:
                    story.append(Spacer(1, 5))
            story.append(Spacer(1, 5))
        SimpleDocTemplate(str(path), pagesize=(210 * mm, 297 * mm), leftMargin=20 * mm,
                          rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                          title="脱敏审查输出", author="明鉴").build(story)


def write_chart(path, counts):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    prop = FontProperties(fname=str(font_path()))
    fig = Figure(figsize=(7, 3), dpi=140, facecolor="#fafbf9")
    FigureCanvasAgg(fig)
    axes = fig.subplots()
    if counts:
        labels = list(counts)
        axes.bar(range(len(labels)), list(counts.values()), color="#236d63", width=.5)
        axes.set_xticks(range(len(labels)), labels, fontproperties=prop)
        axes.set_ylabel("问题组数", fontproperties=prop)
        axes.yaxis.get_major_locator().set_params(integer=True)
    else:
        axes.text(.5, .5, "无已记录问题", ha="center", va="center", fontproperties=prop)
        axes.set_axis_off()
    axes.set_title("类别分布（按问题组、主类别统计）", fontproperties=prop, pad=15)
    fig.tight_layout()
    fig.savefig(path)


WRITERS = {"txt": write_txt, "docx": write_docx, "pdf": write_pdf}
