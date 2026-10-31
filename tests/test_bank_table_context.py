from docx import Document

from mingjian.domain.models import default_config
from mingjian.engines.rules import scan
from mingjian.parsers.documents import parse_file


def test_card_label_in_left_cell_does_not_join_digits_or_cross_tables(tmp_path):
    doc = Document()
    table = doc.add_table(rows=3, cols=2)
    table.cell(0,0).text = "银行卡号"
    table.cell(0,1).text = "4111111111111111"
    table.cell(1,0).text = "订单号"
    table.cell(1,1).text = "4111111111111111"
    table.cell(2,0).text = "银行卡号 41111111"
    table.cell(2,1).text = "11111111"
    other = doc.add_table(rows=1, cols=2)
    other.cell(0,0).text = "订单号"
    other.cell(0,1).text = "4111111111111111"
    doc.add_paragraph("无上下文数字4111111111111111")
    path = tmp_path/"table.docx"
    doc.save(path)
    parsed = parse_file(path)
    findings,errors,_ = scan(parsed.blocks,default_config(),"test")
    cards = [f for f in findings if f.rule_id == "bank"]
    assert not errors and len(cards) == 1
    block = next(b for b in parsed.blocks if b.id == cards[0].block_id)
    assert block.text[cards[0].start:cards[0].end] == "4111111111111111"
    assert block.locator["row"] == 1 and block.locator["column"] == 2
