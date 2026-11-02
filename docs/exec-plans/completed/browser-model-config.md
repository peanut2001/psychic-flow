# 页面配置模型 Key 与地址（浏览器本地保存，随请求携带）

状态：已完成（2026-11-02）
创建：2026-11-02

## 已确认决策

1. 首页「模型设置」填写 API Key、接口地址、文本模型、视觉模型，保存在浏览器 `localStorage`
2. 配置**只在发起请求时随请求头带给后端**，后端不写盘、不写库，也不另存到进程内存
3. 设置弹窗提供「测试连接」：用表单当前值实际调一次模型
4. 清空 `.env` 中的 `OPENAI_API_KEY`；`.env` 其余模型项保留，作为页面未填写模型名时的默认值

## 已知影响（用户已知悉）

- 不由浏览器请求触发的后台任务拿不到页面配置，仍按 `.env`（已无 key）运行：
  - 入池简历身份提取轮询（`identity.py`）会把待提取简历标为 `failed`（现有逻辑无正则兜底）
  - 邮箱捕获附件的扫描页视觉识别失败后降级为本地 tesseract OCR
  - 应用重启时中断的任务仍标记失败，需要在页面点「重试」（重试请求会带上页面配置）

## 后端

- 新模块 `backend/app/model_config.py`：`ModelConfig`（key 不进 repr）、请求级 `ContextVar`、纯 ASGI 中间件读取 `X-Model-*` 请求头、`model_session` 统一取 client
  - 请求内创建的后台任务（`screening_runner.start`、`BackgroundTasks` 解析）复制当前上下文，自动继承请求配置
  - `.env` 路径沿用各组件缓存的 client；请求配置路径每次新建 client 并在用完后关闭
- `evaluator.py` / `documents.py` / `identity.py` 改为读取当前生效配置
- `workbench.create_run_for_resumes` 的模型快照、`/api/health` 的模型状态按当前生效配置
- 新接口 `POST /api/model-config/test`

## 前端

- `api.ts`：读写 `localStorage`，所有 `/api` 请求附带配置头；`testModel`
- `App.tsx`：首页模型状态改为可点击的「模型设置」弹窗（测试连接 / 保存 / 清除）

## 文档

- `AGENTS.md` 安全段、`README.md` 配置说明、`.env.example` 注释

## 追加：模型只能从接口列表中选择（2026-11-02）

- 新接口 `POST /api/model-config/models`：用表单中未保存的 key 与地址读取接口的模型列表（去重、排序），不保存
- 设置弹窗的文本/视觉模型改为下拉框：先「获取模型」才能选择；key 或地址变化后列表作废需重新获取；已保存配置打开弹窗时自动获取
- 视觉模型默认「与文本模型相同」
