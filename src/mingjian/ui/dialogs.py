from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from mingjian.domain.models import Limits, MingjianError, RULE_NAMES, dumps
from mingjian.engines.rules import CSV_COLUMNS, dictionary_csv_preview, validate_config
from mingjian.reporting.writers import font_path


def error(parent, exc):
    text = str(exc) if isinstance(exc, MingjianError) else f"操作失败（{type(exc).__name__}），请检查磁盘空间、路径和权限"
    QMessageBox.warning(parent, "明鉴 · 操作未完成", text)


def table(headers, rows):
    widget = QTableWidget(len(rows), len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            widget.setItem(i, j, item)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.verticalHeader().hide()
    return widget


class ImportDialog(QDialog):
    def __init__(self, preview, parent=None):
        super().__init__(parent)
        self.setWindowTitle("确认导入 · 原始文件保持不变")
        self.resize(900, 470)
        layout = QVBoxLayout(self)
        ready = sum(item["status"] == "ready" for item in preview)
        layout.addWidget(QLabel(f"准备导入 {ready} 个文件；其余 {len(preview)-ready} 个将跳过。确认后保存源文件快照。"))
        states = {"ready": "可导入", "duplicate": "重复跳过", "rejected": "拒绝"}
        layout.addWidget(table(["文件", "大小 MiB", "状态", "说明"], [[p["name"], f"{p['size']/1024**2:.3f}", states[p["status"]], p["reason"]] for p in preview]))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导入并解析")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ready > 0)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class EncodingDialog(QDialog):
    def __init__(self, path, parent=None):
        import codecs
        super().__init__(parent)
        self.setWindowTitle("TXT 编码预览 · 严格解码")
        self.resize(760, 500)
        self.encoding = None
        with Path(path).open("rb") as stream:
            self.raw = stream.read(16_384)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择编码并检查预览；正式解析会读取全部文件且严格校验，不丢弃无法解码字符。"))
        self.choice = QComboBox()
        self.choice.addItems(["自动（BOM / 严格 UTF-8）", "gb18030", "utf-8", "utf-16"])
        layout.addWidget(self.choice)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        def update():
            self.encoding = None if self.choice.currentIndex() == 0 else self.choice.currentText()
            chosen = self.encoding or ("utf-16" if self.raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8-sig")
            try:
                # A preview may end in the middle of a multi-byte character.
                text = codecs.getincrementaldecoder(chosen)(errors="strict").decode(self.raw, final=False)
                self.preview.setPlainText(text)
                self.message.setText(f"预览编码：{chosen}。仅预览前 16 KiB；正式解析仍会校验全部内容。")
                buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
            except UnicodeError:
                self.preview.clear()
                self.message.setText("所选编码无法严格解码，请尝试其他编码或提供正确文本副本。")
                buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.choice.currentIndexChanged.connect(update)
        update()


class PreviewDialog(QDialog):
    def __init__(self, blocks, result, parent=None):
        super().__init__(parent)
        self.setWindowTitle("脱敏预览 · 原文与副本")
        self.resize(1100, 740)
        layout = QVBoxLayout(self)
        summary = f"计划替换 {len(result['plan'])} 处；冲突 {len(result['conflicts'])} 组。仅已确认项参与替换。"
        layout.addWidget(QLabel(summary))
        if result["conflicts"]:
            label = QLabel("存在交叉区间：请返回问题列表，保留需要的确认项，其余项填写原因后忽略。冲突解决前不能完成审查或导出。")
            label.setWordWrap(True)
            label.setObjectName("warning")
            layout.addWidget(label)
        row = QHBoxLayout()
        for title, texts in (("原始解析文本", [b.text for b in blocks]), ("脱敏文本", [result["texts"][b.id] for b in blocks])):
            pane = QVBoxLayout()
            pane.addWidget(QLabel(title))
            editor = QPlainTextEdit()
            editor.setReadOnly(True)
            editor.setPlainText("\n\n".join(texts))
            pane.addWidget(editor)
            row.addLayout(pane)
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)


class RulesDialog(QDialog):
    def __init__(self, project, document_id=None, parent=None):
        super().__init__(parent)
        self.project = project
        self.document_id = document_id
        snapshot, self.config = project.rules()
        self.setWindowTitle(f"规则中心 · 当前方案 v{snapshot['version']}")
        self.resize(960, 700)
        layout = QVBoxLayout(self)
        label = QLabel("发布创建不可变新版本；历史识别批次使用原快照。复核已完成的文档需手动重新识别以应用新方案。")
        label.setWordWrap(True)
        layout.addWidget(label)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        rules_page = QWidget()
        rules_layout = QVBoxLayout(rules_page)
        self.rule_controls = {}
        for rule_id, options in self.config["rules"].items():
            row = QHBoxLayout()
            enabled = QCheckBox(RULE_NAMES[rule_id])
            enabled.setChecked(options["enabled"])
            risk = QComboBox()
            risk.addItems(["高", "中", "低"])
            risk.setCurrentText(options["risk"])
            row.addWidget(enabled, 1)
            row.addWidget(QLabel("风险"))
            row.addWidget(risk)
            self.rule_controls[rule_id] = (enabled, risk)
            rules_layout.addLayout(row)
        rules_layout.addStretch()
        tabs.addTab(rules_page, "内置规则")
        words_page = QWidget()
        words_layout = QVBoxLayout(words_page)
        words_layout.addWidget(QLabel("可导入 UTF-8 CSV；固定列名见导出模板。错误行会阻止本次导入，原方案不变。"))
        self.words_table = table(["词语", "分类", "风险", "大小写", "启用"], [])
        words_layout.addWidget(self.words_table)
        toolbar = QHBoxLayout()
        for text, callback in (("新增词条", self.add_word), ("删除选中词条", self.remove_word), ("导入 CSV", self.import_csv), ("导出 CSV / 模板", self.export_csv)):
            btn = QPushButton(text)
            btn.clicked.connect(callback)
            toolbar.addWidget(btn)
        words_layout.addLayout(toolbar)
        tabs.addTab(words_page, "敏感词库")
        whitelist_page = QWidget()
        whitelist_layout = QVBoxLayout(whitelist_page)
        whitelist_layout.addWidget(QLabel("仅支持明确值或规则。若同时填写，两者都匹配时才排除。单文档范围使用当前选中文档。"))
        self.white_table = table(["范围", "文档 ID", "规则 ID", "值", "原因"], [])
        whitelist_layout.addWidget(self.white_table)
        row = QHBoxLayout()
        for text, callback in (("新增白名单", self.add_whitelist), ("删除选中", self.remove_whitelist)):
            btn = QPushButton(text)
            btn.clicked.connect(callback)
            row.addWidget(btn)
        whitelist_layout.addLayout(row)
        tabs.addTab(whitelist_page, "白名单")
        advanced = QWidget()
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.addWidget(QLabel("完整方案 JSON：可修改逐规则规范化开关。点击“从当前表单更新”后编辑；发布时校验。"))
        self.json_editor = QPlainTextEdit()
        self.json_editor.setPlainText(json.dumps(self.config, ensure_ascii=False, indent=2))
        self.use_json = QCheckBox("发布时使用下面的 JSON（勾选后不采用其他页表单）")
        advanced_layout.addWidget(self.use_json)
        advanced_layout.addWidget(self.json_editor)
        update = QPushButton("从当前表单更新 JSON")
        update.clicked.connect(self.update_json)
        advanced_layout.addWidget(update)
        tabs.addTab(advanced, "方案 JSON")
        versions = project.db.rows("SELECT version,created_at,content_hash FROM rule_snapshots ORDER BY version DESC")
        tabs.addTab(table(["版本", "发布时间 UTC", "摘要"], [[v["version"], v["created_at"], v["content_hash"]] for v in versions]), "版本历史")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("发布新版本")
        buttons.accepted.connect(self.publish)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_tables()

    def refresh_tables(self):
        for widget, rows in ((self.words_table, [[w["word"], w.get("category", "敏感词"), w["risk"], "区分" if w.get("case_sensitive", True) else "忽略", w.get("enabled", True)] for w in self.config["words"]]),
                             (self.white_table, [[w["scope"], w.get("document_id", ""), w.get("rule_id", ""), w.get("value", ""), w["reason"]] for w in self.config["whitelist"]])):
            widget.setRowCount(len(rows))
            for i, row in enumerate(rows):
                for j, value in enumerate(row):
                    widget.setItem(i, j, QTableWidgetItem(str(value)))

    def form(self):
        for rule_id, (enabled, risk) in self.rule_controls.items():
            self.config["rules"][rule_id].update(enabled=enabled.isChecked(), risk=risk.currentText())
        return self.config

    def update_json(self):
        self.json_editor.setPlainText(json.dumps(self.form(), ensure_ascii=False, indent=2))

    def add_word(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("新增敏感词")
        layout = QFormLayout(dialog)
        word, category, suggestion = QLineEdit(), QLineEdit("敏感词"), QLineEdit("复核后替换")
        risk = QComboBox()
        risk.addItems(["高", "中", "低"])
        case = QCheckBox("区分大小写")
        case.setChecked(True)
        for title, widget in (("词语", word), ("分类", category), ("风险", risk), ("建议", suggestion), ("策略", case)):
            layout.addRow(title, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec():
            item = {"word": word.text().strip(), "category": category.text(), "risk": risk.currentText(),
                    "suggestion": suggestion.text(), "case_sensitive": case.isChecked(), "enabled": True}
            try:
                config = json.loads(dumps(self.config))
                config["words"].append(item)
                validate_config(config)
                self.config = config
                self.refresh_tables()
            except Exception as exc:
                error(self, exc)

    def remove_word(self):
        indices = sorted({i.row() for i in self.words_table.selectedIndexes()}, reverse=True)
        for index in indices:
            self.config["words"].pop(index)
        self.refresh_tables()

    def import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入 UTF-8 CSV", "", "CSV (*.csv)")
        if not path:
            return
        try:
            result = dictionary_csv_preview(Path(path).read_text(encoding="utf-8-sig"), self.config["words"])
            if result["errors"]:
                dialog = QDialog(self)
                dialog.setWindowTitle("CSV 错误清单 · 本次未导入")
                layout = QVBoxLayout(dialog)
                layout.addWidget(table(["行", "错误"], [[r["line"], r["error"]] for r in result["errors"]]))
                save = QPushButton("保存错误清单 JSON")
                def save_errors():
                    dest, _ = QFileDialog.getSaveFileName(dialog, "保存错误清单", "dictionary-errors.json", "JSON (*.json)")
                    if dest:
                        Path(dest).write_text(dumps(result["errors"]), encoding="utf-8")
                save.clicked.connect(save_errors)
                layout.addWidget(save)
                dialog.resize(660, 400)
                dialog.exec()
                return
            if QMessageBox.question(self, "CSV 预览", f"新增 {len(result['words'])} 行，重复跳过 {len(result['duplicates'])} 行。加入草稿？") == QMessageBox.StandardButton.Yes:
                self.config["words"].extend(result["words"])
                self.refresh_tables()
        except Exception as exc:
            error(self, exc)

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出词库 / 空模板", "dictionary.csv", "CSV (*.csv)")
        if path:
            try:
                output = io.StringIO()
                writer = csv.DictWriter(output, CSV_COLUMNS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(self.config["words"])
                Path(path).write_text(output.getvalue(), encoding="utf-8-sig")
            except Exception as exc:
                error(self, exc)

    def add_whitelist(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("新增白名单")
        layout = QFormLayout(dialog)
        scope = QComboBox()
        scope.addItems(["全项目", "当前文档"])
        value, rule, reason = QLineEdit(), QLineEdit(), QLineEdit()
        for title, widget in (("范围", scope), ("精确值（可空）", value), ("规则 ID（可空）", rule), ("原因", reason)):
            layout.addRow(title, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec():
            try:
                config = json.loads(dumps(self.config))
                config["whitelist"].append({"scope": "document" if scope.currentIndex() else "project",
                    "document_id": self.document_id if scope.currentIndex() else None, "value": value.text(),
                    "rule_id": rule.text(), "reason": reason.text(), "enabled": True})
                validate_config(config)
                self.config = config
                self.refresh_tables()
            except Exception as exc:
                error(self, exc)

    def remove_whitelist(self):
        for index in sorted({i.row() for i in self.white_table.selectedIndexes()}, reverse=True):
            self.config["whitelist"].pop(index)
        self.refresh_tables()

    def publish(self):
        try:
            config = json.loads(self.json_editor.toPlainText()) if self.use_json.isChecked() else self.form()
            self.project.publish_rules(config)
            self.accept()
        except Exception as exc:
            error(self, exc)


class SettingsDialog(QDialog):
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("设置与维护")
        layout = QFormLayout(self)
        self.mib = QSpinBox()
        self.mib.setRange(5, 200)
        self.mib.setValue(project.limits.max_mib)
        layout.addRow("单文件限制 MiB", self.mib)
        self.parse_workers, self.scan_workers = QSpinBox(), QSpinBox()
        for control, value in ((self.parse_workers, project.limits.parse_workers), (self.scan_workers, project.limits.scan_workers)):
            control.setRange(1, 4)
            control.setValue(value)
        layout.addRow("解析并发", self.parse_workers)
        layout.addRow("识别并发", self.scan_workers)
        root = QLabel(str(project.root))
        root.setWordWrap(True)
        layout.addRow("当前项目目录", root)
        font = QLabel(str(font_path()))
        font.setWordWrap(True)
        layout.addRow("离线导出字体", font)
        layout.addRow(QLabel("后台调度默认 2 个文档并发；同一文档任务互斥，数据库独立写入队列。\n单次最多 200 文件；PDF 最多 300 页；文档最多 200 万字符。\n本版本不记录文档原文日志。项目目录可在新建项目时选择。"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def save(self):
        try:
            current = self.project.limits
            limits = Limits(**{**current.__dict__, "max_mib": self.mib.value(),
                              "parse_workers": self.parse_workers.value(), "scan_workers": self.scan_workers.value()})
            self.project.save_limits(limits)
            self.accept()
        except Exception as exc:
            error(self, exc)
