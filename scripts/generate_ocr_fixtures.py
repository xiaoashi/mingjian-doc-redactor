"""Deterministic synthetic OCR documents and simulated camera conditions.

These are rendered test documents, not photographs supplied by a real person.
Truth is authored before the image transformations and independently of OCR.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from mingjian.reporting.writers import font_path, write_pdf

ROOT = Path(__file__).resolve().parents[1]
FIELDS = [
    ("phone", "联系电话", "13800138000"),
    ("email", "联系邮箱", "review@example.org"),
    ("identity", "证件号码", "11010519491231002X"),
    ("bank", "银行卡号", "4111111111111111"),
]


def image_pdf(path, images):
    pdf = canvas.Canvas(str(path), pagesize=(595, 842))
    pdf.setTitle("明鉴合成 OCR 测试资料")
    pdf.setAuthor("明鉴测试生成器")
    for image in images:
        pdf.drawImage(ImageReader(image), 0, 0, width=595, height=842)
        pdf.showPage()
    pdf.save()


def generate():
    import numpy as np
    folder = ROOT / "fixtures/ocr"
    folder.mkdir(exist_ok=True)
    font = ImageFont.truetype(str(font_path()), 39)
    small = ImageFont.truetype(str(font_path()), 28)
    heading = ImageFont.truetype(str(font_path()), 60)
    sheet = Image.new("RGB", (1600, 2200), "#fffef9")
    draw = ImageDraw.Draw(sheet)
    draw.text((140, 160), "文档敏感信息核对单", font=heading, fill="#1e293b")
    draw.text((140, 265), "合成测试资料 · 不对应真实个人或账户", font=small, fill="#626d7d")
    draw.line((140, 350, 1460, 350), fill="#b8bec8", width=2)
    y = 470
    truth = []
    for kind, label, value in FIELDS:
        draw.text((140, y), label + "：" + value, font=font, fill="#172334")
        truth.append({"rule_id": kind, "value": value, "label": label})
        y += 165
    lines = ["业务说明：本资料仅用于识别流程与脱敏功能测试。", "请将候选问题与原件逐项核对后再决定处理方式。", "正常对照文字：春风拂过图书馆，今日天气晴朗。"]
    y += 100
    for line in lines:
        draw.text((140, y), line, font=small, fill="#374151")
        y += 92
    draw.line((140, 1830, 1460, 1830), fill="#c8cbd0", width=2)
    draw.text((140, 1900), "明鉴测试集 / 合成原稿 / 第 1 页", font=small, fill="#687381")
    sheet.save(folder / "原稿_请用于实拍.png")
    sections = [("title", "文档敏感信息核对单"), ("body", "合成测试资料，不对应真实个人或账户。")]
    sections += [("body", label + "：" + value) for _, label, value in FIELDS]
    sections += [("body", line) for line in lines]
    write_pdf(folder / "01_可提取文字.pdf", sections)
    image_pdf(folder / "02_清晰扫描.pdf", [sheet])
    blur = sheet.resize((800, 1100), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(.9)).resize(sheet.size, Image.Resampling.BICUBIC)
    image_pdf(folder / "03_模糊扫描.pdf", [blur])
    # Project a real rendered paper onto a deterministic desk quadrilateral.
    width, height = 1900, 2500
    source = np.array([[0, 0], [1599, 0], [1599, 2199], [0, 2199]], dtype=float)
    target = np.array([[260, 170], [1670, 280], [1750, 2290], [120, 2150]], dtype=float)
    matrix, vector = [], []
    for (x, y), (u, v) in zip(target, source):
        matrix.extend([[x, y, 1, 0, 0, 0, -u*x, -u*y], [0, 0, 0, x, y, 1, -v*x, -v*y]])
        vector.extend([u, v])
    coefficients = np.linalg.solve(np.array(matrix), np.array(vector))
    projected = sheet.convert("RGBA").transform((width, height), Image.Transform.PERSPECTIVE,
                  coefficients, Image.Resampling.BICUBIC, fillcolor=(0, 0, 0, 0))
    desk = Image.new("RGBA", (width, height), "#6c6258")
    desk.alpha_composite(projected)
    photo = desk.convert("RGB").filter(ImageFilter.GaussianBlur(.25))
    photo.save(folder / "04_倾斜拍照.jpg", quality=88)
    values = np.asarray(photo).astype(float)
    xx = np.linspace(.55, .96, width)[None, :, None]
    yy = np.linspace(.72, 1.0, height)[:, None, None]
    shadow = Image.fromarray(np.uint8(np.clip(values * xx * yy, 0, 255)))
    shadow.save(folder / "05_低光照拍照.jpg", quality=72)
    mixed = canvas.Canvas(str(folder / "06_混合文档.pdf"), pagesize=(595, 842))
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if "OCRFixture" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("OCRFixture", str(font_path())))
    mixed.setFont("OCRFixture", 15)
    mixed.drawString(55, 765, "第一页：可提取文字，联系电话：13900139000")
    mixed.showPage()
    mixed.drawImage(ImageReader(sheet), 0, 0, width=595, height=842)
    mixed.showPage(); mixed.save()
    write_pdf(folder / "07_无敏感项对照.pdf", [("title", "无敏感项对照资料"), ("body", "春风拂过图书馆，今日天气晴朗。请检查是否产生误报。")])
    # Source text is useful for independent visual checks and user photography.
    (folder / "原稿文字与预期.md").write_text("# 合成 OCR 测试说明\n\n请打印“原稿_请用于实拍.png”或“02_清晰扫描.pdf”并拍照，用作真实拍照测试。模拟拍照文件不代替真实照片。\n\n" +
        "\n".join(f"- {label}：{value}" for _, label, value in FIELDS) +
        "\n\n以上为合成测试值，不验证真实存在性。03 模糊扫描、04 倾斜模拟拍照和 05 低光照模拟拍照均应尽量识别四类值；漏字错字必须如实记录，不能把 OCR 不完整判为无风险。06 混合文档第一页额外包含 13900139000，第二页包含上述四类值。07 对照预期零命中，但仍需明确完成审查。\n", encoding="utf-8")
    manifest = {"synthetic": True, "real_photograph_received": False, "truth": truth, "files": []}
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["files"].append({"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "needs_ocr": path.suffix.lower() in (".png", ".jpg") or path.name.startswith(("02", "03", "06")),
                "expected_instances": 0 if path.name.startswith("07") else 5 if path.name.startswith("06") else 4})
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    destination = ROOT / "src/mingjian/resources/ocr-samples"
    shutil.copytree(folder, destination, dirs_exist_ok=True)
    print(f"Generated {len(manifest['files'])} files and a truth manifest.")


if __name__ == "__main__":
    generate()
