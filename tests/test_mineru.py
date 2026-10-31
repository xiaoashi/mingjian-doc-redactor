import io
import json
import zipfile

import httpx
import pytest

from mingjian.ai.reviewer import DisabledReviewer, Suggestion, review_with_provider, validate_suggestions
from mingjian.domain.models import Block, Limits, MingjianError
from mingjian.integrations.mineru import MinerUClient, normalize_result, validate_endpoint


def test_v1_v2_and_envelope():
    v1 = [{"type": "text", "text": "联系电话13800138000", "page_idx": 1, "bbox": [10,20,30,40]},
          {"type": "table", "table_body": "<table><tr><td>邮箱</td><td>a@example.org</td></tr></table>", "page_idx": 1}]
    parsed = normalize_result({"results": {"document": {"content_list": json.dumps(v1)}}})
    assert len(parsed.blocks) == 2 and parsed.blocks[0].locator["page"] == 2
    assert "a@example.org" in parsed.blocks[1].text and parsed.blocks[1].kind == "table"
    assert parsed.coverage == "partial" and parsed.metadata["ocr"]
    v2 = [[{"type":"paragraph","content":{"paragraph_content":[{"type":"text","content":"中文正文"}]},"bbox":[0,0,10,10]}]]
    assert normalize_result(v2).blocks[0].text == "中文正文"


def test_middle_json_and_empty_result():
    middle = {"pdf_info": [{"page_idx":0, "para_blocks":[{"bbox":[0,0,1,1], "lines":[{"spans":[{"content":"hello"},{"content":"中文"}]}]}]}]}
    assert normalize_result(middle).blocks[0].text == "hello中文"
    with pytest.raises(MingjianError):
        normalize_result({"results":{}})
    with pytest.raises(MingjianError):
        normalize_result([{"type":"text","text":"123456","page_idx":0}], Limits(max_chars=5))


def test_zip_response_cannot_traverse():
    output = io.BytesIO()
    with zipfile.ZipFile(output,"w") as archive:
        archive.writestr("../evil.txt", "bad")
        archive.writestr("document_content_list.json", '[{"type":"text","text":"safe","page_idx":0}]')
    with pytest.raises(MingjianError):
        normalize_result(output.getvalue())


def test_upload_consent_and_async_protocol(tmp_path):
    requests = []
    path = tmp_path / "private-name.png"
    path.write_bytes(b"synthetic-image")
    def handler(request):
        requests.append(request)
        if request.url.path == "/tasks" and request.method == "POST":
            assert b"private-name" not in request.content
            return httpx.Response(202,json={"task_id":"one"})
        if request.url.path.endswith("/result"):
            return httpx.Response(200,json={"results":{"document":{"content_list":[{"type":"text","text":"联系电话13800138000","page_idx":0}]}}})
        return httpx.Response(200,json={"status":"completed"})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = MinerUClient("http://127.0.0.1:8003",client=client)
    with pytest.raises(MingjianError):
        adapter.parse(path)
    assert not requests
    parsed = adapter.parse(path,consent_endpoint=adapter.endpoint)
    assert parsed.blocks[0].text == "联系电话13800138000"
    assert len([r for r in requests if r.method=="POST"]) == 1
    client.close()


def test_transport_failure_and_health(tmp_path):
    path = tmp_path / "test.png"; path.write_bytes(b"synthetic")
    def handler(request):
        raise httpx.ConnectError("private server detail",request=request)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = MinerUClient("http://127.0.0.1:8003",client=client)
    assert not adapter.health()["available"]
    with pytest.raises(MingjianError, match="无法连接"):
        adapter.parse(path,consent_endpoint=adapter.endpoint)
    client.close()


@pytest.mark.parametrize("url", ["file:///tmp/a", "http://user:pass@host", "https://host?token=secret", "ftp://host"])
def test_endpoint_validation(url):
    with pytest.raises(MingjianError):
        validate_endpoint(url)


def test_ai_extension_never_applies_suggestions_automatically():
    block = Block("正常内容")
    with pytest.raises(MingjianError):
        review_with_provider(DisabledReviewer(),[block])
    with pytest.raises(MingjianError):
        validate_suggestions([block],[Suggestion(block.id,0,999,"语义风险","解释")])
    result = validate_suggestions([block],[Suggestion(block.id,0,2,"语义风险","待人工复核")])
    assert len(result)==1
