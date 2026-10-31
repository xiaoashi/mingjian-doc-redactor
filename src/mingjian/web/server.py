"""Loopback-only web UI over the existing versioned review services."""
from __future__ import annotations

import hmac
import json
import os
import secrets
import shutil
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mingjian.domain.models import Cancelled, MingjianError, VERSION, dumps, now, uid
from mingjian.ai.ecnu import AIConfig, EcnuClient, DEFAULT_AI_BASE, DEFAULT_AI_MODEL
from mingjian.integrations.mineru import DEFAULT_ENDPOINT, MinerUClient, validate_endpoint
from mingjian.parsers.documents import checkpoint
from mingjian.services.export import export_project
from mingjian.services.project import Project
from mingjian.services.ai import augment_with_ai

STATIC = Path(__file__).parent / "static"
IMAGES = {"png", "jpg", "jpeg", "webp", "tif", "tiff"}
SAMPLES = {
    "text": ("01_可提取文字.pdf", "文字 PDF", "不使用 OCR，自动检测隐私候选"),
    "scan": ("02_清晰扫描.pdf", "清晰扫描件", "只有图像，需要 OCR 提取文字"),
    "blur": ("03_模糊扫描.pdf", "模糊扫描件", "降采样与模糊，核对数字识别"),
    "photo": ("04_倾斜拍照.jpg", "模拟拍照图片", "透视倾斜和背景，测试中文 OCR"),
    "dark": ("05_低光照拍照.jpg", "低光照图片", "阴影和较低亮度，测试可读性"),
    "mixed": ("06_混合文档.pdf", "文字与扫描混合", "保留页码与未覆盖警告"),
    "control": ("07_无敏感项对照.pdf", "无敏感项对照", "用于检查误报和零命中流程"),
}


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.job_lock = threading.RLock()
        self.jobs = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mingjian-web")
        state_path = self.root / "workspace.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            folder = (self.root / state["project"]).resolve()
            if not folder.is_relative_to(self.root):
                raise MingjianError("工作区路径异常")
            self.project = Project(folder)
        else:
            self.project = Project.create(self.root, "我的文档")
            state_path.write_text(dumps({"project": self.project.root.name}), encoding="utf-8")
        self.settings_path = self.root / "web-settings.json"
        self.settings = {"ocr_endpoint": os.environ.get("MINERU_API_BASE_URL", DEFAULT_ENDPOINT), "ai_enabled": False}
        if self.settings_path.exists():
            self.settings.update(json.loads(self.settings_path.read_text(encoding="utf-8")))
        self.ai_config = AIConfig(os.environ.get("MINGJIAN_AI_BASE_URL", self.settings.get("ai_base_url", DEFAULT_AI_BASE)),
            os.environ.get("MINGJIAN_AI_MODEL", self.settings.get("ai_model", DEFAULT_AI_MODEL)), os.environ.get("MINGJIAN_AI_API_KEY", ""))

    def public_settings(self):
        return {"ocr_endpoint": self.settings["ocr_endpoint"], "ai_base_url": self.ai_config.base_url,
                "ai_model": self.ai_config.model, "ai_enabled": bool(self.ai_config.api_key)}

    def close(self):
        for job in self.jobs.values():
            job["cancel"].set()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.project.close()

    def public_job(self, job):
        with self.job_lock:
            return {key: value for key, value in job.items() if key != "cancel"}

    def submit(self, title, operation):
        identifier = uid()
        job = {"id": identifier, "title": title, "status": "queued", "stage": "等待处理", "done": 0, "total": 1,
               "created_at": now(), "cancel": threading.Event(), "result": None, "error": None}
        with self.job_lock:
            self.jobs[identifier] = job
        self.project.db.write(lambda db: db.execute("INSERT INTO tasks(id,kind,state,created_at) VALUES(?,?,'queued',?)", (identifier, title, now())))
        def run():
            try:
                job.update(status="running")
                self.project.db.write(lambda db: db.execute("UPDATE tasks SET state='running' WHERE id=?", (identifier,)))
                def progress(done, total, stage):
                    with self.job_lock:
                        job.update(done=done, total=total, stage=stage)
                checkpoint(job["cancel"])
                result = operation(job["cancel"], progress)
                checkpoint(job["cancel"])
                partial = bool(result.get("failures")) if isinstance(result, dict) else False
                job.update(status="partial_failure" if partial else "success", stage="已完成", result=result)
            except Exception as exc:
                job.update(status="cancelled" if isinstance(exc, Cancelled) else "failed",
                    error=str(exc) if isinstance(exc, MingjianError) else f"处理未完成（{type(exc).__name__}），请检查文件或服务后重试")
            finally:
                job["finished_at"] = now()
                self.project.db.write(lambda db: db.execute("UPDATE tasks SET state=?,progress=?,total=?,finished_at=?,detail_json=? WHERE id=?",
                    (job["status"], job["done"], job["total"], now(), dumps({"error": job["error"]}), identifier)))
        self.pool.submit(run)
        return self.public_job(job)

    def process_document(self, document_id, cancel, progress, *, use_ocr=False, consent_endpoint=None):
        project = self.project
        if use_ocr:
            endpoint = self.settings["ocr_endpoint"]
            if consent_endpoint != endpoint:
                raise MingjianError("OCR 地址已变化，请重新确认本批次文件发送授权")
            progress(0, 1, "正在使用 MinerU 识别图片文字")
            client = MinerUClient(endpoint)
            try:
                project.parse(document_id, cancel=cancel,
                    parser=lambda path, limits, event: client.parse(path, limits, event, consent_endpoint), parser_version="mineru-adapter-1.1")
            finally:
                client.close()
        else:
            progress(0, 1, "提取文档文字")
            project.parse(document_id, cancel=cancel)
        version, blocks = project.parsed(document_id)
        metadata = json.loads(version["metadata_json"])
        if metadata.get("requires_ocr") or not any(b.text.strip() for b in blocks):
            return {"id": document_id, "state": "needs_ocr"}
        if version["coverage_status"] == "partial" and not version["coverage_ack"]:
            return {"id": document_id, "state": "needs_coverage"}
        progress(0, 1, "识别敏感信息与文本问题")
        project.scan(document_id, cancel=cancel)
        return {"id": document_id, "state": "ready"}


