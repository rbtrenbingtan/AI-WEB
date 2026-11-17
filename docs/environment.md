# 环境配置指南

本指南适用于从公开仓库克隆的源码。所有命令默认在项目根目录执行。

## 基础环境与依赖文件

| 项目 | 要求或用途 |
| --- | --- |
| Git | 克隆仓库并保留完整提交历史 |
| Python | 3.11 及以上；Docker 使用 Python 3.11 |
| Node.js | 推荐 22.x（至少 22.12.0）或 24.x；`.nvmrc` 指定 22 系列 |
| npm | 随 Node.js 安装，使用 `npm ci` 按锁文件安装 |
| `requirements.txt` | Python 直接依赖及版本范围 |
| `requirements-lock.txt` | Python 完整固定版本清单，启动脚本与 Docker 均使用此文件 |
| `frontend/package.json` | 前端运行、构建和测试依赖及命令 |
| `frontend/package-lock.json` | 前端依赖锁文件 |
| `tools/submission/requirements.txt` | 可选的 PDF / 视频材料制作依赖，不属于应用运行必需项 |

Vitest 在当前锁文件中声明的 Node.js 范围为 `^22.12.0 || ^24.0.0 || >=26.0.0`。Node.js 20.19 可以满足部分构建工具，但不能满足本项目完整测试工具链要求。

应用无需 Redis、外部数据库或外部向量数据库。默认 SQLite 和离线示例库可以在无模型密钥时运行。

## 安装与启动

```bash
git clone https://github.com/rbtrenbingtan/AI-WEB.git
cd AI-WEB
```

macOS / Linux 可直接运行 `sh scripts/start.sh`；Windows PowerShell 可运行 `powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1 -Install`。脚本会建立虚拟环境、安装锁定依赖、构建前端并启动应用。

如需手动安装，macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
cp .env.example .env
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Windows PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
npm --prefix frontend ci
npm --prefix frontend run build
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

安装了其他受支持 Python 版本时，可将 `py -3.11` 改为相应版本。macOS / Linux 使用 nvm 管理 Node.js 时，可先执行 `nvm install` 和 `nvm use`，读取项目的 `.nvmrc`。

应用地址为 <http://127.0.0.1:8000>，API 文档为 <http://127.0.0.1:8000/docs>，健康检查为 <http://127.0.0.1:8000/api/health>。无 Key 模式保留模板中的空密钥即可；首次启动从 `seed/demo.sqlite` 复制示例数据库到数据目录。

PDF 解析使用独立子进程和总超时。Linux 使用 `RLIMIT_AS`，Windows 使用 Job 进程内存限制；macOS 由父进程通过系统 `/bin/ps` 每约 50 毫秒采样 RSS，超过 512 MiB 或无法监测即终止解析。macOS 的采样保护可能短暂越过阈值，不等同于内核严格内存上限。

开发时可在两个终端分别运行后端的 `uvicorn ... --reload` 和 `npm --prefix frontend run dev`。前端开发地址为 <http://127.0.0.1:5173>，Vite 将 API 请求代理到本地 8000 端口。

## 环境变量

复制根目录 `.env.example` 为 `.env` 后修改。后端与配置检查工具会读取根目录 `.env`，已经设置的进程环境变量优先。下表列出未设置时的默认值；模型地址、模型名称和独立凭证的空值回退见表。布尔和数值变量请保留有效值，不能用空字符串代替默认值。模板中的 `EMBED_BASE_URL` 显式填写了 OpenAI 地址，使用其他服务时应同步修改。

