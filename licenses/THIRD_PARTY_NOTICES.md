# 第三方组件与分发说明

本目录的 `dependency-inventory.json` 根据实际安装的元数据生成，`components/` 保存各包随附的许可证、NOTICE 等文件。依赖源码不作为明鉴自主代码申报。字体附单独 OFL 文本。

PySide6 Essentials / Shiboken6 / Qt 采用可选商业或开源许可；本构建按 LGPLv3/GPLv3 及 Qt 各模块适用条款分发，只导入 Qt Core / Gui / Widgets。Qt 动态库与 Python 扩展保留为独立文件，不静态链接；应用不禁止用户替换或调试这些库，不限制为调试库修改所需的逆向工程。相关许可副本位于 components。Qt 原始源码与 LGPL 说明可从 [Qt 官方源码档案](https://download.qt.io/archive/qt/) 和 [Qt for Python](https://code.qt.io/pyside/pyside-setup.git/) 获取，版本见清单；分发者应保留相应版本的来源和条款。此清单为工程分发记录，正式商业发行前仍需由权利人复核许可义务。

Noto Sans SC、Noto Emoji 使用 SIL Open Font License 1.1。随附静态字体由 Google Fonts 官方仓库中的变量字体固定 wght=400 生成；OFL 原文随附。未分发 macOS 系统专有字体。字体不是明鉴自主作品。

python-docx（MIT）、pdfplumber（MIT）、pdfminer.six（MIT）、ReportLab（BSD）、Matplotlib（Matplotlib/PSF 兼容许可）、NumPy、Pillow、lxml、cryptography 等各自适用具体随附条款。pypdfium2 / PDFium 的第三方通知保留在其包与清单中。PyInstaller 是构建工具，适用其 bootloader 例外；pytest/ruff 等仅用于开发测试。

本项目新增代码的权利人、开发方式、完成日期和发表信息由用户依据实际情况填写；当前不替用户预填权属或虚构验收结论。
