import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mingjian.web.server import create_app


@pytest.fixture
def client(tmp_path):
    root = Path(__file__).resolve().parents[1]
    with TestClient(create_app(tmp_path/"web",root/"fixtures/ocr")) as session:
        token = session.get("/api/bootstrap").json()["token"]
        session.headers["X-Mingjian-Session"] = token
        yield session


def wait_job(client, identifier):
    for _ in range(300):
        job = next(j for j in client.get("/api/jobs").json() if j["id"]==identifier)
        if job["status"] not in ("queued","running"):
            return job
        time.sleep(.01)
    raise AssertionError("job did not complete")


def test_web_upload_scan_review_export(client):
    result = client.post("/api/uploads",files=[("files",("test.txt","😀联系电话13800138000\n邮箱review@example.org".encode(),"text/plain"))])
    assert result.status_code == 202
    job = wait_job(client,result.json()["id"])
    assert job["status"] == "success"
    identifier = job["result"]["imported"][0]
    detail = client.get(f"/api/documents/{identifier}").json()
    assert len(detail["findings"])==2
    assert detail["findings"][0]["start_offset"] == 5
    updates=[{"id":f["id"],"revision":f["revision"],"status":"confirmed"} for f in detail["findings"]]
    assert client.post(f"/api/documents/{identifier}/review",json={"updates":updates}).status_code==200
    assert client.post(f"/api/documents/{identifier}/complete").status_code==200
    result = client.post("/api/export",json={"documents":[identifier]})
    job = wait_job(client,result.json()["id"])
    assert job["status"]=="success" and job["result"]["validation"]["passed"]
    download=client.get(f"/api/exports/{job['result']['id']}/download")
    assert download.status_code==200 and download.content.startswith(b"PK")


def test_scans_never_pass_as_zero_risk(client):
    result=client.post("/api/samples/blur")
    job=wait_job(client,result.json()["id"])
    identifier=job["result"]["imported"][0]
    detail=client.get(f"/api/documents/{identifier}").json()
    assert detail["metadata"]["requires_ocr"]
    assert detail["warnings"] and not detail["findings"]
    assert client.post(f"/api/documents/{identifier}/complete").status_code==409
    assert client.post(f"/api/documents/{identifier}/scan",json={"acknowledge_coverage":True}).status_code==409
    assert client.post(f"/api/documents/{identifier}/ocr",json={"consent":False}).status_code==400


def test_photo_upload_is_ocr_pending(client):
    image=Image.new("RGB",(100,100),"white"); stream=io.BytesIO();image.save(stream,format="PNG")
    result=client.post("/api/uploads",files=[("files",("photo.png",stream.getvalue(),"image/png"))])
    job=wait_job(client,result.json()["id"])
    assert job["result"]["processed"][0]["state"]=="needs_ocr"


def test_local_security_and_no_arbitrary_file_read(client):
    assert client.post("/api/settings",json={"ocr_endpoint":"http://localhost:8003"},headers={"X-Mingjian-Session":"wrong"}).status_code==403
    assert client.post("/api/settings",json={"ocr_endpoint":"http://localhost:8003"},headers={"Origin":"https://evil.example"}).status_code==403
    assert client.get("/api/bootstrap",headers={"Host":"evil.example"}).status_code==400
    assert client.get("/api/samples/unknown/download").status_code==404
    assert client.get("/api/exports/unknown/download").status_code==404


def test_ocr_consent_requires_current_endpoint(client):
    result=client.post("/api/uploads",files=[("files",("test.txt",b"content","text/plain"))],data={"ocr":"true","consent_endpoint":"http://different:8003"})
    assert result.status_code==400


def test_source_preview_uses_verified_snapshot(client):
    result=client.post("/api/samples/scan")
    job=wait_job(client,result.json()["id"])
    identifier=job["result"]["imported"][0]
    preview=client.get(f"/api/documents/{identifier}/source-preview")
    assert preview.status_code==200 and preview.content.startswith(b"\x89PNG")
    assert client.get(f"/api/documents/{identifier}/source-preview?page=999").status_code==400


def test_ocr_response_can_enter_same_review_chain(client,monkeypatch):
    from mingjian.integrations.mineru import MinerUClient, normalize_result
    calls=[]
    def parse(self,path,limits=None,cancel=None,consent_endpoint=None):
        calls.append(consent_endpoint)
        return normalize_result([{"type":"text","text":"联系电话13800138000","page_idx":0}])
    monkeypatch.setattr(MinerUClient,"parse",parse)
    job=wait_job(client,client.post("/api/samples/blur").json()["id"])
    identifier=job["result"]["imported"][0]
    endpoint=client.get("/api/bootstrap").json()["settings"]["ocr_endpoint"]
    result=client.post(f"/api/documents/{identifier}/ocr",json={"consent":True,"endpoint":endpoint})
    job=wait_job(client,result.json()["id"])
    assert calls==[endpoint] and job["result"]["state"]=="needs_coverage"
    assert client.post(f"/api/documents/{identifier}/complete").status_code==409
    job=wait_job(client,client.post(f"/api/documents/{identifier}/scan",json={"acknowledge_coverage":True}).json()["id"])
    detail=client.get(f"/api/documents/{identifier}").json()
    assert len(detail["findings"])==1 and detail["metadata"]["ocr"]