| 变量 | 默认值 / 回退 | 用途 |
| --- | --- | --- |
| `DATA_DIR` | 项目根目录下的 `data`；模板为 `./data` | SQLite、上传 PDF 和向量缓存目录；相对路径按启动工作目录解析 |
| `LLM_API_KEY` | 空 | 生成模型密钥；非空时启用实时模式 |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容生成接口根地址 |
| `LLM_MODEL` | `gpt-4o-mini` | 代码内置的生成模型回退名称；实际应填写供应商支持的文本模型 |
| `REVIEW_MODEL` | 同 `LLM_MODEL` | 独立复核所用模型名称 |
| `REVIEW_BASE_URL` | 同 `LLM_BASE_URL` | 独立复核接口地址 |
| `REVIEW_API_KEY` | 同 `LLM_API_KEY` | 独立复核接口密钥 |
| `EMBED_BASE_URL` | 同 `LLM_BASE_URL` | 向量接口地址；模板显式设置为 `https://api.openai.com/v1` |
| `EMBED_API_KEY` | 同 `LLM_API_KEY` | 向量接口密钥 |
| `EMBED_MODEL` | `text-embedding-3-small` | 代码内置的向量模型回退名称；实际应填写供应商支持的模型 |
| `LLM_TIMEOUT_SECONDS` | `45` | 模型 HTTP 请求超时秒数，代码限制在 1–120 秒 |
| `GEN_CONCURRENCY` | `3` | 单进程后台生成并发数，最小为 1 |
| `MAX_CALLS_PER_DOC` | `100` | 每份资料的模型调用次数预算 |
| `MAX_CALLS_PER_DAY` | `300` | 每日模型调用次数预算 |
| `UPLOAD_ENABLED` | `true` | 是否开放上传；仍须配置非空 `LLM_API_KEY` 才能启用实时上传 |
| `COOKIE_SECURE` | `false` | 是否只通过 HTTPS 发送会话 Cookie；本地 HTTP 保持 `false`，HTTPS 部署设为 `true` |
| `PORT` | `8000` | 仅供 `scripts/start.sh` 选择监听端口，例如 `PORT=8765 sh scripts/start.sh`；Windows 使用脚本参数 `-Port 8765` |

`UPLOAD_ENABLED` 未设置时，代码兼容旧变量 `ALLOW_UPLOADS`；新配置使用 `UPLOAD_ENABLED`。布尔变量使用小写 `true` 或 `false`，数值变量填写有效数字，预算使用正整数。

实时模式需要支持 Chat Completions、`response_format: {"type":"json_object"}` 与 embeddings 的兼容服务。独立复核和向量服务可各用不同地址与密钥。默认模型名称仅表示代码回退值，不能代表任意供应商均支持。

`.env`、`data/`、本地依赖和缓存已被 Git 忽略。模型密钥仅由后端读取；不要将密钥放入 `VITE_` 变量或提交到仓库。

## 配置检查与工程测试

macOS / Linux：

```bash
.venv/bin/python scripts/check_provider.py
.venv/bin/python -m pytest
npm --prefix frontend test
npm --prefix frontend run build
```

Windows 将 `.venv/bin/python` 替换为 `.\.venv\Scripts\python.exe`。

`check_provider.py` 默认只检查本地配置。需要实际验证供应商时，由操作者填好密钥后显式增加 `--live`；这会请求生成、复核及向量接口，并可能产生费用。报告默认写入项目根目录下的 `data/provider-checks/`，不随 `DATA_DIR` 改变，可用 `--output` 单独指定。工程测试使用模型替身，不代表真实供应商或题目质量已经验收。

## Docker 与数据持久化

安装 Docker Engine / Desktop 以及支持 `env_file.required` 的 Docker Compose 后：

```bash
docker compose up --build -d
docker compose logs -f app
docker compose down
```

仍访问 <http://127.0.0.1:8000>。Compose 将 `DATA_DIR` 设置为 `/app/data`，通过 `study-data` 命名卷持久化数据库、上传文件和向量缓存。普通 `docker compose down` 保留数据卷；删除卷会删除运行数据。

Docker 使用 Node.js 22 和 Python 3.11，安装相同锁文件，以单 worker 运行。旧版 Compose 可先复制模板为 `.env`，再将 Compose 的 `env_file` 改为普通 `env_file: .env`。Docker 配置尚未在本轮实际运行验证。

## 可选材料制作环境

只有需要重建技术 PDF 或演示视频时，才安装 `tools/submission/requirements.txt`。中文字体、Windows 普通话合成语音、Poppler 和 FFmpeg 的具体说明见 [材料工具说明](../tools/submission/README.md)。
