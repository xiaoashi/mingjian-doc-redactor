import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  BadgeCheck,
  BookOpen,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Clock3,
  CloudUpload,
  FileCheck2,
  FileImage,
  FileSearch,
  FileText,
  FolderOpen,
  History,
  Image,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  MoreHorizontal,
  ScanLine,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Undo2,
  Upload,
  X,
} from "lucide-react";
import { api, post, setToken } from "./api";
import type { Block, Detail, Doc, Finding, Job } from "./api";
import "./style.css";

type Page = "home" | "files" | "review" | "history" | "rules";
const statusName: Record<string, string> = {
  pending: "待复核",
  confirmed: "已确认",
  ignored: "已忽略",
};
const ruleNames: Record<string, string> = {
  phone: "手机号",
  identity: "身份证号",
  email: "邮箱",
  bank: "银行卡号",
  punctuation: "重复标点",
  pairs: "符号配对",
  numbering: "章节编号",
};
const activeJob = (job: Job) => ["queued", "running"].includes(job.status);
const fileSize = (size: number) =>
  size < 1048576
    ? `${Math.max(1, Math.round(size / 1024))} KB`
    : `${(size / 1048576).toFixed(1)} MB`;
const date = (value: string) =>
  new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });

function FileIcon({
  format,
  small = false,
}: {
  format: string;
  small?: boolean;
}) {
  const picture = ["png", "jpeg", "jpg", "webp", "tiff", "tif"].includes(
    format,
  );
  return (
    <span
      className={`file-icon ${format === "pdf" ? "pdf" : picture ? "picture" : "word"} ${small ? "small" : ""}`}
    >
      {picture ? (
        <FileImage size={small ? 18 : 23} />
      ) : (
        <FileText size={small ? 18 : 23} />
      )}
      <span>{format.toUpperCase()}</span>
    </span>
  );
}
function docState(doc: Doc) {
  if (doc.error) return { name: "处理未完成", style: "error" };
  if (doc.parse_status === "running")
    return { name: "正在解析", style: "processing" };
  if (doc.requires_ocr) return { name: "需要 OCR", style: "warning" };
  if (doc.parse_status === "partial" && !doc.coverage_ack)
    return { name: "待核对文字", style: "warning" };
  if (doc.review_status === "completed")
    return { name: "已完成复核", style: "complete" };
  if (doc.review_status === "in_review" || doc.review_status === "needs_review")
    return { name: "待人工复核", style: "pending" };
  return { name: "等待识别", style: "neutral" };
}

