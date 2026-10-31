# 明鉴 · 文档敏感信息识别与脱敏系统

V1.2 是运行在本机的中文文档工作台。拖入 DOCX、PDF、TXT 或图片，提取文字、识别候选、人工确认，再生成经过独立回查的脱敏副本与报告。

**使用者需自行准备大模型 API。此仓库不包含可用密钥、运行数据库或个人软著申请资料。** 不配置外部服务仍可使用本地规则；模型和 OCR 请求发送前必须明确确认。

## 从源码运行

建议 Python 3.11，使用 uv 安装锁定依赖。当前实际验证平台为 macOS 26.6.2 / Apple Silicon；其他平台需要对应环境测试。

```sh
git clone https://github.com/xiaoashi/mingjian-doc-redactor.git
cd mingjian-doc-redactor
uv sync --extra dev --frozen
uv run python scripts/setup_resources.py --samples
uv run python -m mingjian.web.launcher
```

页面地址为 http://127.0.0.1:8765 。也可在 macOS 双击 `启动明鉴网页.command`。服务只监听本机回环地址。停止时使用网页“识别设置 → 停止本机服务”，或命令窗口 Ctrl+C。

源码仓库附前端构建结果，正常启动不要求 Node。初次安装需要下载普通依赖和 OFL 字体；`setup_resources.py` 验证字体摘要并生成确定性合成样本，不下载模型、不需要 API key。完成准备后，本地文字流程可离线运行。更改前端时运行：

```sh
cd frontend
npm ci
npm run build
```

## 模型与 OCR

识别设置可以填写兼容服务的 Base URL、模型名称和密码框。默认示例为 ECNU `ecnu-max`，支持图片读取和文字辅助复核。密码框只保留当前运行进程的密钥，不写入设置文件；修改服务地址后必须重新配置。`MINGJIAN_AI_API_KEY` 等环境变量也可由使用者自行设置，`.env.example` 仅为说明，程序不会自动加载 `.env`。

AI 视觉最多处理8页PDF/8张图片或DOCX嵌入图片，结果仍需对照原件；AI语义复核最多64000字符，建议必须逐字定位原文。它不会自动完成审查。可选 MinerU 协议适配与模型接口分开配置。

## 使用流程

导入后检查覆盖提示；扫描件需要OCR或视觉转写，再人工核对文字。点击候选高亮原文，选择确认或填写原因忽略，可调整替换方式、备注和预览。待复核项、失败规则、未确认覆盖和重叠冲突会阻止最终导出，零命中仍需明确完成复核。

输出是重新排版的 TXT/DOCX/PDF，不保留复杂原版式。报告、图表、manifest 与 validation 一起生成。副本重新读取并核对预期文字，通过后才发布；原文件不覆盖。

## 测试

```sh
uv run python scripts/setup_resources.py --samples
QT_QPA_PLATFORM=offscreen uv run python -m pytest -q
# 可选：生成并对照固定合成集
uv run python scripts/generate_fixtures.py
uv run python scripts/benchmark.py
```

自动测试不需要真实 API，用显式模拟响应验证接口与审查门槛。开发时已执行68项自动测试，并用自行提供的ECNU API对10份公开/派生/合成样本作真实调用。手写结果仍存在差异，不将这些演示称为全面准确率或独立认证。详情见 `docs/model-validation-summary.md`。

## 代码与资源

`frontend/` 为可编辑的网页源代码；`src/mingjian/` 包含解析、规则、AC匹配、位置映射、版本化复核、模型适配与导出回查。`tests/` 为自动测试。字体按 `resources-fonts.json` 下载并固化字重，许可位于 `licenses/`；合成样本由脚本生成。历史 Qt 入口保留，但当前主入口是网页。

未确定再分发授权的第三方照片、个人申请表和身份材料不进入此仓库。公开样本来源链接另列，不将第三方组件或样本声称为自主代码。仓库提供源码下载与运行方法，目前未选择通用开源许可证；进一步修改、商用和再分发授权请向实际权利人确认。
