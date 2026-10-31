"""Verify the actual packaged web application, without external OCR calls."""
import json
import subprocess
import tempfile
import time
import zipfile
import io
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def verify():
    executable = ROOT / "dist/明鉴网页.app/Contents/MacOS/明鉴网页"
    checks = []
    with tempfile.TemporaryDirectory(prefix="mingjian-web-bundle-") as tmp:
        with (ROOT / "docs/web-bundle-check.log").open("w") as log:
            process = subprocess.Popen([str(executable), "--no-browser", "--port", "8766", "--workspace", tmp], stdout=log, stderr=log)
            try:
                with httpx.Client(base_url="http://127.0.0.1:8766",trust_env=False,timeout=10) as client:
                    for _ in range(150):
                        if process.poll() is not None:
                            raise RuntimeError("打包应用提前退出")
                        try:
                            boot = client.get("/api/bootstrap")
                            if boot.status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(.1)
                    else:
                        raise RuntimeError("打包服务启动超时")
                    data = boot.json()
                    assert data["service"]=="mingjian-web" and data["version"]=="1.1.0"
                    client.headers["X-Mingjian-Session"] = data["token"]
                    checks.append({"check":"packaged_service_boot","passed":True})
                    assert 'id="root"' in client.get("/").text
                    assert client.get("/fonts/NotoSansSC-Regular.ttf").status_code==200
                    assert all(s["available"] for s in data["samples"])
                    checks.append({"check":"frontend_fonts_samples_bundled","passed":True})
                    def wait(identifier):
                        for _ in range(200):
                            job = next(j for j in client.get("/api/jobs").json() if j["id"]==identifier)
                            if job["status"] not in ("queued","running"):
                                assert job["status"]=="success",job.get("error")
                                return job
                            time.sleep(.1)
                        raise RuntimeError("诊断任务超时")
                    job=wait(client.post("/api/samples/text").json()["id"])
                    identifier=job["result"]["imported"][0]
                    detail=client.get(f"/api/documents/{identifier}").json()
                    assert len(detail["findings"])==4
                    checks.append({"check":"drop_equivalent_auto_text_scan","passed":True})
                    assert client.get(f"/api/documents/{identifier}/source-preview").content.startswith(b"\x89PNG")
                    updates=[{"id":f["id"],"revision":f["revision"],"status":"confirmed"} for f in detail["findings"]]
                    assert client.post(f"/api/documents/{identifier}/review",json={"updates":updates}).status_code==200
                    assert client.post(f"/api/documents/{identifier}/complete").status_code==200
                    job=wait(client.post("/api/export",json={"documents":[identifier]}).json()["id"])
                    assert job["result"]["validation"]["passed"]
                    archive=client.get(f"/api/exports/{job['result']['id']}/download").content
                    with zipfile.ZipFile(io.BytesIO(archive)) as z:
                        assert z.testzip() is None
                        assert {"copies/document-001.txt","copies/document-001.docx","copies/document-001.pdf","manifest.json","validation.json"}<=set(z.namelist())
                    checks.append({"check":"review_three_format_export_download_validation","passed":True})
                    job=wait(client.post("/api/samples/blur").json()["id"])
                    image_id=job["result"]["imported"][0]
                    detail=client.get(f"/api/documents/{image_id}").json()
                    assert detail["metadata"]["requires_ocr"] and not detail["findings"]
                    assert client.post(f"/api/documents/{image_id}/complete").status_code==409
                    checks.append({"check":"scanned_pdf_cannot_pass_without_ocr","passed":True})
                    assert client.post("/api/shutdown").status_code==200
                process.wait(timeout=15)
                assert process.returncode==0
                checks.append({"check":"clean_shutdown","passed":True})
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
    result={"version":"1.1.0","passed":all(c["passed"] for c in checks),"checks":checks,
            "ocr_live_test":"用户明确要求推迟至后续优化；未调用远程 OCR 或 AI"}
    (ROOT/"docs/web-bundle-check.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":
    verify()
