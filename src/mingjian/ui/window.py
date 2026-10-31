from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QKeySequence, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter, QStackedWidget,
    QTextEdit, QVBoxLayout, QWidget)

from mingjian.domain.models import APP_NAME, MingjianError, VERSION, dumps
from mingjian.infrastructure.maintenance import backup_project, clear_cache, restore_project
from mingjian.infrastructure.tasks import Worker, run_documents
from mingjian.parsers.mapping import utf16_offset
from mingjian.services.export import export_project
from mingjian.services.project import Project
from mingjian.ui.dialogs import EncodingDialog, ImportDialog, PreviewDialog, RulesDialog, SettingsDialog, error, table

PARSE = {"pending": "待解析", "running": "解析中", "success": "解析成功", "partial": "部分覆盖", "failed": "解析失败", "cancelled": "已取消"}
REVIEW = {"not_started": "未开始", "in_review": "复核中", "completed": "已完成", "needs_review": "需重新复核"}
STATUS = {"pending": "待复核", "confirmed": "已确认", "ignored": "已忽略"}
TASK_STATUS = {"queued": "排队", "running": "运行中", "success": "成功", "partial_failure": "部分失败",
               "failed": "失败", "cancelled": "已取消", "interrupted": "上次运行中断"}


def local_time(value):
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return value or "—"

STYLE = """
QMainWindow,QDialog { background:#f5f7f5; color:#213630; }
QWidget { font-size:13px; }
QLabel { color:#213630; }
QLabel#heading { font-size:23px; font-weight:600; }
QLabel#muted { color:#52665f; }
QLabel#warning { background:#fff2d7; color:#684800; padding:10px; border-radius:5px; }
QLabel#save { color:#246b4b; }
QPushButton { padding:7px 12px; border:1px solid #bdcac4; border-radius:5px; background:#fff; color:#213630; }
QPushButton:hover { background:#eaf1ed; border-color:#638a7e; }
QPushButton:focus { border:2px solid #236d63; }
QPushButton:disabled { color:#89948f; background:#edf0ed; }
QPushButton#primary { background:#236d63; border-color:#236d63; color:#fff; font-weight:600; }
QPushButton#primary:hover { background:#18554e; }
QPushButton#primary:disabled { background:#779a90; border-color:#779a90; }
QLineEdit,QComboBox,QSpinBox { background:#fff; border:1px solid #bdcac4; border-radius:4px; padding:5px; min-height:20px; }
QLineEdit:focus,QComboBox:focus,QPlainTextEdit:focus,QListWidget:focus { border:1px solid #236d63; }
QTextEdit,QPlainTextEdit { background:#fff; color:#213630; border:1px solid #ced7d1; padding:12px; selection-background-color:#c5e5db; selection-color:#132a21; }
QListWidget,QTableWidget { background:#fff; color:#213630; border:1px solid #ced7d1; border-radius:4px; outline:0; }
QListWidget::item { padding:10px 9px; border-bottom:1px solid #edf0ec; }
QListWidget::item:selected { background:#dfeee7; color:#133f34; }
QListWidget::item:hover { background:#edf4ef; }
QHeaderView::section { background:#e9efea; color:#314c40; border:0; padding:8px; }
QProgressBar { border:0; background:#dfe7df; height:8px; }
QProgressBar::chunk { background:#236d63; }
QSplitter::handle { background:#e5eae5; width:6px; }
QStatusBar { background:#eaf0eb; color:#314c40; }
QTabBar::tab { padding:9px 13px; }
"""