def create_app(root=None, sample_root=None):
    workspace = Workspace(root or Path.home() / "Library/Application Support/MingjianWeb")
    samples = Path(sample_root) if sample_root else Path(__file__).resolve().parents[1] / "resources" / "ocr-samples"
    @asynccontextmanager
    async def lifespan(app):
        yield
        workspace.close()
    app = FastAPI(title="明鉴本地文档工作台", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.workspace = workspace
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_guard(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            token = request.headers.get("x-mingjian-session", "")
            origin = request.headers.get("origin")
            expected = str(request.base_url).rstrip("/")
            if not hmac.compare_digest(token, workspace.token) or (origin and origin != expected):
                return JSONResponse({"detail": "本机会话验证失败，请刷新页面"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(MingjianError)
    async def domain_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/bootstrap")
    def bootstrap():
        return {"service": "mingjian-web", "token": workspace.token, "version": VERSION, "project": workspace.project.info["name"],
                "settings": workspace.public_settings(), "samples": [{"id": k, "name": v[1], "description": v[2], "ocr": k not in ("text", "control"),
                    "available": (samples/v[0]).exists()} for k, v in SAMPLES.items()],
                "ai": workspace.ai_config.public()}

    @app.get("/api/documents")
    def list_documents():
        result = []
        project = workspace.project
        for doc in project.documents():
            counts = project.db.one("SELECT count(*) AS total,count(DISTINCT group_id) AS groups,sum(status='pending') AS pending FROM findings WHERE run_id=?", (doc["current_run_id"],))
            version = project.db.one("SELECT metadata_json,coverage_ack FROM parse_versions WHERE id=?", (doc["current_parse_id"],))
            metadata = json.loads(version["metadata_json"]) if version else {}
            requires_ocr = bool(metadata.get("requires_ocr"))
            if version and doc["format"] == "pdf" and "requires_ocr" not in metadata:
                has_text = project.db.one("SELECT id FROM text_blocks WHERE parse_id=? AND length(trim(text))>0 LIMIT 1", (doc["current_parse_id"],))
                requires_ocr = not bool(has_text)
            result.append({k: doc[k] for k in ("id", "display_name", "format", "size", "parse_status", "review_status", "revision", "error", "imported_at")}
                          | {"counts": counts, "ocr": bool(metadata.get("ocr")), "ocr_engine":metadata.get("engine"),"requires_ocr": requires_ocr,
                             "coverage_ack": bool(version and version["coverage_ack"])})
        return result

    @app.get("/api/documents/{document_id}")
    def document_detail(document_id: str):
        project = workspace.project
        doc = project.document(document_id)
        public = {k: doc[k] for k in ("id", "display_name", "format", "parse_status", "review_status", "revision", "error")}
        if doc["parse_status"] not in ("success", "partial"):
            return {"document": public, "blocks": [], "findings": [], "warnings": [], "metadata": {}, "coverage_ack": False}
        project.verify_source(document_id)
        version, blocks = project.parsed(document_id)
        return {"document": public, "blocks": [asdict(b) for b in blocks], "findings": project.findings(document_id),
                "warnings": json.loads(version["warnings_json"]), "metadata": json.loads(version["metadata_json"]),
                "coverage_ack": bool(version["coverage_ack"]), "parse_id": version["id"]}

    @app.get("/api/documents/{document_id}/source-preview")
    def source_preview(document_id: str, page: int = 1):
        project = workspace.project
        doc = project.document(document_id)
        path = project.verify_source(document_id)
        if doc["format"] in IMAGES:
            return FileResponse(path)
        if doc["format"] != "pdf":
            raise HTTPException(400, "此格式请查看解析文本")
        if not 1 <= page <= project.limits.max_pages:
            raise HTTPException(400, "页码超出范围")
        target = project.root / "cache" / f"{document_id}-source-page-{page}.png"
        if not target.exists():
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(path)
            try:
                if page > len(pdf):
                    raise HTTPException(400, "页码超出范围")
                sheet = pdf[page-1]
                bitmap = sheet.render(scale=1.5)
                try:
                    bitmap.to_pil().save(target)
                finally:
                    bitmap.close()
                    sheet.close()
            finally:
                pdf.close()
        return FileResponse(target)

    @app.post("/api/uploads", status_code=202)
    async def upload(files: list[UploadFile] = File(...), ocr: bool = Form(False), consent_endpoint: str = Form("")):
        if not 1 <= len(files) <= workspace.project.limits.max_files:
            raise HTTPException(400, "单次请选择 1—200 个文件")
        if ocr and consent_endpoint != workspace.settings["ocr_endpoint"]:
            raise HTTPException(400, "需要明确确认当前 OCR 服务地址与文件发送授权")
        folder = Path(tempfile.mkdtemp(prefix="upload-", dir=workspace.root))
        paths, names = [], {}
        try:
            for index, upload_file in enumerate(files):
                name = (upload_file.filename or "document").replace("\\", "/").rsplit("/", 1)[-1][:240]
                target = folder / (str(index) + Path(name).suffix.lower())
                size = 0
                with target.open("wb") as stream:
                    while data := await upload_file.read(1024**2):
                        size += len(data)
                        if size > workspace.project.limits.max_mib * 1024**2:
                            raise HTTPException(413, f"{name} 超过单文件大小限制")
                        stream.write(data)
                await upload_file.close()
                paths.append(target)
                names[str(target)] = name
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        def operation(cancel, progress):
            try:
                preview = workspace.project.import_preview(paths)
                for item in preview:
                    item["name"] = names[item["path"]]
                result = workspace.project.import_files(preview, cancel)
                result["failures"].extend({"name": p["name"], "message": p["reason"]} for p in preview if p["status"] == "rejected")
                processed = []
                for i, document_id in enumerate(result["imported"]):
                    checkpoint(cancel)
                    doc = workspace.project.document(document_id)
                    try:
                        state = workspace.process_document(document_id, cancel, progress,
                            use_ocr=ocr and doc["format"] in IMAGES | {"pdf"}, consent_endpoint=consent_endpoint)
                        processed.append(state)
                    except Cancelled:
                        raise
                    except Exception as exc:
                        result["failures"].append({"id": document_id, "message": str(exc) if isinstance(exc, MingjianError) else "文件处理失败"})
                    progress(i+1, len(result["imported"]), "正在处理文档")
                result["processed"] = processed
                return result
            finally:
                shutil.rmtree(folder, ignore_errors=True)
        return workspace.submit("导入并辅助识别", operation)

    @app.post("/api/samples/{sample_id}", status_code=202)
    def sample(sample_id: str):
        if sample_id not in SAMPLES or not (samples/SAMPLES[sample_id][0]).is_file():
            raise HTTPException(404, "示例文件不存在")
        path = samples / SAMPLES[sample_id][0]
        def operation(cancel, progress):
            preview = workspace.project.import_preview([path])
            result = workspace.project.import_files(preview, cancel)
            if not result["imported"]:
                existing = next((d for d in workspace.project.documents() if d["sha256"] == preview[0].get("sha256")), None)
                return {**result, "existing_id": existing["id"] if existing else None}
            identifier = result["imported"][0]
            state = workspace.process_document(identifier, cancel, progress)
            return {**result, "processed": [state]}
        return workspace.submit("试用合成示例", operation)

    @app.get("/api/samples/{sample_id}/download")
    def download_sample(sample_id: str):
        if sample_id not in SAMPLES or not (samples/SAMPLES[sample_id][0]).is_file():
            raise HTTPException(404)
        return FileResponse(samples/SAMPLES[sample_id][0], filename=SAMPLES[sample_id][0])

    @app.post("/api/documents/{document_id}/ocr", status_code=202)
    async def ocr_document(document_id: str, request: Request):
        body = await request.json()
        if not body.get("consent") or body.get("endpoint") != workspace.settings["ocr_endpoint"]:
            raise HTTPException(400, "请确认本次文件将发送到指定 OCR 服务")
        doc = workspace.project.document(document_id)
        if doc["format"] not in IMAGES | {"pdf"}:
            raise HTTPException(400, "OCR 适用于 PDF 或图片")
        return workspace.submit("OCR 文字识别", lambda cancel, progress: workspace.process_document(
            document_id, cancel, progress, use_ocr=True, consent_endpoint=body["endpoint"]))

    @app.post("/api/documents/{document_id}/scan", status_code=202)
    async def scan_document(document_id: str, request: Request):
        body = await request.json()
        version, blocks = workspace.project.parsed(document_id)
        if not any(b.text.strip() for b in blocks):
            raise MingjianError("尚未提取出文字，请先使用 OCR")
        if version["coverage_status"] == "partial" and not body.get("acknowledge_coverage"):
            raise MingjianError("请明确确认已核对当前文档的覆盖限制")
        if version["coverage_status"] == "partial":
            workspace.project.acknowledge_coverage(document_id)
        return workspace.submit("识别敏感信息", lambda cancel, progress: {"id": document_id, "run_id": workspace.project.scan(document_id, cancel=cancel)})

    @app.post("/api/documents/{document_id}/ai-ocr", status_code=202)
    async def ai_ocr(document_id: str, request: Request):
        body = await request.json()
        config = workspace.ai_config
        if not body.get("consent") or body.get("base_url") != config.base_url or body.get("model") != config.model:
            raise HTTPException(400, "请明确确认本次图像将发送到配置的模型服务")
        if not config.api_key:
            raise MingjianError("请自行准备大模型 API 并在设置中填写，本软件交付不附带密钥")
        doc = workspace.project.document(document_id)
        if doc["format"] not in IMAGES | {"pdf", "docx"}:
            raise HTTPException(400, "视觉识别适用于图片、PDF和DOCX嵌入图片")
        def operation(cancel, progress):
            progress(0, 1, "模型正在读取文档图像")
            provider = EcnuClient(config)
            try:
                workspace.project.parse(document_id, cancel=cancel, parser=provider.parse_file, parser_version="ecnu-vision-adapter-1.2")
            finally:
                provider.close()
            version, _ = workspace.project.parsed(document_id)
            if version["coverage_status"] == "complete":
                workspace.project.scan(document_id,cancel=cancel)
            return {"id":document_id,"state":"needs_coverage" if version["coverage_status"]=="partial" else "ready",
                    "vision":True,"usage":provider.usage_total}
        return workspace.submit("AI视觉文字识别",operation)

    @app.post("/api/documents/{document_id}/ai-review", status_code=202)
    async def ai_review(document_id: str, request: Request):
        body = await request.json()
        config = workspace.ai_config
        if not body.get("consent") or body.get("base_url") != config.base_url or body.get("model") != config.model:
            raise HTTPException(400, "请明确确认本次解析文字的发送范围与模型服务地址")
        if not config.api_key:
            raise MingjianError("请自行配置大模型 API，本软件不包含可用密钥")
        def operation(cancel, progress):
            progress(0, 1, "模型正在复核文档文字")
            return augment_with_ai(workspace.project,document_id,config,consent=True,cancel=cancel)
        return workspace.submit("AI语义辅助复核",operation)

    @app.post("/api/documents/{document_id}/review")
    async def review(document_id: str, request: Request):
        body = await request.json()
        updates = body.get("updates")
        if not isinstance(updates, list) or not updates or len(updates) > 2000:
            raise HTTPException(400, "请选择需要复核的问题")
        try:
            workspace.project.review_many(document_id, updates)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, "复核参数无效") from exc
        return document_detail(document_id)

    @app.post("/api/documents/{document_id}/undo")
    async def undo(document_id: str, request: Request):
        workspace.project.undo(document_id, bool((await request.json()).get("redo")))
        return document_detail(document_id)

    @app.get("/api/documents/{document_id}/preview")
    def preview(document_id: str):
        return workspace.project.preview(document_id)

    @app.post("/api/documents/{document_id}/complete")
    def complete(document_id: str):
        workspace.project.complete(document_id)
        return document_detail(document_id)

    @app.post("/api/export", status_code=202)
    async def export(request: Request):
        body = await request.json()
        ids = body.get("documents", [])
        if not isinstance(ids, list) or not ids:
            raise HTTPException(400, "请选择已完成复核的文档")
        return workspace.submit("生成脱敏副本与报告", lambda cancel, progress: export_project(
            workspace.project, ids, formats=body.get("formats", ["txt", "docx", "pdf"]), cancel=cancel, progress=progress))

    @app.get("/api/exports")
    def exports():
        rows = workspace.project.db.rows("SELECT id,status,created_at,error FROM export_records ORDER BY created_at DESC")
        return rows

    @app.get("/api/exports/{identifier}/download")
    def download_export(identifier: str):
        record = workspace.project.db.one("SELECT * FROM export_records WHERE id=? AND status='success'", (identifier,))
        if not record:
            raise HTTPException(404, "不存在已校验的导出结果")
        folder = Path(record["output_path"]).resolve()
        if not folder.is_relative_to(workspace.project.root / "exports") or not folder.is_dir():
            raise HTTPException(404, "导出目录已被移除")
        target = workspace.project.root / "cache" / (identifier + ".zip")
        if not target.exists():
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in folder.rglob("*"):
                    if path.is_file():
                        archive.write(path, str(path.relative_to(folder)))
        return FileResponse(target, filename="明鉴-脱敏副本与报告.zip")

    @app.get("/api/jobs")
    def jobs():
        return [workspace.public_job(job) for job in list(workspace.jobs.values())[-30:]][::-1]

    @app.post("/api/jobs/{identifier}/cancel")
    def cancel_job(identifier: str):
        if identifier not in workspace.jobs:
            raise HTTPException(404)
        workspace.jobs[identifier]["cancel"].set()
        return {"requested": True}

    @app.get("/api/rules")
    def rules():
        snapshot, config = workspace.project.rules()
        return {"version": snapshot["version"], "config": config}

    @app.post("/api/rules")
    async def save_rules(request: Request):
        body = await request.json()
        workspace.project.publish_rules(body["config"])
        return rules()

    @app.post("/api/settings")
    async def settings(request: Request):
        body = await request.json()
        endpoint = validate_endpoint(body.get("ocr_endpoint", workspace.settings["ocr_endpoint"]))
        workspace.settings.update(ocr_endpoint=endpoint, ai_enabled=False)
        workspace.settings_path.write_text(dumps(workspace.settings), encoding="utf-8")
        return workspace.public_settings()

    @app.post("/api/ai/config")
    async def ai_config(request: Request):
        body=await request.json()
        old=workspace.ai_config
        base=body.get("base_url",old.base_url)
        key=body.get("api_key") or (old.api_key if base.rstrip("/")==old.base_url else "")
        if body.get("clear_key"):
            key=""
        if not isinstance(key,str) or len(key)>500:
            raise HTTPException(400,"密钥格式无效")
        workspace.ai_config=AIConfig(base,body.get("model",old.model),key)
        workspace.settings.update(ai_base_url=workspace.ai_config.base_url,ai_model=workspace.ai_config.model)
        # Persist an allowlist; credentials can never enter this settings file.
        public={"ocr_endpoint":workspace.settings["ocr_endpoint"],"ai_base_url":workspace.ai_config.base_url,"ai_model":workspace.ai_config.model}
        workspace.settings_path.write_text(dumps(public),encoding="utf-8")
        return workspace.ai_config.public()

    @app.post("/api/ai/probe")
    def ai_probe():
        provider=EcnuClient(workspace.ai_config)
        try:
            result=provider.chat("只输出JSON对象。","返回 {\"ok\":true}。",max_tokens=128)
            return {"available":result.get("ok") is True,"model":workspace.ai_config.model,"usage":provider.last_usage}
        finally:
            provider.close()

    @app.post("/api/ocr/health")
    def ocr_health():
        client = MinerUClient(workspace.settings["ocr_endpoint"])
        try:
            return client.health()
        finally:
            client.close()

    @app.post("/api/shutdown")
    def shutdown():
        if any(job["status"] in ("running", "queued") for job in workspace.jobs.values()):
            raise MingjianError("请先等待任务结束或取消，再停止本机服务")
        callback = getattr(app.state, "shutdown_callback", None)
        if callback is None:
            raise MingjianError("当前环境请从启动窗口停止服务")
        threading.Timer(.3, callback).start()
        return {"stopping": True}

    if (STATIC / "assets").exists():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")
    font_root = Path(__file__).resolve().parents[1] / "resources" / "fonts"
    app.mount("/fonts", StaticFiles(directory=font_root), name="fonts")

    @app.get("/{path:path}")
    def index(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        if not (STATIC/"index.html").is_file():
            return JSONResponse({"message": "前端尚未构建，请在 frontend 中运行 npm run build"}, status_code=503)
        return FileResponse(STATIC/"index.html")
    return app
