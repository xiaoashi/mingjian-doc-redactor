import json

import httpx
import pytest

from mingjian.ai.ecnu import AIConfig, EcnuClient, validate_ai_base
from mingjian.domain.models import Block, MingjianError
from mingjian.services.ai import augment_with_ai, ai_provenance
from test_web import client as web_client  # noqa: F401 -- reuse the isolated web workspace fixture


def response(data,finish="stop"):
    return httpx.Response(200,json={"choices":[{"finish_reason":finish,"message":{"content":json.dumps(data,ensure_ascii=False)}}],
                                    "usage":{"prompt_tokens":12,"completion_tokens":18,"total_tokens":30}})


def test_key_never_in_prompt_or_repr_and_quotes_map_codepoints():
    block=Block("😀联系人张三，地址某路。")
    key="unit-test-credential-do-not-export"
    def handler(request):
        assert request.headers["Authorization"]=="Bearer "+key
        assert key.encode() not in request.content
        return response({"suggestions":[{"block_id":block.id,"quote":"张三","category":"姓名","reason":"出现明确联系人姓名"}]})
    config=AIConfig(api_key=key)
    assert key not in repr(config) and key not in json.dumps(config.public())
    client=httpx.Client(transport=httpx.MockTransport(handler))
    provider=EcnuClient(config,client)
    result=provider.suggest([block])
    assert result[0].start==4 and block.text[result[0].start:result[0].end]=="张三"
    client.close()


def test_hallucinated_quotes_and_truncation_rejected():
    block=Block("原始内容")
    transport=httpx.MockTransport(lambda r:response({"suggestions":[{"block_id":block.id,"quote":"不存在的内容","reason":"猜测"}]}))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(MingjianError,match="拒绝"):
            EcnuClient(AIConfig(api_key="fake"),client).suggest([block])
    with httpx.Client(transport=httpx.MockTransport(lambda r:response({},"length"))) as client:
        with pytest.raises(MingjianError,match="截断"):
            EcnuClient(AIConfig(api_key="fake"),client).chat("x","y")


def test_redirect_and_http_error_do_not_leak_credentials():
    for status in (307,401,429,500):
        with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(status,text="secret-body"))) as client:
            with pytest.raises(MingjianError) as error:
                EcnuClient(AIConfig(api_key="fake"),client).chat("x","y")
            assert "secret-body" not in str(error.value)
    with pytest.raises(MingjianError):
        validate_ai_base("http://remote.example/api")


def test_vision_result_is_partial_and_has_original_page():
    from PIL import Image
    with httpx.Client(transport=httpx.MockTransport(lambda r:response({"blocks":[{"text":"联系电话13800138000","kind":"text"}],"warnings":[]}))) as client:
        provider=EcnuClient(AIConfig(api_key="fake"),client)
        blocks,warnings=provider.transcribe_image(Image.new("RGB",(200,300),"white"),page=2)
        assert blocks[0].locator["page"]==2 and blocks[0].locator["ocr"]
        assert not warnings


def test_ai_new_batch_preserves_old_review_and_requires_new_completion(project,imported):
    identifier=imported("联系人张三，联系电话13800138000")
    project.scan(identifier)
    old=project.findings(identifier)[0]
    project.review_many(identifier,[{"id":old["id"],"revision":old["revision"],"status":"confirmed","note":"已核对"}])
    old_run=project.document(identifier)["current_run_id"]
    block=project.parsed(identifier)[1][0]
    with httpx.Client(transport=httpx.MockTransport(lambda r:response({"suggestions":[{"block_id":block.id,"quote":"张三","category":"姓名","reason":"联系人姓名"}]}))) as client:
        provider=EcnuClient(AIConfig(api_key="fake"),client)
        result=augment_with_ai(project,identifier,provider.config,consent=True,client=provider)
    assert result["run_id"]!=old_run and result["suggestions"]==1
    current=project.findings(identifier)
    assert len(current)==2
    inherited=next(f for f in current if f["rule_id"]=="phone")
    assert inherited["status"]=="confirmed" and inherited["note"]=="已核对"
    assert project.document(identifier)["review_status"]=="needs_review"
    assert ai_provenance(project,result["run_id"])["model"]=="ecnu-max"
    with pytest.raises(MingjianError):
        project.complete(identifier)
    assert len(project.db.rows("SELECT * FROM scan_runs"))==2


def test_ai_config_never_written_to_settings(web_client):  # noqa: F811 -- pytest injects the imported fixture
    client=web_client
    key="unit-ephemeral-credential"
    result=client.post("/api/ai/config",json={"api_key":key,"base_url":"https://chat.ecnu.edu.cn/open/api/v1","model":"ecnu-max"})
    assert result.status_code==200 and result.json()["configured"]
    assert key not in client.get("/api/bootstrap").text
    root=client.app.state.workspace.root
    assert key not in (root/"web-settings.json").read_text()
    changed=client.post("/api/ai/config",json={"base_url":"https://different.example/api","model":"model"})
    assert not changed.json()["configured"]