class MainWindow(QMainWindow):
    def __init__(self, config_root):
        super().__init__()
        self.config_root = Path(config_root)
        self.config_root.mkdir(parents=True, exist_ok=True)
        self.project = None
        self.current_id = None
        self.worker = None
        self.blocks, self.all_findings, self.visible_findings = [], [], []
        self.excluded_count = 0
        self.detail_dirty = False
        self.loading_detail = False
        self.loaded_finding_id = None
        self.block_positions = {}
        self.recent = []
        try:
            self.recent = json.loads((self.config_root / "recent.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        self.setWindowTitle(f"明鉴 V{VERSION} · 文档敏感信息识别与脱敏")
        self.setMinimumSize(1120, 720)
        self.resize(1480, 920)
        self.setStyleSheet(STYLE)
        self.setAcceptDrops(True)
        self.build_menu()
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.build_home()
        self.build_workbench()
        status = QWidget()
        row = QHBoxLayout(status)
        row.setContentsMargins(8, 3, 8, 3)
        self.task_label = QLabel("本地离线运行")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(240)
        self.progress.setVisible(False)
        self.cancel_button = QPushButton("请求取消")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_task)
        self.save_label = QLabel("已保存")
        self.save_label.setObjectName("save")
        row.addWidget(self.task_label, 1)
        row.addWidget(self.progress)
        row.addWidget(self.cancel_button)
        row.addWidget(self.save_label)
        self.statusBar().addPermanentWidget(status, 1)
        self.refresh_recent()

    def action(self, menu, label, callback, shortcut=None):
        action = QAction(label, self)
        def trigger(checked=False):
            if shortcut in ("Ctrl+Return", "Ctrl+I", "Alt+Up", "Alt+Down") and isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit, QSpinBox)):
                return
            self.safe(callback)
        action.triggered.connect(trigger)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        menu.addAction(action)
        return action

    def build_menu(self):
        project_menu = self.menuBar().addMenu("项目")
        self.action(project_menu, "新建项目…", self.new_project)
        self.action(project_menu, "打开项目…", self.open_project)
        self.action(project_menu, "返回项目首页", self.go_home)
        self.action(project_menu, "重命名当前项目…", self.rename_project)
        self.action(project_menu, "归档当前项目", self.archive_project)
        project_menu.addSeparator()
        self.action(project_menu, "备份项目…", self.backup)
        self.action(project_menu, "恢复备份到新目录…", self.restore)
        self.action(project_menu, "回收区与恢复…", self.trash)
        edit = self.menuBar().addMenu("复核")
        self.action(edit, "撤销", lambda: self.undo(False), "Ctrl+Z")
        self.action(edit, "重做", lambda: self.undo(True), "Ctrl+Shift+Z")
        self.action(edit, "保存", self.saved, "Ctrl+S")
        self.action(edit, "确认选中问题", lambda: self.review("confirmed"), "Ctrl+Return")
        self.action(edit, "忽略选中问题…", lambda: self.review("ignored"), "Ctrl+I")
        self.action(edit, "上一个问题", lambda: self.navigate(-1), "Alt+Up")
        self.action(edit, "下一个问题", lambda: self.navigate(1), "Alt+Down")
        self.action(edit, "查找文本…", self.find_text, "Ctrl+F")
        tools = self.menuBar().addMenu("工具")
        self.action(tools, "规则中心…", self.rules)
        self.action(tools, "设置…", self.settings)
        self.action(tools, "任务记录…", self.tasks)
        self.action(tools, "导出历史…", self.history)
        self.action(tools, "清理可重建缓存", self.clean_cache)
        help_menu = self.menuBar().addMenu("帮助")
        self.action(help_menu, "操作说明与快捷键", self.help)
        self.action(help_menu, "关于明鉴", lambda: QMessageBox.about(self, "关于明鉴", f"{APP_NAME}\nV{VERSION}\n单机离线 · DOCX / 文本型 PDF / TXT\n不支持 OCR 与原版式无损编辑。\n识别和复核不代表不存在其他敏感信息。\nQt for Python（LGPLv3）、Noto Sans SC（OFL）等组件许可见交付目录 licenses。"))

    def build_home(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(72, 60, 72, 40)
        heading = QLabel("明鉴")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        layout.addWidget(QLabel("文档敏感信息识别与脱敏系统  V1.0"))
        description = QLabel("在本机查看、复核与脱敏办公文档。先确认解析覆盖，再逐项决定处理方式。")
        description.setObjectName("muted")
        layout.addWidget(description)
        row = QHBoxLayout()
        for title, callback, primary in (("新建项目", self.new_project, True), ("打开项目", self.open_project, False), ("恢复备份", self.restore, False)):
            button = QPushButton(title)
            if primary:
                button.setObjectName("primary")
            button.clicked.connect(lambda checked=False, cb=callback: self.safe(cb))
            row.addWidget(button)
        row.addStretch()
        layout.addSpacing(20)
        layout.addLayout(row)
        layout.addSpacing(32)
        layout.addWidget(QLabel("最近项目 · 双击打开"))
        self.recent_list = QListWidget()
        self.recent_list.itemDoubleClicked.connect(lambda item: self.safe(lambda: self.load_project(item.data(Qt.ItemDataRole.UserRole))))
        layout.addWidget(self.recent_list, 1)
        note = QLabel("支持 DOCX、文本型 PDF、TXT。扫描页、图像和复杂对象保留覆盖限制说明；软件不会将未覆盖内容计为安全。")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        self.stack.addWidget(page)

    def build_workbench(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(18, 16, 18, 12)
        toolbar = QHBoxLayout()
        self.project_label = QLabel("审查工作台")
        self.project_label.setObjectName("heading")
        self.project_label.setMaximumWidth(400)
        toolbar.addWidget(self.project_label, 1)
        self.project_buttons = []
        for title, callback, primary in (("导入文档", self.choose_import, True), ("规则中心", self.rules, False), ("脱敏预览", self.preview, False), ("导出已完成文档", self.export, False), ("历史", self.history, False)):
            button = QPushButton(title)
            if primary:
                button.setObjectName("primary")
            button.clicked.connect(lambda checked=False, cb=callback: self.safe(cb))
            toolbar.addWidget(button)
            self.project_buttons.append(button)
        outer.addLayout(toolbar)
        self.summary_label = QLabel("尚未导入文档")
        self.summary_label.setObjectName("muted")
        outer.addWidget(self.summary_label)
        splitter = QSplitter()
        outer.addWidget(splitter, 1)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 10, 7, 0)
        self.file_search = QLineEdit()
        self.file_search.setPlaceholderText("按文件名查找")
        self.file_search.textChanged.connect(self.refresh_documents)
        ll.addWidget(self.file_search)
        self.doc_filter = QComboBox()
        self.doc_filter.addItems(["全部文档", "DOCX", "PDF", "TXT", "部分覆盖", "解析失败", "待完成审查", "已完成审查"])
        self.doc_filter.currentIndexChanged.connect(self.refresh_documents)
        ll.addWidget(self.doc_filter)
        self.document_list = QListWidget()
        self.document_list.currentItemChanged.connect(self.select_document)
        ll.addWidget(self.document_list, 1)
        for title, callback in (("解析 / 重新解析", self.reparse), ("识别当前文档", self.scan_current), ("批量识别已解析文档", self.scan_batch), ("移入项目回收区", self.remove_current)):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, cb=callback: self.safe(cb))
            ll.addWidget(button)
            self.project_buttons.append(button)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(10, 10, 10, 0)
        self.document_title = QLabel("选择文档开始审查")
        self.document_title.setWordWrap(True)
        cl.addWidget(self.document_title)
        self.coverage_label = QLabel("导入后显示解析覆盖说明")
        self.coverage_label.setObjectName("warning")
        self.coverage_label.setWordWrap(True)
        cl.addWidget(self.coverage_label)
        self.coverage_button = QPushButton("查看并确认当前文档覆盖限制")
        self.coverage_button.clicked.connect(lambda: self.safe(self.acknowledge))
        cl.addWidget(self.coverage_button)
        self.reader = QTextEdit()
        self.reader.setReadOnly(True)
        self.reader.setPlaceholderText("解析原文将在这里显示。点击右侧问题，定位原文区间。")
        cl.addWidget(self.reader, 1)
        bottom = QHBoxLayout()
        self.position_label = QLabel("原始解析文本 · Python 代码点位置")
        bottom.addWidget(self.position_label, 1)
        self.complete_button = QPushButton("完成当前文档审查")
        self.complete_button.setObjectName("primary")
        self.complete_button.clicked.connect(lambda: self.safe(self.complete))
        self.project_buttons.append(self.complete_button)
        bottom.addWidget(self.complete_button)
        cl.addLayout(bottom)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 10, 0, 0)
        self.finding_summary = QLabel("问题列表 · 支持多选后批量处理")
        self.finding_summary.setWordWrap(True)
        rl.addWidget(self.finding_summary)
        filter_row = QHBoxLayout()
        self.risk_filter = QComboBox()
        self.risk_filter.addItems(["全部风险", "高", "中", "低"])
        self.type_filter = QComboBox()
        self.type_filter.addItems(["全部类型", "手机号", "身份证号", "邮箱", "疑似银行卡号", "敏感词", "文本规范"])
        self.status_filter = QComboBox()
        self.status_filter.addItems(["全部状态", "待复核", "已确认", "已忽略"])
        for combo in (self.risk_filter, self.type_filter, self.status_filter):
            combo.currentIndexChanged.connect(self.filter_findings)
            filter_row.addWidget(combo)
        rl.addLayout(filter_row)
        self.finding_list = QListWidget()
        self.finding_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.finding_list.currentItemChanged.connect(self.select_finding)
        rl.addWidget(self.finding_list, 1)
        self.detail = QLabel("选中问题查看依据；确认仅影响脱敏副本。")
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rl.addWidget(self.detail)
        self.strategy = QComboBox()
        self.strategy.addItems(["完整替换", "保留前后字符", "固定文本"])
        rl.addWidget(self.strategy)
        strategy_row = QHBoxLayout()
        self.prefix, self.suffix = QSpinBox(), QSpinBox()
        for widget in (self.prefix, self.suffix):
            widget.setRange(0, 100)
        self.prefix.setValue(3)
        self.suffix.setValue(4)
        strategy_row.addWidget(QLabel("保留前"))
        strategy_row.addWidget(self.prefix)
        strategy_row.addWidget(QLabel("后"))
        strategy_row.addWidget(self.suffix)
        rl.addLayout(strategy_row)
        self.fixed_text = QLineEdit()
        self.fixed_text.setMaxLength(500)
        self.fixed_text.setPlaceholderText("固定替换文本")
        rl.addWidget(self.fixed_text)
        self.note = QLineEdit()
        self.note.setMaxLength(500)
        self.note.setPlaceholderText("备注（保存在项目中，不复制到报告）")
        rl.addWidget(self.note)
        def mark_dirty(*_):
            if not self.loading_detail and self.loaded_finding_id:
                self.detail_dirty = True
                self.save_label.setText("有待保存的备注或策略")
        self.note.textEdited.connect(mark_dirty)
        self.fixed_text.textEdited.connect(mark_dirty)
        self.strategy.activated.connect(mark_dirty)
        self.prefix.valueChanged.connect(mark_dirty)
        self.suffix.valueChanged.connect(mark_dirty)
        row = QHBoxLayout()
        for title, callback in (("确认选中", lambda: self.review("confirmed")), ("忽略选中", lambda: self.review("ignored")), ("重新打开", lambda: self.review("pending"))):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, cb=callback: self.safe(cb))
            row.addWidget(button)
            self.project_buttons.append(button)
        rl.addLayout(row)
        save = QPushButton("保存选中项备注与策略")
        save.clicked.connect(lambda: self.safe(lambda: self.review(None)))
        rl.addWidget(save)
        self.project_buttons.append(save)
        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right)
        splitter.setSizes([240, 710, 390])
        splitter.setStretchFactor(1, 1)
        left.setMinimumWidth(190)
        right.setMinimumWidth(330)
        self.stack.addWidget(page)

    def safe(self, callback):
        try:
            callback()
        except Exception as exc:
            self.save_label.setText("保存失败 / 操作未完成")
            error(self, exc)

    def require_project(self):
        if self.worker:
            raise MingjianError("后台任务执行中，请等待或请求取消后继续")
        if not self.project:
            raise MingjianError("请先新建或打开项目")

    def current_strategy(self):
        strategy = {"mode": ["full", "partial", "fixed"][self.strategy.currentIndex()]}
        if strategy["mode"] == "partial":
            strategy.update(prefix=self.prefix.value(), suffix=self.suffix.value())
        elif strategy["mode"] == "fixed":
            strategy["text"] = self.fixed_text.text()
        return strategy

    def flush_detail(self):
        if not self.detail_dirty or not self.loaded_finding_id or not self.current_id:
            return
        if self.worker:
            raise MingjianError("后台任务中不能保存编辑，请等待任务结束")
        finding = next((f for f in self.all_findings if f["id"] == self.loaded_finding_id), None)
        if not finding:
            raise MingjianError("编辑的位置已失效，请刷新")
        self.save_label.setText("保存中")
        self.project.review_many(self.current_id, [{"id": finding["id"], "revision": finding["revision"],
            "note": self.note.text(), "strategy": self.current_strategy()}])
        self.all_findings = self.project.findings(self.current_id)
        self.detail_dirty = False
        self.save_label.setText("已保存")

    def require_document(self):
        self.require_project()
        if not self.current_id:
            raise MingjianError("请先选择文档")

    def refresh_recent(self):
        self.recent_list.clear()
        for item in self.recent:
            if Path(item["path"]).is_dir():
                row = QListWidgetItem(f"{item['name']}\n{item['path']}")
                row.setData(Qt.ItemDataRole.UserRole, item["path"])
                row.setToolTip(item["path"])
                self.recent_list.addItem(row)

    def remember(self):
        self.recent = [{"path": str(self.project.root), "name": self.project.info["name"]}] + [r for r in self.recent if r["path"] != str(self.project.root)]
        self.recent = self.recent[:30]
        temporary = self.config_root / "recent.pending"
        temporary.write_text(dumps(self.recent), encoding="utf-8")
        temporary.replace(self.config_root / "recent.json")
        self.refresh_recent()

    def new_project(self):
        if self.worker:
            raise MingjianError("请等待后台任务结束")
        self.flush_detail()
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称（1—80 字）")
        if not ok:
            return
        Project.validate_name(name)
        parent = QFileDialog.getExistingDirectory(self, "选择项目存放位置", str(Path.home() / "Documents"))
        if parent:
            new = Project.create(parent, name)
            self.set_project(new)

    def open_project(self):
        if self.worker:
            raise MingjianError("请等待后台任务结束")
        folder = QFileDialog.getExistingDirectory(self, "选择含 project.sqlite3 的明鉴项目目录")
        if folder:
            self.load_project(folder)

    def load_project(self, folder):
        if self.worker:
            raise MingjianError("请等待后台任务结束")
        self.flush_detail()
        if self.project and self.project.root == Path(folder).resolve():
            self.stack.setCurrentIndex(1)
            return
        self.set_project(Project(folder))

    def set_project(self, project):
        if self.project:
            self.project.close()
        self.project = project
        self.current_id = None
        self.stack.setCurrentIndex(1)
        self.project_label.setText(project.info["name"])
        self.project_label.setToolTip(project.info["name"])
        self.remember()
        self.refresh_documents()
        self.show_document()

    def go_home(self):
        if self.worker:
            raise MingjianError("请等待后台任务结束")
        self.flush_detail()
        self.stack.setCurrentIndex(0)
        self.refresh_recent()

    def rename_project(self):
        self.require_project()
        name, ok = QInputDialog.getText(self, "重命名项目", "项目名称", text=self.project.info["name"])
        if ok:
            self.project.rename(name)
            self.project_label.setText(name)
            self.project_label.setToolTip(name)
            self.remember()

    def archive_project(self):
        self.require_project()
        if QMessageBox.question(self, "归档项目", "归档保留所有快照与复核结果，仍可通过打开项目继续使用。确认归档？") == QMessageBox.StandardButton.Yes:
            self.project.archive()
            self.go_home()

    def start_task(self, title, operation, callback=None, tracked=True):
        if self.worker:
            raise MingjianError("已有任务执行中")
        self.flush_detail()
        worker = Worker(title, operation, self.project if tracked else None, self)
        self.worker = worker
        self.task_label.setText(title)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        for widget in self.project_buttons + [self.document_list, self.coverage_button, self.note, self.strategy, self.fixed_text, self.prefix, self.suffix]:
            widget.setEnabled(False)
        worker.progress.connect(self.task_progress)
        outcome = {}
        worker.succeeded.connect(lambda value: outcome.update(result=value))
        worker.failed.connect(lambda message: outcome.update(error=message))
        def finished():
            self.worker = None
            self.progress.hide()
            self.cancel_button.hide()
            for widget in self.project_buttons + [self.document_list, self.coverage_button, self.note, self.strategy, self.fixed_text, self.prefix, self.suffix]:
                widget.setEnabled(True)
            self.task_label.setText(title + " · 已结束")
            self.refresh_documents()
            self.show_document()
            if "error" in outcome:
                QMessageBox.warning(self, "任务未完成", outcome["error"])
            elif callback:
                self.safe(lambda: callback(outcome.get("result")))
            worker.deleteLater()
        worker.finished.connect(finished)
        worker.start()

    def task_progress(self, done, total, stage):
        self.task_label.setText(f"{stage} · {done}/{total}")
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)

    def cancel_task(self):
        if self.worker:
            self.worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self.task_label.setText("已请求取消，将在下一个安全检查点停止")

    def choose_import(self):
        self.require_project()
        paths, _ = QFileDialog.getOpenFileNames(self, "选择需要审查的文件", "", "支持的文档 (*.docx *.pdf *.txt);;所有文件 (*)")
        if paths:
            self.import_paths(paths)

    def import_paths(self, paths):
        self.require_project()
        project = self.project
        def ready(preview):
            if ImportDialog(preview, self).exec():
                def work(cancel, progress):
                    result = project.import_files(preview, cancel, progress)
                    result["failures"].extend(run_documents(result["imported"], lambda document_id: project.parse(document_id, cancel=cancel),
                        project.limits.parse_workers, cancel, progress, "解析文档"))
                    return result
                self.start_task("导入与解析", work, lambda result: QMessageBox.information(self, "导入结果", f"保存快照 {len(result['imported'])} 个；失败 {len(result['failures'])} 个；跳过 {len(result['skipped'])} 个。\n解析失败请选中文档查看原因并重新解析。"))
        self.start_task("检查文件与内容重复", lambda cancel, progress: project.import_preview(paths), ready)

    def refresh_documents(self, *_):
        if not self.project:
            return
        documents = self.project.documents()
        total, success, partial, failed, completed = len(documents), 0, 0, 0, 0
        self.document_list.blockSignals(True)
        self.document_list.clear()
        selected = None
        for doc in documents:
            success += doc["parse_status"] == "success"
            partial += doc["parse_status"] == "partial"
            failed += doc["parse_status"] == "failed"
            completed += doc["review_status"] == "completed"
            if self.file_search.text().lower() not in doc["display_name"].lower():
                continue
            index = self.doc_filter.currentIndex()
            if index in (1, 2, 3) and doc["format"] != {1: "docx", 2: "pdf", 3: "txt"}[index]:
                continue
            if index == 4 and doc["parse_status"] != "partial":
                continue
            if index == 5 and doc["parse_status"] != "failed":
                continue
            if index == 6 and doc["review_status"] == "completed":
                continue
            if index == 7 and doc["review_status"] != "completed":
                continue
            item = QListWidgetItem(f"{doc['display_name']}\n{PARSE.get(doc['parse_status'],doc['parse_status'])} · {REVIEW[doc['review_status']]}")
            item.setToolTip(doc["display_name"] + "\n" + doc["original_path"])
            item.setData(Qt.ItemDataRole.UserRole, doc["id"])
            self.document_list.addItem(item)
            if doc["id"] == self.current_id:
                selected = item
        if selected:
            self.document_list.setCurrentItem(selected)
        elif not self.current_id and self.document_list.count():
            self.document_list.setCurrentRow(0)
            self.current_id = self.document_list.currentItem().data(Qt.ItemDataRole.UserRole)
        self.document_list.blockSignals(False)
        self.summary_label.setText(f"文件 {total}  /  解析成功 {success}  /  部分覆盖 {partial}  /  失败 {failed}    ·    待完成审查 {total-completed}  /  已完成 {completed}")

    def select_document(self, item, previous=None):
        if item:
            try:
                self.flush_detail()
            except Exception as exc:
                self.document_list.blockSignals(True)
                self.document_list.setCurrentItem(previous)
                self.document_list.blockSignals(False)
                self.save_label.setText("保存失败，未切换文档")
                error(self, exc)
                return
            self.current_id = item.data(Qt.ItemDataRole.UserRole)
            self.safe(self.show_document)

    def show_document(self):
        self.detail_dirty = False
        self.loaded_finding_id = None
        self.reader.clear()
        self.blocks, self.all_findings = [], []
        self.excluded_count = 0
        self.block_positions = {}
        self.coverage_button.hide()
        self.coverage_label.setText("选择或导入文档后查看覆盖范围")
        self.document_title.setText("选择文档开始审查")
        if self.project and self.current_id:
            try:
                doc = self.project.document(self.current_id)
                self.document_title.setText(doc["display_name"] + " · " + REVIEW[doc["review_status"]])
                version, self.blocks = self.project.parsed(self.current_id)
                warnings = json.loads(version["warnings_json"])
                chars = sum(len(b.text) for b in self.blocks)
                if warnings:
                    self.coverage_label.setText(f"部分覆盖 · {len(self.blocks)} 块 / {chars:,} 字符 · 限制 {len(warnings)} 项\n" + "；".join(warnings[:3]) + ("…" if len(warnings)>3 else "") + ("\n已确认限制，输出仍将标明部分覆盖。" if version["coverage_ack"] else "\n请先查看并确认覆盖限制。"))
                    self.coverage_button.show()
                else:
                    self.coverage_label.setText(f"受支持文本解析完成 · {len(self.blocks)} 块 / {chars:,} 字符。请执行识别并人工复核。")
                parts, position = [], 0
                for block in self.blocks:
                    locator = block.locator
                    location = f"第 {locator['page']} 页" if "page" in locator else locator.get("story", "文本")
                    label = f"【{location} · 块 {len(parts)//2+1}】\n"
                    parts.append(label)
                    position += len(label)
                    self.block_positions[block.id] = position
                    # Qt normalizes CRLF. Keep display mapping separate from immutable original.
                    text = block.text.replace("\r\n", "\n").replace("\r", "\n")
                    parts.append(text + "\n\n")
                    position += len(text) + 2
                self.reader.setPlainText("".join(parts))
                self.all_findings = self.project.findings(self.current_id)
                run = self.project.db.one("SELECT * FROM scan_runs WHERE id=?", (doc["current_run_id"],))
                self.excluded_count = run["excluded_count"] if run else 0
                if run and run["status"] != "completed":
                    self.coverage_label.setText(self.coverage_label.text() + "\n识别批次未完整成功：" + run["status"] + "，禁止完成审查。")
            except MingjianError as exc:
                self.coverage_label.setText(str(exc))
        self.filter_findings()

    def filter_findings(self, *_):
        current = self.finding_list.currentItem()
        selected_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.finding_list.clear()
        self.visible_findings = []
        for finding in self.all_findings:
            if self.risk_filter.currentIndex() and finding["risk"] != self.risk_filter.currentText():
                continue
            if self.type_filter.currentIndex() and finding["category"] != self.type_filter.currentText():
                continue
            if self.status_filter.currentIndex() and STATUS[finding["status"]] != self.status_filter.currentText():
                continue
            item = QListWidgetItem(f"{finding['risk']}风险 · {finding['category']}  |  {STATUS[finding['status']]}\n原文区间 {finding['start_offset']}—{finding['end_offset']} · {finding['rule_id']}")
            item.setData(Qt.ItemDataRole.UserRole, finding["id"])
            self.finding_list.addItem(item)
            self.visible_findings.append(finding)
            if finding["id"] == selected_id:
                self.finding_list.setCurrentItem(item)
        groups = len({f["group_id"] for f in self.all_findings})
        pending = sum(f["status"] == "pending" for f in self.all_findings)
        self.finding_summary.setText(f"问题组 {groups} · 子命中 {len(self.all_findings)} · 待复核 {pending} · 白名单排除 {self.excluded_count}\n显示 {len(self.visible_findings)} 项；批量操作仅处理明确选中项")
        if not self.finding_list.currentItem() and self.finding_list.count():
            self.finding_list.setCurrentRow(0)

    def select_finding(self, item, previous=None):
        if self.detail_dirty:
            try:
                self.flush_detail()
            except Exception as exc:
                self.finding_list.blockSignals(True)
                self.finding_list.setCurrentItem(previous)
                self.finding_list.blockSignals(False)
                self.save_label.setText("保存失败，未切换问题")
                error(self, exc)
                return
        if not item:
            self.reader.setExtraSelections([])
            self.detail.setText("选中问题查看依据；可多选后批量处理。")
            return
        finding = next((f for f in self.all_findings if f["id"] == item.data(Qt.ItemDataRole.UserRole)), None)
        if not finding:
            return
        block = next(b for b in self.blocks if b.id == finding["block_id"])
        start = finding["start_offset"]
        end = finding["end_offset"]
        display_before = block.text[:start].replace("\r\n", "\n").replace("\r", "\n")
        display_value = block.text[start:end].replace("\r\n", "\n").replace("\r", "\n")
        base = self.block_positions[block.id] + len(display_before)
        full = self.reader.toPlainText()
        cursor = self.reader.textCursor()
        cursor.setPosition(utf16_offset(full, base))
        cursor.setPosition(utf16_offset(full, base + len(display_value)), QTextCursor.MoveMode.KeepAnchor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        # Keep a real selection for keyboard copying; ExtraSelection adds category color.
        self.reader.setTextCursor(cursor)
        fmt = QTextCharFormat()
        color = "#f8d4c6" if finding["category"] not in ("文本规范", "敏感词") else "#efe3b7" if finding["category"] == "敏感词" else "#cee3ef"
        fmt.setBackground(QColor(color))
        fmt.setForeground(QColor("#1f3128"))
        selection.format = fmt
        self.reader.setExtraSelections([selection])
        self.reader.ensureCursorVisible()
        location = json.loads(finding["locator_json"])
        location.pop("characters", None)
        self.position_label.setText(f"块 {block.kind} · 原文 [{start}, {end})")
        self.detail.setText(f"{finding['category']} · {finding['risk']}风险 · {STATUS[finding['status']]}\n依据：{finding['evidence']}\n建议：{finding['suggestion']}\n位置：{dumps(location)}\n忽略原因：{finding['reason'] or '无'}")
        strategy = finding["strategy"]
        self.loading_detail = True
        self.strategy.setCurrentIndex({"full": 0, "partial": 1, "fixed": 2}[strategy.get("mode", "full")])
        self.prefix.setValue(strategy.get("prefix", 3))
        self.suffix.setValue(strategy.get("suffix", 4))
        self.fixed_text.setText(strategy.get("text", ""))
        self.note.setText(finding["note"])
        self.loading_detail = False
        self.loaded_finding_id = finding["id"]
        self.detail_dirty = False

    def review(self, status):
        self.require_document()
        selected = {i.data(Qt.ItemDataRole.UserRole) for i in self.finding_list.selectedItems()}
        if not selected:
            raise MingjianError("请明确选择要处理的问题")
        reason = None
        if status == "ignored":
            reason, ok = QInputDialog.getText(self, "忽略选中问题", f"为选中的 {len(selected)} 项填写原因（1—500 字）")
            if not ok:
                return
        strategy = self.current_strategy()
        self.save_label.setText("保存中")
        updates = []
        for item in self.all_findings:
            if item["id"] in selected:
                update = {"id": item["id"], "revision": item["revision"], "strategy": strategy,
                          "note": self.note.text() if len(selected) == 1 else item["note"]}
                if status:
                    update["status"] = status
                if reason is not None:
                    update["reason"] = reason
                updates.append(update)
        self.project.review_many(self.current_id, updates)
        self.detail_dirty = False
        self.save_label.setText("已保存")
        self.all_findings = self.project.findings(self.current_id)
        self.filter_findings()
        self.refresh_documents()
        doc = self.project.document(self.current_id)
        self.document_title.setText(doc["display_name"] + " · " + REVIEW[doc["review_status"]])

    def undo(self, redo):
        focused = QApplication.focusWidget()
        if isinstance(focused, (QLineEdit, QPlainTextEdit)):
            focused.redo() if redo else focused.undo()
            return
        self.require_document()
        self.flush_detail()
        self.project.undo(self.current_id, redo)
        self.show_document()
        self.refresh_documents()
        self.save_label.setText("已保存")

    def saved(self):
        self.require_project()
        self.flush_detail()
        self.save_label.setText("已保存 · 复核操作均已事务提交")

    def acknowledge(self):
        self.require_document()
        version, _ = self.project.parsed(self.current_id)
        warnings = json.loads(version["warnings_json"])
        message = "\n".join(warnings) + "\n\n确认仅继续审查已支持的文本；未覆盖内容仍未经审查，副本和报告将始终注明部分覆盖。"
        if QMessageBox.question(self, "当前文档覆盖限制", message) == QMessageBox.StandardButton.Yes:
            self.project.acknowledge_coverage(self.current_id)
            self.show_document()

    def reparse(self):
        self.require_document()
        doc = self.project.document(self.current_id)
        encoding = None
        if doc["format"] == "txt":
            dialog = EncodingDialog(self.project.verify_source(self.current_id), self)
            if not dialog.exec():
                return
            encoding = dialog.encoding
        if doc["current_parse_id"] and QMessageBox.question(self, "重新解析", "会创建新文本版本，旧识别历史保留。新版本需要重新识别与复核。继续？") != QMessageBox.StandardButton.Yes:
            return
        document_id, project = self.current_id, self.project
        self.start_task("解析文档", lambda cancel, progress: project.parse(document_id, encoding, cancel))

    def scan_current(self):
        self.require_document()
        self.scan_ids([self.current_id])

    def scan_batch(self):
        self.require_project()
        ids = [d["id"] for d in self.project.documents() if d["parse_status"] in ("success", "partial")]
        self.scan_ids(ids)

    def scan_ids(self, ids):
        if not ids:
            raise MingjianError("没有可识别的已解析文档")
        for document_id in ids:
            version, _ = self.project.parsed(document_id)
            if version["coverage_status"] == "partial" and not version["coverage_ack"]:
                raise MingjianError("批次中存在未确认覆盖限制的文档，请逐文档查看并确认")
        if any(self.project.document(d)["current_run_id"] for d in ids):
            if QMessageBox.question(self, "重新识别", "创建新识别批次，不继承旧复核状态。历史保留，当前结果需重新复核。继续？") != QMessageBox.StandardButton.Yes:
                return
        project, snapshot = self.project, self.project.rules()[0]["id"]
        def work(cancel, progress):
            failures = run_documents(ids, lambda document_id: project.scan(document_id, snapshot, cancel),
                                     project.limits.scan_workers, cancel, progress, "规则识别")
            return {"failures": failures}
        self.start_task("识别文档", work, lambda result: QMessageBox.information(self, "识别任务结束", f"处理 {len(ids)} 个文档，失败 {len(result['failures'])} 个。\n候选问题需要人工复核；零命中仍需明确完成审查。"))

    def complete(self):
        self.require_document()
        self.flush_detail()
        if QMessageBox.question(self, "完成审查", "确认已复核当前规则范围内的全部候选问题，并理解覆盖限制？\n零命中也不代表文档不存在其他敏感内容。") == QMessageBox.StandardButton.Yes:
            self.project.complete(self.current_id)
            self.show_document()
            self.refresh_documents()

    def preview(self):
        self.require_document()
        self.flush_detail()
        _, blocks = self.project.parsed(self.current_id)
        PreviewDialog(blocks, self.project.preview(self.current_id), self).exec()

    def export(self):
        self.require_project()
        self.flush_detail()
        docs = [d for d in self.project.documents() if d["review_status"] == "completed"]
        if not docs:
            raise MingjianError("尚无已完成当前版本审查的文档")
        choice, ok = QInputDialog.getItem(self, "选择副本格式", "报告始终包括 PDF / TXT / JSON", ["TXT + DOCX + PDF", "TXT + DOCX", "仅 TXT", "仅 PDF", "仅 DOCX"], 0, False)
        if not ok:
            return
        formats = {"TXT + DOCX + PDF": ("txt", "docx", "pdf"), "TXT + DOCX": ("txt", "docx"),
                   "仅 TXT": ("txt",), "仅 PDF": ("pdf",), "仅 DOCX": ("docx",)}[choice]
        if QMessageBox.question(self, "导出已完成文档", f"导出全部 {len(docs)} 个已完成审查文档的 TXT / DOCX / PDF 副本及报告。\n使用匿名文档序号命名；保留完整回查和清单。继续？") != QMessageBox.StandardButton.Yes:
            return
        project, ids = self.project, [d["id"] for d in docs]
        def done(result):
            if QMessageBox.question(self, "导出校验通过", "输出已发布到：\n" + result["path"] + "\n\n现在打开目录？") == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl.fromLocalFile(result["path"]))
        self.start_task("生成副本与审查报告", lambda cancel, progress: export_project(project, ids, formats=formats, cancel=cancel, progress=progress), done)

    def rules(self):
        self.require_project()
        RulesDialog(self.project, self.current_id, self).exec()

    def settings(self):
        self.require_project()
        SettingsDialog(self.project, self).exec()

    def history(self):
        self.require_project()
        rows = self.project.db.rows("SELECT * FROM export_records ORDER BY created_at DESC")
        dialog = QDialog(self)
        dialog.setWindowTitle("导出历史与校验记录")
        dialog.resize(1050, 560)
        layout = QVBoxLayout(dialog)
        widget = table(["本地时间", "状态", "输出位置", "校验"], [[local_time(r["created_at"]), TASK_STATUS.get(r["status"], r["status"]) if not r["output_path"] or Path(r["output_path"]).exists() else "输出已被移除", r["output_path"] or "—", "通过" if r["validation_json"] and json.loads(r["validation_json"])["passed"] else r["error"] or "—"] for r in rows])
        layout.addWidget(widget)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        layout.addWidget(details)
        def selected():
            index = widget.currentRow()
            if index >= 0:
                details.setPlainText(rows[index]["validation_json"] or rows[index]["error"] or "未执行校验")
        widget.itemSelectionChanged.connect(selected)
        button = QPushButton("打开选中输出目录")
        def open_output():
            index = widget.currentRow()
            if index >= 0 and rows[index]["output_path"] and Path(rows[index]["output_path"]).is_dir():
                QDesktopServices.openUrl(QUrl.fromLocalFile(rows[index]["output_path"]))
        button.clicked.connect(open_output)
        layout.addWidget(button)
        report_button = QPushButton("打开选中审查报告 PDF")
        def open_report():
            index = widget.currentRow()
            if index >= 0 and rows[index]["output_path"]:
                path = Path(rows[index]["output_path"]) / "report.pdf"
                if path.is_file():
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        report_button.clicked.connect(open_report)
        layout.addWidget(report_button)
        dialog.exec()

    def tasks(self):
        if not self.project:
            raise MingjianError("请先打开项目")
        rows = self.project.db.rows("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 200")
        dialog = QDialog(self)
        dialog.setWindowTitle("任务记录")
        dialog.resize(960, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(table(["任务", "状态", "进度", "失败", "本地开始时间", "本地结束时间"], [[r["kind"], TASK_STATUS.get(r["state"],r["state"]), f"{r['progress']}/{r['total']}", r["failures"], local_time(r["created_at"]), local_time(r["finished_at"])] for r in rows]))
        dialog.exec()

    def backup(self):
        self.require_project()
        path, _ = QFileDialog.getSaveFileName(self, "保存完整项目备份", "mingjian-backup.zip", "ZIP (*.zip)")
        if path:
            project = self.project
            self.start_task("备份项目", lambda cancel, progress: backup_project(project, path), lambda result: QMessageBox.information(self, "备份完成", result))

    def restore(self):
        if self.worker:
            raise MingjianError("请等待任务结束")
        path, _ = QFileDialog.getOpenFileName(self, "选择明鉴备份", "", "ZIP (*.zip)")
        if not path:
            return
        parent = QFileDialog.getExistingDirectory(self, "选择恢复位置（创建新项目，不覆盖已有项目）")
        if parent:
            self.start_task("校验并恢复备份", lambda cancel, progress: restore_project(path, parent), self.load_project, tracked=False)

    def remove_current(self):
        self.require_document()
        self.flush_detail()
        doc = self.project.document(self.current_id)
        if QMessageBox.question(self, "移入项目回收区", f"文档：{doc['display_name']}\n快照：{doc['source_path']}\n项目中的解析与识别历史将隐藏保留，可从回收区恢复。\n原始路径中的文件不受影响。确认移除？") == QMessageBox.StandardButton.Yes:
            self.project.remove(self.current_id)
            self.current_id = None
            self.refresh_documents()
            self.show_document()

    def trash(self):
        self.require_project()
        docs = self.project.documents(True)
        if not docs:
            QMessageBox.information(self, "回收区", "回收区为空")
            return
        labels = [f"{d['display_name']} · {d['id']}" for d in docs]
        label, ok = QInputDialog.getItem(self, "恢复文档", "选择要恢复的文档快照与历史", labels, 0, False)
        if ok:
            self.project.remove(docs[labels.index(label)]["id"], restore=True)
            self.refresh_documents()

    def clean_cache(self):
        self.require_project()
        clear_cache(self.project)
        QMessageBox.information(self, "缓存已清理", "已清理可重建缓存；源快照、规则与复核结果均保留。")

    def find_text(self):
        self.require_document()
        text, ok = QInputDialog.getText(self, "查找当前文档", "查找文本")
        if ok and text and not self.reader.find(text):
            cursor = self.reader.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.reader.setTextCursor(cursor)
            if not self.reader.find(text):
                QMessageBox.information(self, "查找", "未找到所填文本")

    def navigate(self, delta):
        if self.finding_list.count():
            self.finding_list.setCurrentRow((self.finding_list.currentRow()+delta) % self.finding_list.count())

    def help(self):
        QMessageBox.information(self, "使用说明与快捷键", "新建项目 → 导入并解析 → 确认覆盖限制 → 识别 → 逐项确认/忽略 → 脱敏预览 → 完成审查 → 导出。\n\nCtrl+F 查找　Alt+↑/↓ 问题导航\nCtrl+Enter 确认　Ctrl+I 忽略\nCtrl+S 保存状态　Ctrl+Z 撤销\nCtrl+Shift+Z 重做\n\n完整操作手册见交付目录 docs/用户操作手册.md。\n重复标点仅检查配置的同符号连续；括号引号按段落配对；编号仅检查显式顶层 n. / n、 且后跟空白的连续条目。")

    def dragEnterEvent(self, event):
        if self.project and not self.worker and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.safe(lambda: self.import_paths(paths))

    def closeEvent(self, event):
        if self.worker:
            QMessageBox.information(self, "后台任务执行中", "请先请求取消，等待安全检查点结束后关闭。已提交的复核结果已保存。")
            event.ignore()
            return
        try:
            self.flush_detail()
        except Exception as exc:
            self.save_label.setText("保存失败，尚未退出")
            error(self, exc)
            event.ignore()
            return
        if self.project:
            self.project.close()
        event.accept()
