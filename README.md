# 知证学伴

依据《知证学伴-方案V2》实现的 React + FastAPI 学习应用：选择讲义和知识点，作答并标注信心，回查原文证据，按所选错误选项对应的误区补练，再在后续练习中复测。

## 快速运行

需要 Python 3.11+、Node.js 20.19+。所有命令在本目录执行。无 API Key 即可体验完整的示例学习闭环。

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1 -Install
```

之后再次运行可省略 `-Install`。脚本只为这次执行设置策略，不修改系统执行策略。

Linux / macOS：

```bash
sh scripts/start.sh
```

打开 <http://127.0.0.1:8000>。API 文档位于 <http://127.0.0.1:8000/docs>。按 Ctrl+C 停止。

若 8000 端口被系统保留或占用，Windows 可追加 `-Port 8765`；Linux / macOS 可使用 `PORT=8765 sh scripts/start.sh`。本次环境已在 8765 端口完成浏览器验证。

## Docker

```bash
docker compose up --build -d
```

仍访问 8000 端口。`study-data` 卷保存 SQLite、上传 PDF 和向量缓存。使用单 worker，避免进程内后台任务被重复调度。可选 `.env` 需要支持 `env_file.required` 的 Docker Compose；旧版 Compose 可先复制 `.env.example` 为 `.env`，并改成普通 `env_file: .env`。

## 实时模式

复制 `.env.example` 为 `.env`，填写服务端参数并重启：

```dotenv
LLM_API_KEY=你的模型服务密钥
LLM_BASE_URL=https://你的兼容服务/v1
LLM_MODEL=该服务支持的文本模型
REVIEW_MODEL=该服务支持的复核模型
EMBED_BASE_URL=https://你的向量服务/v1
EMBED_MODEL=该服务支持的向量模型
```

独立向量供应商可配置 `EMBED_API_KEY`；独立复核供应商可配置 `REVIEW_BASE_URL` / `REVIEW_API_KEY`。接口使用 OpenAI 兼容 Chat Completions JSON 模式与 embeddings；服务须支持 `response_format: json_object`。密钥只保存在后端，不写入前端环境变量。

实时流程：上传文本型 PDF → 查看抽取样本 → 勾选、修改候选知识点 → 确认 → 后台建立证据包、生成误区和题库。达到 3 题的知识点可开始练习。模型失败、预算耗尽或重启会留下明确状态；资料页可继续生成。默认上传限制 10 MB / 30 页，不支持扫描 PDF / OCR。公开演示部署建议保持无 Key 模式。

## 已实现的学习行为

- **资料**：示例讲义、PDF 导入、物理页码及句子 ID、候选知识点确认、知识点与误区覆盖度、后台处理状态。
- **练习**：三道未答题、目标误区不同、信心必须主动选择、服务端判分、首次提交唯一约束、刷新恢复、逐项反馈和原文上下文。
- **补练**：按选错的干扰项对应误区取未做过的题；实时模式下自有上传资料库存不足时生成，仍走相同质量检查。
- **复习**：高信心错误优先、待纠正 / 纠正中 / 近期已纠正、知识点答题汇总、信心与对错统计、猜对复查、下一轮自动混入复测。
- **AI 工作流**：向量 Top-4 与关键词补充并集、纠正句强制进入证据包、代码校验、随机选项映射、独立盲答调用、最多一次修复、调用日志、预算与有限重试。

状态仅描述观察到的答题表现；近期已纠正要求上次错误后在至少两道不同的相关题上答对。系统不输出未经验证的掌握度或提分承诺。

## 示例内容与真实性

`examples/source_notes.md` 是本项目原创演示讲义，按物理页面编排；`examples/sample_notes.pdf` 为其可打印版本。`seed/demo.sqlite` 随项目分发，首次启动复制到数据目录，无 Key 不需要外部服务。

重新构建离线题库：`python scripts/build_seed.py --force`。真实模型生成后，可使用 `python scripts/build_seed.py --source-db data/app.sqlite --document-id 实际文档ID --output seed/reviewed.sqlite` 导出该资料及通过盲答复核的题；不会导出会话、答题或调用日志。生成配套讲义：`python scripts/build_pdf.py`。离线演示直接使用证据包与题库，不附造假的模型向量缓存；真实导入产生的缓存保存在数据目录。

**示例内容由编码助手离线编写，并通过程序结构检查；尚未经过独立人工审定或真实模型盲答复核。** 前端和数据库保留此来源标记，不能把示例库描述为人工审定或线上生成结果。实时模式生成的题则必须通过代码校验和模型盲答才会入库。正式参赛前应由团队核对示例内容，记录审定人和修订事实。

证据句由后端按 ID 查表回填，不接受模型指定页码或引文。但“引文存在”不等于语义上必然支持答案，模型复核也不能替代人工评测。

## 开发与检查

```powershell
# 后端（另开终端；先安装依赖）
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000
# 前端
cd frontend
npm ci
npm run dev
```

前端开发地址 <http://127.0.0.1:5173>，通过 Vite 代理调用 API。生产构建由 FastAPI 同源提供。Python 实际环境锁定记录在 `requirements-lock.txt`，前端版本由 `package-lock.json` 锁定。

```powershell
.\.venv\Scripts\python.exe -m pytest
cd frontend
npm run build
```

```powershell
# 从真实运行数据库导出调用日志与汇总；不导出原文或会话 ID
.\.venv\Scripts\python.exe scripts/export_metrics.py --db data/app.sqlite
```

详见 `docs/verification.md` 的本次实际验证结果。无凭证时不能声称验证了真实供应商调用、真实生成质量或费用；测试中的模型替身只验证协议和流程。Docker 配置需在安装 Docker 的环境中进一步验证。

## 项目结构

```text
frontend/             React + TypeScript 三页应用
backend/main.py       FastAPI 路由、会话、后台任务和学习逻辑
backend/db.py         SQLite 表结构
backend/seed_data.py  示例讲义和题库的可追溯内容
backend/ai/           PDF 解析、检索、模型调用与质量校验
backend/prompts/      版本化提示词
scripts/             启动、种子构建、PDF 生成和指标导出
seed/                无 Key 示例数据库
examples/            示例讲义
tests/               自动化工程测试
eval/                人工评测表与实际结果
docs/                架构、验证结果和录制脚本
```

会话通过 httpOnly Cookie 绑定当前浏览器；清除 Cookie 后无法再访问原会话记录。数据库中的旧记录不会自动物理删除。本项目按方案不提供完整账号系统、班级管理、OCR 或自由问答。

## 提交前仍需完成

真实模型服务的端到端验收、独立内容审定、冻结测试集及留出资料评测、真人用户测试、正式录屏、竞赛技术报告 PDF、比赛规则核对与实际部署。仓库提供对应脚本和记录模板，不虚构上述活动的完成情况。方案中的可选 P1（计算题模拟器 / 先找依据模式）未纳入这一版。