function App() {
  const [boot, setBoot] = useState<any>(null);
  const [page, setPage] = useState<Page>("home");
  const [docs, setDocs] = useState<Doc[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState("");
  const [failure, setFailure] = useState("");
  const [saving, setSaving] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [useOCR, setUseOCR] = useState(false);
  const [settings, setSettings] = useState(false);
  const [endpoint, setEndpoint] = useState("");
  const [aiBase, setAIBase] = useState("https://chat.ecnu.edu.cn/open/api/v1");
  const [aiModel, setAIModel] = useState("ecnu-max");
  const [aiKey, setAIKey] = useState("");
  const [aiAction, setAIAction] = useState<"vision" | "review" | null>(null);
  const [aiConsent, setAIConsent] = useState(false);
  const [aiProbing, setAIProbing] = useState(false);
  const [health, setHealth] = useState<string | null>(null);
  const [checkingHealth, setCheckingHealth] = useState(false);
  const [ocrAction, setOCRAction] = useState<{
    files?: File[];
    id?: string;
  } | null>(null);
  const [consent, setConsent] = useState(false);
  const [ignore, setIgnore] = useState(false);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [strategy, setStrategy] = useState("full");
  const [prefix, setPrefix] = useState(3);
  const [suffix, setSuffix] = useState(4);
  const [fixed, setFixed] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [showSource, setShowSource] = useState(false);
  const [sourcePage, setSourcePage] = useState(1);
  const [exports, setExports] = useState<any[]>([]);
  const [rules, setRules] = useState<any>(null);
  const [word, setWord] = useState("");
  const [wordRisk, setWordRisk] = useState("高");
  const [sampleMenu, setSampleMenu] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const modalRef = useRef<HTMLElement | null>(null);
  const selectedDocRef = useRef(selectedDoc);
  const seen = useRef(new Set<string>());
  selectedDocRef.current = selectedDoc;
  const busy = jobs.some(activeJob);
  const current = detail?.findings.find((f) => f.id === selected) || null;
  const pending =
    detail?.findings.filter((f) => f.status === "pending").length || 0;
  const visibleFindings =
    detail?.findings.filter(
      (f) => filter === "all" || f.status === filter || f.risk === filter,
    ) || [];

  const notifyError = useCallback(
    (e: unknown) =>
      setFailure(e instanceof Error ? e.message : "操作未完成，请重试"),
    [],
  );
  const run = async (operation: () => Promise<unknown>) => {
    try {
      await operation();
    } catch (e) {
      notifyError(e);
    }
  };
  const refresh = useCallback(async () => {
    const items = await api<Doc[]>("/documents");
    setDocs(items);
    return items;
  }, []);
  const loadDetail = useCallback(async (id: string, keep = false) => {
    const result = await api<Detail>("/documents/" + id);
    setDetail(result);
    if (keep)
      setSelected((id) =>
        result.findings.some((f) => f.id === id)
          ? id
          : result.findings.find((f) => f.status === "pending")?.id ||
            result.findings[0]?.id ||
            null,
      );
    if (!keep) {
      setSourcePage(1);
      setShowSource(!result.blocks.some((b) => b.text.trim()));
      setSelected(
        result.findings.find((f) => f.status === "pending")?.id ||
          result.findings[0]?.id ||
          null,
      );
      setPreview(null);
      setFilter("all");
    }
    return result;
  }, []);
  const openDoc = async (id: string) => {
    await saveIfDirty();
    setSelectedDoc(id);
    setShowSource(false);
    setPage("review");
    await loadDetail(id);
  };
  useEffect(() => {
    api("/bootstrap")
      .then(async (data) => {
        setToken(data.token);
        const oldJobs = await api<Job[]>("/jobs");
        oldJobs
          .filter((j) => !activeJob(j))
          .forEach((j) => seen.current.add(j.id));
        setJobs(oldJobs);
        setBoot(data);
        setEndpoint(data.settings.ocr_endpoint);
        setAIBase(data.ai.base_url);
        setAIModel(data.ai.model);
        return refresh();
      })
      .catch(notifyError);
  }, [refresh, notifyError]);
  useEffect(() => {
    if (!boot) return;
    let running = true;
    const poll = async () => {
      try {
        const result = await api<Job[]>("/jobs");
        if (!running) return;
        setJobs(result);
        for (const job of result) {
          if (!activeJob(job) && !seen.current.has(job.id)) {
            seen.current.add(job.id);
            await refresh();
            if (selectedDocRef.current)
              await loadDetail(selectedDocRef.current, true);
            if (job.error) setFailure(job.error);
            else if (job.result?.failures?.length)
              setFailure(
                job.result.failures
                  .map((f: any) => f.message || f.reason || "部分文件处理失败")
                  .join("；"),
              );
            else if (job.result?.validation?.passed) {
              setToast("脱敏副本和报告已通过回查，可以下载");
              setExports(await api("/exports"));
            } else if (
              job.result?.imported?.length ||
              job.result?.existing_id
            ) {
              const id = job.result.existing_id || job.result.imported[0];
              setSelectedDoc(id);
              setPage("review");
              await loadDetail(id);
            }
          }
        }
      } catch (e) {
        if (running) notifyError(e);
      }
    };
    poll();
    const timer = window.setInterval(poll, 1200);
    return () => {
      running = false;
      clearInterval(timer);
    };
  }, [boot, refresh, loadDetail, notifyError]);
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(""), 5000);
      return () => clearTimeout(timer);
    }
  }, [toast]);
  useEffect(() => {
    setNote(current?.note || "");
    setStrategy(current?.strategy.mode || "full");
    setPrefix(current?.strategy.prefix ?? 3);
    setSuffix(current?.strategy.suffix ?? 4);
    setFixed(current?.strategy.text || "");
    if (current)
      setSourcePage(
        detail?.blocks.find((b) => b.id === current.block_id)?.locator.page ||
          1,
      );
    if (current)
      requestAnimationFrame(() =>
        document
          .getElementById("finding-" + current.id)
          ?.scrollIntoView({
            behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
              ? "instant"
              : "smooth",
            block: "center",
          }),
      );
  }, [selected, current?.revision]);
  useEffect(() => {
    const modalOpen = settings || !!ocrAction || !!aiAction || ignore;
    const previousFocus = document.activeElement as HTMLElement | null;
    if (modalOpen)
      requestAnimationFrame(() =>
        modalRef.current
          ?.querySelector<HTMLElement>("button:not(:disabled),input,textarea")
          ?.focus(),
      );
    const keys = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSettings(false);
        setOCRAction(null);
        setAIAction(null);
        setIgnore(false);
        setSampleMenu(false);
      }
      if (e.key === "Tab" && modalOpen && modalRef.current) {
        const controls = Array.from(
          modalRef.current.querySelectorAll<HTMLElement>(
            "button:not(:disabled),input,textarea,select,a[href]",
          ),
        );
        const first = controls[0],
          last = controls[controls.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", keys);
    return () => {
      document.removeEventListener("keydown", keys);
      if (modalOpen) previousFocus?.focus();
    };
  }, [settings, ocrAction, aiAction, ignore]);

  async function upload(files: File[], authorized = false) {
    if (!files.length) return;
    if (busy) {
      setFailure("请等待当前任务完成后再添加文件");
      return;
    }
    if (
      useOCR &&
      !authorized &&
      files.some((f) => /\.(pdf|png|jpe?g|webp|tiff?)$/i.test(f.name))
    ) {
      setOCRAction({ files });
      setConsent(false);
      return;
    }
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    form.append("ocr", String(authorized));
    form.append("consent_endpoint", authorized ? endpoint : "");
    const job = await api<Job>("/uploads", { method: "POST", body: form });
    setJobs((old) => [job, ...old]);
    setToast("文件已加入本机工作区，开始辅助识别");
    if (input.current) input.current.value = "";
  }
  async function authorizeOCR() {
    if (!consent || !ocrAction) return;
    const action = ocrAction;
    setOCRAction(null);
    if (action.files) await upload(action.files, true);
    else if (action.id) {
      const job = await post<Job>(`/documents/${action.id}/ocr`, {
        consent: true,
        endpoint,
      });
      setJobs((old) => [job, ...old]);
    }
  }
  async function review(status: string | null, ignoreReason = "") {
    if (!current || !selectedDoc) return;
    setSaving(true);
    try {
      const replacement =
        strategy === "partial"
          ? { mode: strategy, prefix, suffix }
          : strategy === "fixed"
            ? { mode: strategy, text: fixed }
            : { mode: strategy };
      const update: any = {
        id: current.id,
        revision: current.revision,
        note,
        strategy: replacement,
      };
      if (status) update.status = status;
      if (status === "ignored") update.reason = ignoreReason;
      const result = await post<Detail>(`/documents/${selectedDoc}/review`, {
        updates: [update],
      });
      setDetail(result);
      await refresh();
      setToast(status ? "复核结果已保存" : "备注与策略已保存");
      setIgnore(false);
      setPreview(null);
      if (status) {
        const index = result.findings.findIndex((f) => f.id === current.id);
        const next =
          result.findings
            .slice(index + 1)
            .find((f) => f.status === "pending") ||
          result.findings.find((f) => f.status === "pending");
        if (next) setSelected(next.id);
      }
    } finally {
      setSaving(false);
    }
  }
  function isDirty() {
    if (!current) return false;
    return (
      note !== current.note ||
      strategy !== current.strategy.mode ||
      (strategy === "partial" &&
        (prefix !== (current.strategy.prefix ?? 3) ||
          suffix !== (current.strategy.suffix ?? 4))) ||
      (strategy === "fixed" && fixed !== (current.strategy.text || ""))
    );
  }
  async function saveIfDirty() {
    if (isDirty()) await review(null);
  }
  async function chooseFinding(id: string) {
    await saveIfDirty();
    setSelected(id);
  }
  async function saveAIConfig() {
    const config = await post("/ai/config", {
      base_url: aiBase,
      model: aiModel,
      api_key: aiKey,
    });
    setAIKey("");
    setBoot({ ...boot, ai: config });
    setToast("模型地址已保存；密钥仅保留在本次运行内存");
    return config;
  }
  async function runAIAction() {
    if (!aiAction || !selectedDoc || !aiConsent) return;
    await saveIfDirty();
    const kind = aiAction;
    setAIAction(null);
    const job = await post<Job>(
      `/documents/${selectedDoc}/${kind === "vision" ? "ai-ocr" : "ai-review"}`,
      { consent: true, base_url: boot.ai.base_url, model: boot.ai.model },
    );
    setJobs((old) => [job, ...old]);
  }
  async function saveSettings() {
    const result = await post("/settings", { ocr_endpoint: endpoint });
    setBoot({ ...boot, settings: result });
    setEndpoint(result.ocr_endpoint);
    setHealth(null);
    setToast("识别设置已保存");
  }
  async function go(next: Page) {
    await saveIfDirty();
    setPage(next);
    if (next === "history") setExports(await api("/exports"));
    if (next === "rules") setRules(await api("/rules"));
    if (next === "files") await refresh();
  }
  async function sample(id: string) {
    setSampleMenu(false);
    const job = await post<Job>("/samples/" + id);
    setJobs((old) => [job, ...old]);
  }
  async function acknowledge() {
    if (!selectedDoc) return;
    const job = await post<Job>(`/documents/${selectedDoc}/scan`, {
      acknowledge_coverage: true,
    });
    setJobs((old) => [job, ...old]);
  }

  function documentsTable(limit?: number) {
    const result = docs
      .filter((d) => d.display_name.toLowerCase().includes(query.toLowerCase()))
      .slice()
      .reverse();
    const items = limit ? result.slice(0, limit) : result;
    return (
      <div className="document-table">
        <div className="table-head">
          <span>文档名称</span>
          <span>识别方式</span>
          <span>问题组</span>
          <span>状态</span>
          <span>添加时间</span>
          <span />
        </div>
        {items.map((doc) => {
          const state = docState(doc);
          return (
            <button
              className="document-row"
              key={doc.id}
              onClick={() => run(() => openDoc(doc.id))}
            >
              <span className="document-name">
                <FileIcon format={doc.format} small />
                <span>
                  <strong>{doc.display_name}</strong>
                  <small>{fileSize(doc.size)}</small>
                </span>
              </span>
              <span className="method">
                {doc.ocr ? (
                  <ScanLine size={15} />
                ) : doc.requires_ocr ? (
                  <Image size={15} />
                ) : (
                  <FileSearch size={15} />
                )}{" "}
                {doc.ocr
                  ? doc.ocr_engine || "OCR"
                  : doc.requires_ocr
                    ? "待 OCR"
                    : "本地提取"}
              </span>
              <span className="count">
                {doc.counts.groups ||
                  (doc.review_status === "in_review" ||
                  doc.review_status === "completed"
                    ? "0"
                    : "—")}
              </span>
              <span>
                <span className={"badge " + state.style}>{state.name}</span>
              </span>
              <span className="muted">{date(doc.imported_at)}</span>
              <ChevronRight size={17} />
            </button>
          );
        })}
        {!items.length && (
          <div className="empty-table">
            <FolderOpen size={30} />
            <p>
              {query
                ? "没有找到匹配的文档"
                : "还没有文档，拖入一份文件就可以开始"}
            </p>
          </div>
        )}
      </div>
    );
  }

  function documentText(block: Block) {
    if (preview) return preview.texts[block.id] ?? block.text;
    const hits = (detail?.findings || []).filter(
      (f) => f.block_id === block.id,
    );
    const points = new Set([0, Array.from(block.text).length]);
    hits.forEach((f) => {
      points.add(f.start_offset);
      points.add(f.end_offset);
    });
    const chars = Array.from(block.text);
    return [...points]
      .sort((a, b) => a - b)
      .slice(0, -1)
      .map((start, i, arr) => {
        const end = [...points].sort((a, b) => a - b)[i + 1];
        const hit =
          hits.find(
            (f) =>
              f.start_offset <= start &&
              f.end_offset >= end &&
              f.id === selected,
          ) || hits.find((f) => f.start_offset <= start && f.end_offset >= end);
        const text = chars.slice(start, end).join("");
        return hit ? (
          <mark
            key={start}
            id={hit.start_offset === start ? "finding-" + hit.id : undefined}
            className={`${hit.category === "文本规范" ? "format" : hit.category === "敏感词" ? "word" : "privacy"} ${hit.id === selected ? "active" : ""} ${hit.status === "ignored" ? "ignored" : ""}`}
            onClick={() => run(() => chooseFinding(hit.id))}
            tabIndex={0}
            role="button"
            onKeyDown={(e) => {
              if (e.key === "Enter") run(() => chooseFinding(hit.id));
            }}
            aria-label={hit.category + "，" + statusName[hit.status]}
          >
            {text}
          </mark>
        ) : (
          text
        );
      });
  }

  if (!boot)
    return (
      <div className="startup">
        <ShieldCheck size={34} />
        <p>{failure || "正在打开明鉴本机工作区…"}</p>
      </div>
    );
  return (
    <div className="app-shell">
      <input
        ref={input}
        type="file"
        multiple
        accept=".pdf,.docx,.txt,.png,.jpg,.jpeg,.webp,.tif,.tiff"
        className="visually-hidden"
        onChange={(e) => run(() => upload(Array.from(e.target.files || [])))}
      />
      <aside
        className="sidebar"
        inert={settings || !!ocrAction || !!aiAction || ignore}
      >
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            run(() => go("home"));
          }}
        >
          <span className="brand-symbol">
            <ShieldCheck size={25} />
          </span>
          <span>
            明鉴<small>文档审查助手</small>
          </span>
        </a>
        <button
          className="new-document"
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          <Upload size={17} /> 添加文档 <span>+</span>
        </button>
        <nav aria-label="主导航">
          {(
            [
              ["home", "工作台", LayoutDashboard],
              ["files", "我的文档", FolderOpen],
              ["rules", "识别规则", SlidersHorizontal],
              ["history", "导出记录", History],
            ] as const
          ).map(([key, name, Icon]) => (
            <button
              key={key}
              aria-label={name}
              className={
                page === key || (key === "files" && page === "review")
                  ? "nav-item active"
                  : "nav-item"
              }
              onClick={() => run(() => go(key))}
            >
              <Icon size={19} />
              <span>{name}</span>
              {key === "files" && <small>{docs.length}</small>}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <LockKeyhole size={18} />
          <strong>你的文档，留在你的掌控中</strong>
          <p>普通文档在本机处理。远程 OCR 仅在你确认后使用。</p>
        </div>
        <div className="sidebar-bottom">
          <button onClick={() => setSettings(true)}>
            <Settings2 size={18} /> 识别设置
          </button>
          <span className="version">
            V{boot.version} <i /> 本机运行
          </span>
        </div>
      </aside>
      <div
        className="main-shell"
        inert={settings || !!ocrAction || !!aiAction || ignore}
      >
        <header className="topbar">
          <div className="breadcrumb">
            工作空间 <ChevronRight size={14} />{" "}
            <strong>
              {page === "review"
                ? "文档复核"
                : page === "files"
                  ? "我的文档"
                  : page === "rules"
                    ? "识别规则"
                    : page === "history"
                      ? "导出记录"
                      : "工作台"}
            </strong>
          </div>
          <span className="local-status">
            <span />
            本机工作区
          </span>
          <button
            className="icon-button"
            aria-label="打开识别设置"
            onClick={() => setSettings(true)}
          >
            <Settings2 size={19} />
          </button>
        </header>
        {jobs.filter(activeJob).map((job) => (
          <div className="task-banner" key={job.id} role="status">
            <LoaderCircle size={18} className="spin" />
            <strong>{job.title}</strong>
            <span>{job.stage}</span>
            <span className="job-counter">
              {job.done}/{job.total}
            </span>
            <button
              onClick={() =>
                run(async () => {
                  await post("/jobs/" + job.id + "/cancel");
                  setToast("已请求取消，将在安全检查点停止");
                })
              }
            >
              取消任务
            </button>
          </div>
        ))}
        {failure && (
          <div className="error-banner" role="alert">
            <CircleAlert size={19} />
            <span>{failure}</span>
            <button
              className="icon-button"
              aria-label="关闭错误提示"
              onClick={() => setFailure("")}
            >
              <X size={17} />
            </button>
          </div>
        )}
        {(page === "home" || page === "files") && (
          <main className="workspace-home">
            {page === "home" ? (
              <>
                <div className="welcome">
                  <div>
                    <h1>
                      让每一份文档，
                      <br />
                      都经得起仔细看。
                    </h1>
                    <p>
                      放入文件，自动发现敏感信息。
                      <br className="mobile-break" />
                      清楚地识别，从容地复核。
                    </p>
                  </div>
                  <div className="welcome-note">
                    <span className="soft-icon">
                      <FileCheck2 size={28} />
                    </span>
                    <span>
                      从原文到脱敏副本<strong>每一步，都有据可查</strong>
                    </span>
                  </div>
                </div>
                <div className="upload-layout">
                  <section
                    className={"dropzone " + (dragging ? "dragging" : "")}
                    aria-label="文档上传区域"
                    onDragOver={(e) => {
                      e.preventDefault();
                      setDragging(true);
                    }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={(e) => {
                      e.preventDefault();
                      setDragging(false);
                      run(() => upload(Array.from(e.dataTransfer.files)));
                    }}
                  >
                    <div className="upload-emblem">
                      <FileText size={32} />
                      <span>
                        <Upload size={17} />
                      </span>
                    </div>
                    <h2>
                      {dragging ? "松开，开始处理这份文档" : "把文件拖到这里"}
                    </h2>
                    <p>PDF、Word、TXT，或一张拍照图片</p>
                    <button
                      className="button primary"
                      disabled={busy}
                      onClick={() => input.current?.click()}
                    >
                      <Upload size={17} /> 选择本机文件
                    </button>
                    <div className="file-types">
                      <span>PDF</span>
                      <span>DOCX</span>
                      <span>TXT</span>
                      <span>JPG / PNG</span>
                      <small>单文件 ≤ 50 MB</small>
                    </div>
                  </section>
                  <section className="scan-options">
                    <div className="section-label">
                      <ScanLine size={18} />
                      <h2>这次怎么识别</h2>
                    </div>
                    <label className="option-row">
                      <span className="radio selected" />
                      <span>
                        <strong>自动提取文字</strong>
                        <small>文字文档在本机完成识别</small>
                      </span>
                      <Check size={17} />
                    </label>
                    <label
                      className={
                        "option-row ocr-option " + (useOCR ? "enabled" : "")
                      }
                    >
                      <span className="option-symbol">
                        <Image size={19} />
                      </span>
                      <span>
                        <strong>扫描件 / 拍照识别</strong>
                        <small>使用 MinerU 中文 OCR</small>
                      </span>
                      <input
                        type="checkbox"
                        checked={useOCR}
                        onChange={(e) => setUseOCR(e.target.checked)}
                      />
                    </label>
                    <p className="option-explanation">
                      {useOCR
                        ? "本批次图片和 PDF 将在你确认服务地址后发送识别。"
                        : "遇到纯图片或扫描页会提示使用 OCR，不会当作零风险。"}
                    </p>
                    <button
                      className="text-link"
                      onClick={() => setSettings(true)}
                    >
                      配置识别服务 <ArrowRight size={14} />
                    </button>
                    <div className="human-note">
                      <ShieldCheck size={16} />
                      <span>识别只是开始，处理由你确认。</span>
                    </div>
                  </section>
                </div>
                <div className="sample-line">
                  <Sparkles size={16} />
                  <span>想先体验一下？</span>
                  <button
                    disabled={busy}
                    onClick={() => run(() => sample("text"))}
                  >
                    试用一份示例文档 <ArrowRight size={14} />
                  </button>
                  <div className="sample-dropdown">
                    <button
                      className="muted-link"
                      onClick={() => setSampleMenu(!sampleMenu)}
                    >
                      更多测试样本 <ChevronDown size={14} />
                    </button>
                    {sampleMenu && (
                      <div className="sample-popover">
                        {boot.samples.map((item: any) => (
                          <div key={item.id}>
                            <button
                              disabled={!item.available || busy}
                              onClick={() => run(() => sample(item.id))}
                            >
                              <FileIcon
                                format={item.ocr ? "jpg" : "pdf"}
                                small
                              />
                              <span>
                                <strong>{item.name}</strong>
                                <small>{item.description}</small>
                              </span>
                            </button>
                            <a
                              href={`/api/samples/${item.id}/download`}
                              title="下载原稿以便打印拍照"
                              aria-label={"下载" + item.name}
                            >
                              <ArrowDownToLine size={17} />
                            </a>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </>
            ) : (
              <div className="page-heading">
                <div>
                  <h1>我的文档</h1>
                  <p>查看已保存的文档、识别结果和复核进度。</p>
                </div>
                <button
                  className="button primary"
                  disabled={busy}
                  onClick={() => input.current?.click()}
                >
                  <Upload size={17} />
                  添加文档
                </button>
              </div>
            )}
            <section className="recent-section">
              <div className="section-heading">
                <h2>
                  {page === "home" ? "最近的文档" : "全部文档"}{" "}
                  <span>{docs.length}</span>
                </h2>
                <div>
                  {page === "files" && (
                    <label className="search-input">
                      <Search size={16} />
                      <input
                        placeholder="查找文档名称"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                      />
                    </label>
                  )}
                  {page === "home" && (
                    <button
                      className="text-link muted-link"
                      onClick={() => run(() => go("files"))}
                    >
                      查看全部 <ArrowRight size={15} />
                    </button>
                  )}
                </div>
              </div>
              {documentsTable(page === "home" ? 5 : undefined)}
            </section>
            <footer className="home-footer">
              <span>
                <LockKeyhole size={13} /> 原始文件保持不变
              </span>
              <span>
                <BadgeCheck size={14} /> 副本导出前独立回查
              </span>
              <span>
                <BookOpen size={14} /> 结果需要人工复核
              </span>
            </footer>
          </main>
        )}
        {page === "review" && detail && (
          <main className="review-page">
            <div className="review-heading">
              <button
                className="icon-button"
                aria-label="返回文档列表"
                onClick={() => run(() => go("files"))}
              >
                <ArrowLeft size={19} />
              </button>
              <FileIcon format={detail.document.format} />
              <div className="review-name">
                <h1 title={detail.document.display_name}>
                  {detail.document.display_name}
                </h1>
                <p>
                  {detail.metadata.requires_ocr ||
                  !detail.blocks.some((b) => b.text.trim())
                    ? "等待 OCR 提取文字"
                    : detail.metadata.ocr
                      ? (detail.metadata.engine || "OCR") + " 文字"
                      : "本机解析文字"}
                  <span>·</span>原始文件保持不变
                </p>
              </div>
              <div className="review-actions">
                <button
                  className="button"
                  disabled={
                    busy ||
                    !detail.blocks.some((b) => b.text.trim()) ||
                    detail.document.review_status === "not_started"
                  }
                  onClick={() => {
                    setAIConsent(false);
                    setAIAction("review");
                  }}
                >
                  <Sparkles size={16} />
                  AI辅助复核
                </button>
                <span className="saved-state">
                  {saving ? (
                    <LoaderCircle size={14} className="spin" />
                  ) : (
                    <CircleCheck size={14} />
                  )}{" "}
                  {saving
                    ? "保存中"
                    : isDirty()
                      ? "有待保存的修改"
                      : "结果已保存"}
                </span>
                <button
                  className="button"
                  disabled={busy || !detail.blocks.length}
                  onClick={() =>
                    run(async () => {
                      await saveIfDirty();
                      setShowSource(false);
                      setPreview(
                        preview
                          ? null
                          : await api(`/documents/${selectedDoc}/preview`),
                      );
                    })
                  }
                >
                  <FileCheck2 size={16} />
                  {preview ? "返回原文" : "脱敏预览"}
                </button>
                <button
                  className="button primary"
                  disabled={
                    busy || detail.document.review_status !== "completed"
                  }
                  onClick={() =>
                    run(async () => {
                      const job = await post<Job>("/export", {
                        documents: [selectedDoc],
                      });
                      setJobs((old) => [job, ...old]);
                    })
                  }
                >
                  <ArrowDownToLine size={16} />
                  导出副本
                </button>
              </div>
            </div>
            {detail.document.error && (
              <div className="coverage-box danger">
                <CircleAlert size={21} />
                <div>
                  <strong>这份文档还没有处理完成</strong>
                  <p>{detail.document.error}</p>
                </div>
                {["pdf", "png", "jpg", "jpeg", "webp", "tiff", "tif"].includes(
                  detail.document.format,
                ) && (
                  <button
                    className="button amber"
                    disabled={busy}
                    onClick={() => {
                      setOCRAction({ id: selectedDoc! });
                      setConsent(false);
                    }}
                  >
                    重试 OCR
                  </button>
                )}
              </div>
            )}
            {detail.warnings.length > 0 && (
              <div className="coverage-box">
                <CircleAlert size={21} />
                <div>
                  <strong>
                    {detail.metadata.requires_ocr ||
                    !detail.blocks.some((b) => b.text.trim())
                      ? "图片里还有文字，使用 OCR 继续"
                      : detail.metadata.ocr
                        ? "OCR 文字需要对照原件核对"
                        : "请留意这份文档的覆盖范围"}
                  </strong>
                  <details>
                    <summary>{detail.warnings[0]}</summary>
                    {detail.warnings.slice(1).map((w, i) => (
                      <p key={i}>{w}</p>
                    ))}
                  </details>
                </div>
                {detail.metadata.requires_ocr ||
                !detail.blocks.some((b) => b.text.trim()) ? (
                  <button
                    className="button amber"
                    disabled={busy}
                    onClick={() => {
                      setOCRAction({ id: selectedDoc! });
                      setConsent(false);
                    }}
                  >
                    <ScanLine size={16} />
                    使用 OCR 识别
                  </button>
                ) : (
                  !detail.coverage_ack && (
                    <button
                      className="button amber"
                      disabled={busy}
                      onClick={() => run(acknowledge)}
                    >
                      已核对，继续识别
                      <ArrowRight size={15} />
                    </button>
                  )
                )}
                {detail.document.format === "pdf" &&
                  !detail.metadata.ocr &&
                  detail.blocks.some((b) => b.text.trim()) && (
                    <button
                      className="text-link"
                      disabled={busy}
                      onClick={() => {
                        setOCRAction({ id: selectedDoc! });
                        setConsent(false);
                      }}
                    >
                      整份文档使用 OCR
                    </button>
                  )}
              </div>
            )}
            {[
              "pdf",
              "docx",
              "png",
              "jpg",
              "jpeg",
              "webp",
              "tif",
              "tiff",
            ].includes(detail.document.format) && (
              <div className="ai-vision-entry">
                <Sparkles size={15} />
                <span>手写、拍照或扫描文字也可以使用视觉模型辅助读取。</span>
                <button
                  className="text-link"
                  disabled={busy}
                  onClick={() => {
                    setAIConsent(false);
                    setAIAction("vision");
                  }}
                >
                  AI视觉识别
                  <ArrowRight size={14} />
                </button>
              </div>
            )}
            <div className="review-grid">
              <aside className="document-rail">
                <h3>
                  工作区文档<span>{docs.length}</span>
                </h3>
                {docs.map((doc) => (
                  <button
                    key={doc.id}
                    className={
                      doc.id === selectedDoc
                        ? "mini-document active"
                        : "mini-document"
                    }
                    onClick={() => run(() => openDoc(doc.id))}
                  >
                    <FileIcon format={doc.format} small />
                    <span>
                      <strong>{doc.display_name}</strong>
                      <small>{docState(doc).name}</small>
                    </span>
                  </button>
                ))}
              </aside>
              <section className="reading-panel">
                <div className="reading-toolbar">
                  <span>
                    <FileText size={16} />
                    {preview ? "脱敏结果预览" : "解析原文"}
                  </span>
                  <span>{detail.blocks.length} 个文本块</span>
                  {[
                    "pdf",
                    "png",
                    "jpg",
                    "jpeg",
                    "webp",
                    "tif",
                    "tiff",
                  ].includes(detail.document.format) && (
                    <button
                      className="text-link source-toggle"
                      onClick={() => setShowSource(!showSource)}
                    >
                      {showSource ? "返回解析文字" : "查看原件"}
                    </button>
                  )}
                  <span className="legend">
                    <i />
                    隐私
                    <i className="blue" />
                    规范
                  </span>
                </div>
                {preview?.conflicts?.length > 0 && (
                  <div className="inline-alert">
                    存在 {preview.conflicts.length} 组重叠冲突，请返回原文复核。
                  </div>
                )}
                <div className="paper-scroll">
                  {showSource ? (
                    <div className="original-preview">
                      <img
                        src={`/api/documents/${selectedDoc}/source-preview?page=${sourcePage}`}
                        alt="当前文档原件，请对照识别文字核对"
                      />
                      <p>原件预览 · 不会改写源文件</p>
                      {detail.document.format === "pdf" &&
                        (detail.metadata.pages || 1) > 1 && (
                          <div className="source-pages">
                            <button
                              className="icon-button"
                              aria-label="上一页原件"
                              disabled={sourcePage <= 1}
                              onClick={() => setSourcePage(sourcePage - 1)}
                            >
                              <ChevronLeft size={16} />
                            </button>
                            <span>
                              第 {sourcePage} / {detail.metadata.pages} 页
                            </span>
                            <button
                              className="icon-button"
                              aria-label="下一页原件"
                              disabled={
                                sourcePage >= (detail.metadata.pages || 1)
                              }
                              onClick={() => setSourcePage(sourcePage + 1)}
                            >
                              <ChevronRight size={16} />
                            </button>
                          </div>
                        )}
                    </div>
                  ) : (
                    <article className="paper">
                      {detail.blocks.map((block, i) => (
                        <section className="text-block" key={block.id}>
                          <span className="block-location">
                            {block.locator.page
                              ? `第 ${block.locator.page} 页`
                              : `段落 ${i + 1}`}
                            {block.kind === "table"
                              ? " · 表格文字"
                              : block.kind === "header"
                                ? " · 页眉"
                                : block.kind === "footer"
                                  ? " · 页脚"
                                  : ""}
                          </span>
                          <p>
                            {documentText(block) || (
                              <span className="no-text">
                                此处尚无可提取文字
                              </span>
                            )}
                          </p>
                        </section>
                      ))}
                      {!detail.blocks.length && (
                        <div className="reading-empty">
                          <FileSearch size={36} />
                          <h3>文字还在路上</h3>
                          <p>处理完成后，解析文字会显示在这里。</p>
                        </div>
                      )}
                    </article>
                  )}
                </div>
              </section>
              <aside className="findings-panel">
                <div className="findings-heading">
                  <h2>
                    候选问题 <span>{detail.findings.length}</span>
                  </h2>
                  <span>{pending} 项待复核</span>
                </div>
                <div className="filter-tabs">
                  {[
                    ["all", "全部"],
                    ["pending", "待复核"],
                    ["confirmed", "已确认"],
                    ["ignored", "已忽略"],
                  ].map(([key, label]) => (
                    <button
                      key={key}
                      className={filter === key ? "active" : ""}
                      onClick={() => setFilter(key)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div className="findings-list">
                  {visibleFindings.map((f) => (
                    <button
                      className={
                        "finding-item " + (f.id === selected ? "selected" : "")
                      }
                      key={f.id}
                      onClick={() => run(() => chooseFinding(f.id))}
                    >
                      <span
                        className={
                          "risk-dot " +
                          (f.risk === "高"
                            ? "high"
                            : f.risk === "中"
                              ? "medium"
                              : "low")
                        }
                      />
                      <span>
                        <strong>{f.category}</strong>
                        <small>
                          {f.risk}风险 · {statusName[f.status]}
                        </small>
                      </span>
                      {f.status === "confirmed" ? (
                        <CircleCheck size={16} />
                      ) : f.status === "ignored" ? (
                        <Check size={16} />
                      ) : (
                        <ChevronRight size={16} />
                      )}
                    </button>
                  ))}
                  {!visibleFindings.length && (
                    <div className="no-findings">
                      <ShieldCheck size={30} />
                      <strong>
                        {detail.document.review_status === "not_started"
                          ? "等待开始识别"
                          : "当前没有候选问题"}
                      </strong>
                      <p>
                        {detail.warnings.length && !detail.coverage_ack
                          ? "先核对覆盖提示，再继续识别。"
                          : "零命中也需要你核对后明确完成复核。"}
                      </p>
                    </div>
                  )}
                </div>
                {current && (
                  <div className="finding-detail">
                    <div className="finding-detail-title">
                      <strong>{current.category}</strong>
                      <span
                        className={
                          "badge " +
                          (current.risk === "高" ? "error" : "warning")
                        }
                      >
                        {current.risk}风险
                      </span>
                    </div>
                    <p className="evidence">{current.evidence}</p>
                    <label className="field-label" htmlFor="review-note">
                      复核备注
                    </label>
                    <input
                      id="review-note"
                      value={note}
                      maxLength={500}
                      placeholder="可选，为这次判断留个说明"
                      disabled={busy || saving}
                      onChange={(e) => setNote(e.target.value)}
                    />
                    <details className="strategy-details">
                      <summary>
                        替换方式{" "}
                        <span>
                          {strategy === "full"
                            ? "完整替换"
                            : strategy === "partial"
                              ? "部分隐藏"
                              : "固定文本"}
                        </span>
                        <ChevronDown size={14} />
                      </summary>
                      <select
                        aria-label="替换策略"
                        disabled={busy || saving}
                        value={strategy}
                        onChange={(e) => setStrategy(e.target.value)}
                      >
                        <option value="full">完整替换为类型占位符</option>
                        <option value="partial">保留前后字符，隐藏中间</option>
                        <option value="fixed">替换为固定文本</option>
                      </select>
                      {strategy === "partial" && (
                        <div className="partial-fields">
                          <label>
                            保留前
                            <input
                              aria-label="保留前几个字符"
                              type="number"
                              min={0}
                              max={100}
                              value={prefix}
                              onChange={(e) =>
                                setPrefix(Number(e.target.value))
                              }
                            />
                          </label>
                          <label>
                            保留后
                            <input
                              aria-label="保留后几个字符"
                              type="number"
                              min={0}
                              max={100}
                              value={suffix}
                              onChange={(e) =>
                                setSuffix(Number(e.target.value))
                              }
                            />
                          </label>
                        </div>
                      )}
                      {strategy === "fixed" && (
                        <input
                          aria-label="固定替换文本"
                          placeholder="输入替换内容"
                          value={fixed}
                          maxLength={500}
                          onChange={(e) => setFixed(e.target.value)}
                        />
                      )}
                      <button
                        className="text-link"
                        disabled={busy || saving}
                        onClick={() => run(() => review(null))}
                      >
                        保存备注与策略
                      </button>
                    </details>
                    <div className="review-buttons">
                      <button
                        className="button"
                        disabled={busy || saving}
                        onClick={() => {
                          setReason("");
                          setIgnore(true);
                        }}
                      >
                        忽略
                      </button>
                      <button
                        className="button primary"
                        disabled={busy || saving}
                        onClick={() => run(() => review("confirmed"))}
                      >
                        <Check size={16} />
                        确认脱敏
                      </button>
                    </div>
                    <div className="finding-navigation">
                      <button
                        className="icon-button"
                        aria-label="上一个问题"
                        onClick={() => {
                          const i = detail.findings.findIndex(
                            (f) => f.id === selected,
                          );
                          run(() =>
                            chooseFinding(
                              detail.findings[
                                (i - 1 + detail.findings.length) %
                                  detail.findings.length
                              ].id,
                            ),
                          );
                        }}
                      >
                        <ChevronLeft size={16} />
                      </button>
                      <button
                        className="text-link muted-link"
                        disabled={busy || saving}
                        onClick={() =>
                          run(async () => {
                            await saveIfDirty();
                            setDetail(
                              await post(`/documents/${selectedDoc}/undo`),
                            );
                            await refresh();
                            setToast("已撤销上一步复核");
                          })
                        }
                      >
                        <Undo2 size={13} />
                        撤销
                      </button>
                      <button
                        className="icon-button"
                        aria-label="下一个问题"
                        onClick={() => {
                          const i = detail.findings.findIndex(
                            (f) => f.id === selected,
                          );
                          run(() =>
                            chooseFinding(
                              detail.findings[(i + 1) % detail.findings.length]
                                .id,
                            ),
                          );
                        }}
                      >
                        <ChevronRight size={16} />
                      </button>
                    </div>
                  </div>
                )}
                <div className="finish-review">
                  <button
                    className="button"
                    disabled={
                      busy ||
                      saving ||
                      pending > 0 ||
                      detail.document.review_status === "completed" ||
                      detail.document.review_status === "not_started" ||
                      !!detail.document.error
                    }
                    onClick={() =>
                      run(async () => {
                        await saveIfDirty();
                        setDetail(
                          await post(`/documents/${selectedDoc}/complete`),
                        );
                        await refresh();
                        setToast("当前文档已完成复核，可以导出副本");
                      })
                    }
                  >
                    <CheckCheck size={17} />
                    {detail.document.review_status === "completed"
                      ? "当前文档已完成复核"
                      : "完成当前文档复核"}
                  </button>
                  <small>全部候选处理后，仍需明确完成复核。</small>
                </div>
              </aside>
            </div>
          </main>
        )}
        {page === "review" && !detail && (
          <div className="startup">
            <LoaderCircle className="spin" />
            读取文档…
          </div>
        )}
        {page === "history" && (
          <main className="workspace-home">
            <div className="page-heading">
              <div>
                <h1>导出记录</h1>
                <p>通过回查的脱敏副本与报告，都会保留在这里。</p>
              </div>
            </div>
            <div className="export-list">
              {exports.map((item) => (
                <div className="export-row" key={item.id}>
                  <span className="soft-icon">
                    <FileCheck2 size={23} />
                  </span>
                  <div>
                    <strong>脱敏副本与审查报告</strong>
                    <small>
                      {date(item.created_at)} ·{" "}
                      {item.status === "success"
                        ? "回查通过"
                        : item.status === "failed"
                          ? "导出失败"
                          : item.status}
                    </small>
                    {item.error && <p>{item.error}</p>}
                  </div>
                  {item.status === "success" ? (
                    <a
                      className="button"
                      href={`/api/exports/${item.id}/download`}
                    >
                      <ArrowDownToLine size={16} />
                      下载文件包
                    </a>
                  ) : (
                    <span className="badge error">未发布成品</span>
                  )}
                </div>
              ))}
              {!exports.length && (
                <div className="empty-page">
                  <FileCheck2 size={40} />
                  <h2>还没有导出记录</h2>
                  <p>完成文档复核后，即可生成副本和报告。</p>
                  <button
                    className="button"
                    onClick={() => run(() => go("files"))}
                  >
                    查看我的文档
                  </button>
                </div>
              )}
            </div>
          </main>
        )}
        {page === "rules" && rules && (
          <main className="workspace-home">
            <div className="page-heading">
              <div>
                <h1>识别规则</h1>
                <p>为你的文档选择合适的检查范围。当前方案 v{rules.version}。</p>
              </div>
              <button
                className="button primary"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    setRules(await post("/rules", { config: rules.config }));
                    setToast("已发布新规则版本，已有结果保留原方案");
                  })
                }
              >
                <Check size={16} />
                发布新版本
              </button>
            </div>
            <div className="rules-layout">
              <section className="rules-card">
                <h2>内置检查</h2>
                <p>结构校验与精确匹配，不代表号码真实存在。</p>
                {Object.entries(rules.config.rules).map(
                  ([id, opts]: [string, any]) => (
                    <label className="rule-option" key={id}>
                      <span>
                        <strong>{ruleNames[id]}</strong>
                        <small>
                          {["phone", "email", "identity", "bank"].includes(id)
                            ? "隐私候选识别"
                            : "限定文本规范检查"}
                        </small>
                      </span>
                      <select
                        aria-label={ruleNames[id] + "风险等级"}
                        value={opts.risk}
                        onChange={(e) =>
                          setRules({
                            ...rules,
                            config: {
                              ...rules.config,
                              rules: {
                                ...rules.config.rules,
                                [id]: { ...opts, risk: e.target.value },
                              },
                            },
                          })
                        }
                      >
                        <option>高</option>
                        <option>中</option>
                        <option>低</option>
                      </select>
                      <input
                        type="checkbox"
                        checked={opts.enabled}
                        onChange={(e) =>
                          setRules({
                            ...rules,
                            config: {
                              ...rules.config,
                              rules: {
                                ...rules.config.rules,
                                [id]: { ...opts, enabled: e.target.checked },
                              },
                            },
                          })
                        }
                      />
                    </label>
                  ),
                )}
              </section>
              <section className="rules-card">
                <h2>自定义敏感词</h2>
                <p>按精确子串检查，添加后需发布新版本。</p>
                <div className="add-word">
                  <input
                    aria-label="新增敏感词"
                    value={word}
                    onChange={(e) => setWord(e.target.value)}
                    placeholder="例如：内部资料"
                    maxLength={500}
                  />
                  <select
                    aria-label="新词风险等级"
                    value={wordRisk}
                    onChange={(e) => setWordRisk(e.target.value)}
                  >
                    <option>高</option>
                    <option>中</option>
                    <option>低</option>
                  </select>
                  <button
                    className="button"
                    onClick={() => {
                      if (!word.trim()) return;
                      setRules({
                        ...rules,
                        config: {
                          ...rules.config,
                          words: [
                            ...rules.config.words,
                            {
                              word: word.trim(),
                              category: "自定义",
                              risk: wordRisk,
                              case_sensitive: true,
                              enabled: true,
                              suggestion: "复核后确认替换",
                            },
                          ],
                        },
                      });
                      setWord("");
                    }}
                  >
                    添加
                  </button>
                </div>
                <div className="word-list">
                  {rules.config.words.map((w: any, i: number) => (
                    <div key={w.id || i}>
                      <span>{w.word}</span>
                      <small>{w.risk}风险</small>
                      <button
                        className="icon-button"
                        aria-label={"删除词条" + w.word}
                        onClick={() =>
                          setRules({
                            ...rules,
                            config: {
                              ...rules.config,
                              words: rules.config.words.filter(
                                (_: any, j: number) => j !== i,
                              ),
                            },
                          })
                        }
                      >
                        <X size={15} />
                      </button>
                    </div>
                  ))}
                  {!rules.config.words.length && (
                    <p className="muted">尚未添加自定义敏感词。</p>
                  )}
                </div>
                <div className="quiet-note">
                  <CircleAlert size={16} />
                  <span>更新方案后，旧文档需要重新识别才能使用新规则。</span>
                </div>
              </section>
            </div>
          </main>
        )}
      </div>
      {toast && (
        <div className="toast" role="status">
          <CircleCheck size={18} />
          {toast}
          <button aria-label="关闭通知" onClick={() => setToast("")}>
            <X size={15} />
          </button>
        </div>
      )}
      {settings && (
        <div className="modal-backdrop" onClick={() => setSettings(false)}>
          <section
            ref={modalRef}
            className="modal settings-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="settings-title"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              className="modal-close icon-button"
              aria-label="关闭设置"
              onClick={() => setSettings(false)}
            >
              <X size={20} />
            </button>
            <span className="soft-icon">
              <Settings2 size={25} />
            </span>
            <h2 id="settings-title">识别设置</h2>
            <p>
              普通文档使用本地提取。只有确认后的图片和扫描件会发送到 OCR 服务。
            </p>
            <label className="field-label" htmlFor="ocr-url">
              MinerU 服务地址
            </label>
            <input
              id="ocr-url"
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
              placeholder="http://127.0.0.1:8003"
            />
            <div className="settings-health">
              <button
                className="button"
                disabled={checkingHealth || busy}
                onClick={() =>
                  run(async () => {
                    setCheckingHealth(true);
                    try {
                      await saveSettings();
                      const result = await post("/ocr/health");
                      setHealth(
                        result.available ? "服务可以连接" : result.message,
                      );
                    } finally {
                      setCheckingHealth(false);
                    }
                  })
                }
              >
                {checkingHealth ? (
                  <LoaderCircle size={15} className="spin" />
                ) : (
                  <ScanLine size={15} />
                )}
                检查连接
              </button>
              {health && <span>{health}</span>}
            </div>
            <div className="ai-extension">
              <div>
                <Sparkles size={19} />
                <strong>大模型与视觉识别</strong>
                <span
                  className={
                    "badge " + (boot.ai.configured ? "complete" : "neutral")
                  }
                >
                  {boot.ai.configured ? "本次已配置" : "需自行准备 API"}
                </span>
              </div>
              <p>
                使用兼容格式的模型服务。密钥只保留在本次运行内存，不写入项目、源码、报告或交付包。
              </p>
              <label className="field-label" htmlFor="ai-base">
                模型 Base URL
              </label>
              <input
                id="ai-base"
                value={aiBase}
                onChange={(e) => setAIBase(e.target.value)}
                placeholder="https://服务地址/api/v1"
              />
              <label className="field-label" htmlFor="ai-model">
                模型名称
              </label>
              <input
                id="ai-model"
                value={aiModel}
                onChange={(e) => setAIModel(e.target.value)}
                placeholder="ecnu-max"
              />
              <label className="field-label" htmlFor="ai-key">
                API 密钥（不保存到磁盘）
              </label>
              <input
                id="ai-key"
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={aiKey}
                onChange={(e) => setAIKey(e.target.value)}
                placeholder={
                  boot.ai.configured
                    ? "已配置；留空保持当前内存密钥"
                    : "请自行准备并填写大模型 API 密钥"
                }
              />
              <div className="settings-health">
                <button
                  className="button"
                  disabled={busy || aiProbing}
                  onClick={() => run(saveAIConfig)}
                >
                  保存模型配置
                </button>
                <button
                  className="button"
                  disabled={busy || aiProbing}
                  onClick={() =>
                    run(async () => {
                      setAIProbing(true);
                      try {
                        await saveAIConfig();
                        const data = await post("/ai/probe");
                        setToast(
                          data.available
                            ? "模型连接与JSON响应验证通过"
                            : "模型返回不符合预期",
                        );
                      } finally {
                        setAIProbing(false);
                      }
                    })
                  }
                >
                  {aiProbing ? (
                    <LoaderCircle size={14} className="spin" />
                  ) : (
                    <Sparkles size={14} />
                  )}
                  验证 API
                </button>
                <button
                  className="text-link"
                  disabled={busy || !boot.ai.configured}
                  onClick={() =>
                    run(async () => {
                      const data = await post("/ai/config", {
                        clear_key: true,
                      });
                      setBoot({ ...boot, ai: data });
                      setAIKey("");
                      setToast("当前内存密钥已清除");
                    })
                  }
                >
                  清除本次密钥
                </button>
              </div>
              <p>
                点击“验证
                API”会发送一条简短测试请求，可能使用服务配额。修改服务地址后，需要重新填写密钥。普通文档不会自动发送到模型。
              </p>
            </div>
            <button
              className="text-link stop-service"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await post("/shutdown");
                  setSettings(false);
                  setBoot(null);
                  setFailure("本机服务已停止，双击启动器即可重新打开");
                })
              }
            >
              停止本机服务
            </button>
            <div className="modal-actions">
              <button className="button" onClick={() => setSettings(false)}>
                取消
              </button>
              <button
                className="button primary"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    await saveSettings();
                    setSettings(false);
                  })
                }
              >
                保存设置
              </button>
            </div>
          </section>
        </div>
      )}
      {ocrAction && (
        <div className="modal-backdrop">
          <section
            ref={modalRef}
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="ocr-consent-title"
          >
            <button
              className="modal-close icon-button"
              aria-label="取消 OCR"
              onClick={() => setOCRAction(null)}
            >
              <X size={20} />
            </button>
            <span className="soft-icon amber">
              <ScanLine size={27} />
            </span>
            <h2 id="ocr-consent-title">使用 OCR 读取图片文字</h2>
            <p>
              这会把
              {ocrAction.files
                ? `本批次 ${ocrAction.files.filter((f) => /\.(pdf|png|jpe?g|webp|tiff?)$/i.test(f.name)).length} 个 PDF / 图片文件`
                : "当前文档的完整文件"}
              发送到下面的 MinerU 服务。普通 Word / TXT 继续在本机处理。
            </p>
            <div className="endpoint-box">
              <span>文件发送到</span>
              <strong>{endpoint}</strong>
              {endpoint.startsWith("http:") && (
                <small>当前地址使用 HTTP，传输未经 HTTPS 加密。</small>
              )}
            </div>
            <label className="consent-check">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
              />
              <span>我了解发送范围与地址，同意本次上传识别。</span>
            </label>
            <p className="fine-print">
              OCR
              可能产生错字、漏字或数字混淆。结果需与原件核对。取消等待不能撤回已经发送的文件。
            </p>
            <div className="modal-actions">
              <button className="button" onClick={() => setOCRAction(null)}>
                暂不发送
              </button>
              <button
                className="button primary"
                disabled={!consent}
                onClick={() => run(authorizeOCR)}
              >
                同意并开始 OCR
                <ArrowRight size={16} />
              </button>
            </div>
          </section>
        </div>
      )}
      {aiAction && (
        <div className="modal-backdrop">
          <section
            ref={modalRef}
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="ai-action-title"
          >
            <button
              className="modal-close icon-button"
              aria-label="取消AI请求"
              onClick={() => setAIAction(null)}
            >
              <X size={20} />
            </button>
            <span className="soft-icon">
              <Sparkles size={27} />
            </span>
            <h2 id="ai-action-title">
              {aiAction === "vision" ? "AI视觉文字识别" : "AI语义辅助复核"}
            </h2>
            <p>
              {aiAction === "vision"
                ? "将当前图片、PDF的页面图像或DOCX中的嵌入图片发送到模型转写。模型最多处理8页/张，不能辨认的位置会保留提示。"
                : "将当前文档的解析文字发送到模型，补充姓名、地址等语义候选。本地规则结果保留，新增建议仍需人工确认。"}
            </p>
            <div className="endpoint-box">
              <span>本次接收服务与模型</span>
              <strong>{boot.ai.base_url}</strong>
              <small>{boot.ai.model}</small>
            </div>
            {!boot.ai.configured ? (
              <div className="inline-alert">
                当前未配置密钥。本软件不附带可用 API，请在设置中自行填写。
                <button
                  className="text-link"
                  onClick={() => {
                    setAIAction(null);
                    setSettings(true);
                  }}
                >
                  前往配置
                </button>
              </div>
            ) : (
              <label className="consent-check">
                <input
                  type="checkbox"
                  checked={aiConsent}
                  onChange={(e) => setAIConsent(e.target.checked)}
                />
                <span>
                  我确认上述发送范围、服务地址和配额使用，同意本次请求。
                </span>
              </label>
            )}
            <p className="fine-print">
              模型可能误读或遗漏，也可能给出错误建议。文档中的指令不作为操作授权；所有候选都需要人工复核。
            </p>
            <div className="modal-actions">
              <button className="button" onClick={() => setAIAction(null)}>
                取消
              </button>
              <button
                className="button primary"
                disabled={!aiConsent || !boot.ai.configured || busy}
                onClick={() => run(runAIAction)}
              >
                同意并开始
                <ArrowRight size={16} />
              </button>
            </div>
          </section>
        </div>
      )}
      {ignore && current && (
        <div className="modal-backdrop">
          <section
            ref={modalRef}
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="ignore-title"
          >
            <h2 id="ignore-title">为什么保留这一项？</h2>
            <p>忽略后会保留原文。原因将随审查记录保存。</p>
            <textarea
              autoFocus
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
              placeholder="填写 1—500 字的忽略原因"
            />
            <div className="modal-actions">
              <button className="button" onClick={() => setIgnore(false)}>
                返回
              </button>
              <button
                className="button primary"
                disabled={!reason.trim() || saving}
                onClick={() => run(() => review("ignored", reason))}
              >
                保存忽略原因
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
