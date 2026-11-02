# Repository Guidelines

## 项目结构与架构

PsychicFlow（仓库 `psychic-flow`，原名 HireFlow）是本地优先的招聘简历筛选工作台。后端位于 `backend/app/`：`main.py` 提供 FastAPI 接口，`documents.py` 解析 PDF/DOCX，`evaluator.py` 调用模型，`model_config.py` 决定每次模型调用的连接配置（页面随请求头携带的配置优先，否则 `.env`），`identity.py` 提取入池简历的结构化身份（模型 JSON + 正则兜底，轮询循环挂在 `main.py` lifespan），`runner.py` 隔离并发任务，`mail_ingest.py` 通过 IMAP 捕获邮箱简历附件进入「简历池」（轮询循环挂在 `main.py` lifespan，不直接进岗位），`schemas.py` 与 `models.py` 分别是 API 数据结构和数据库实体。数据库迁移放在 `backend/alembic/versions/`，后端测试放在 `backend/tests/`。React/Vite 前端位于 `frontend/src/`，静态资源位于 `frontend/public/`。`data/` 和 `.env` 是本地运行数据，不得提交。

`DocumentProcessor`、`Evaluator`、`ScreeningRunner` 是模块边界；不要在 HTTP 路由中复制解析、评分或调度逻辑。评分协议与总分计算以 `evaluator.py` 和 `schemas.py` 为准。评估卡 `candidate` 中的 `gender`/`age` 仅在简历明确写出时提取，只用于简历池档案筛选，不参与任何维度评分。简历池接口 `GET /api/pool/resumes` 支持 `scope=all`（全库简历）并附当前评估摘要与曾评估岗位（`evaluated_jobs`，来自 completed 评估的去重岗位）；年龄、性别、分数等筛选在前端按该响应本地过滤，后端不做。粘贴文本经 `POST /api/pool/resumes/paste` 入池（`source="paste"`，落盘 `.txt`，`parse_status` 直接 `ready`），未确认时按姓名/邮箱/手机号查重；岗位内也可直接粘贴（`POST /api/jobs/{id}/resumes/paste`，查重限本岗位，含 sha256 完全相同判重，成功即启动单份评分任务）；入池简历（邮箱 + 粘贴）`identity_status="pending"`，由后台循环提取 `identity_*` 列并补 `display_name`，岗位直传为 `none` 不提取。

## 构建、测试与开发命令

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'                 # 安装后端及测试依赖
.venv/bin/pytest -q backend/tests       # 运行后端测试
cd frontend && npm install && npm test  # 安装依赖并运行 Vitest
npm run build                           # TypeScript 检查并生成生产包
docker compose up --build               # 启动应用和 PostgreSQL
docker compose down                     # 停止服务，保留数据卷
```

本地前端使用 `npm run dev`；后端可运行 `DATABASE_URL=sqlite:///data/hireflow.db DATA_DIR=data uvicorn app.main:app --reload`。提交前还应执行 `docker compose config`。

发布镜像：推送 `vX.Y.Z` tag 触发 `.github/workflows/docker-publish.yml`，前后端测试通过后推送 `ghcr.io/peanut2001/psychic-flow:X.Y.Z` 与 `:latest`；CI 中后端测试在前端构建之后运行（部分接口测试依赖 `frontend/dist`）。服务器部署只用 `deploy/` 下的 compose 与 `.env.example`，修改应用的环境变量时两处 compose 要同步。

正式运行数据以 docker compose 的 PostgreSQL 数据卷为准；`data/hireflow.db` 仅是本地开发的临时 SQLite，启动时自动建表（`main.py` 中的 `Base.metadata.create_all`），可随时删除重建。

项目已从 HireFlow 更名为 PsychicFlow，但以下标识与已有数据绑定，**刻意沿用 `hireflow`，不要随改名修改**：Compose 项目名（`docker-compose.yml` 顶层 `name`）、数据库名/用户/密码、数据卷 `hireflow-postgres`、浏览器存储键 `hireflow.model-config`、`data/hireflow.db`，以及已发布的 Alembic 迁移文件。

## 编码风格与命名

代码注释、docstring、界面文案和文档使用简体中文。Python 使用 4 空格、类型标注、`snake_case` 函数/模块名和 `PascalCase` 类名；保持 `from __future__ import annotations`。React/TypeScript 使用 2 空格、组件 `PascalCase`、变量与 Hook `camelCase`。当前没有自动格式化或 lint 配置，修改时遵循相邻代码风格，避免无关格式化。

## 测试规范

后端使用 pytest/pytest-asyncio，前端使用 Vitest、Testing Library 和 jsdom。测试文件命名为 `test_*.py` 或 `*.test.tsx`。新行为应先通过公开 API 或用户可见交互添加失败测试，再实现修复；涉及模型评分时使用 fake evaluator，禁止在测试中调用真实模型。修改数据库结构必须同时新增 Alembic 迁移并覆盖升级后的行为。

## 提交与 Pull Request

当前仓库没有可用提交历史可供归纳。提交信息使用简短、祈使式中文，例如 `修复：统一维度评分口径`、`功能：支持选人导出`，一次提交只处理一个主题。PR 必须说明动机、主要改动、验证命令和数据迁移影响；界面变更附前后截图，关联 issue，并明确是否影响模型提示词、评分版本或导出格式。

## 安全与配置

简历属于不可信个人信息：不得写入日志、测试夹具或提交记录，不得向评估模型提供 shell、网络搜索或文件写入工具。邮箱捕获（`mail_ingest.py`）只记录同步计数，不得记录邮件正文或附件内容。密钥只放在 `.env`，或由页面「模型设置」保存在浏览器 localStorage、经 `X-Model-*` 请求头随请求携带；后端对页面密钥不得落盘、入库、写日志或跨请求保留；提交前检查 diff，避免泄露 `OPENAI_API_KEY` 与 `IMAP_PASSWORD`。删除数据卷的 `docker compose down -v` 属于破坏性操作，除非明确要求，否则不要执行。

## 深入文档

- 架构现状见 [ARCHITECTURE.md](./ARCHITECTURE.md)。
- 多步骤工作的执行计划放在 `docs/exec-plans/active/`，完成后移入 `docs/exec-plans/completed/`。

## Harness Engineering — 本地工作纪律

### 知识模型
- 仓库是唯一事实来源（system of record）。
- 从本文件出发，顺着链接深入，不要凭空发明约定。

### 文档纪律
- 文档与代码不一致时，先带证据总结分歧，再询问，不擅自改任何一边。
- 本文件保持简短高信号，优先链接而非长篇叙述。

### 计划
- 多步骤工作 → `docs/exec-plans/active/`；完成 → `docs/exec-plans/completed/`。
- 琐碎的单步改动可以不写计划。

### 调试工作流
遇到错误时：完整读取错误信息（不只看最后一行）→ 定位文件和行号 → 读前后 10-20 行上下文 → 形成假设 → 最小复现验证 → 修复并验证。

### 变更纪律
- 改动保持自洽：代码 + 测试 + 文档 + 验证一起交付。
- 发现未知信息时记录下来，不要猜测。
