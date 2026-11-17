# ZB02 DeepResearch Docker 运行说明

## 访问地址

- 前端：http://localhost:12309/app/ （根路径 `/` 会自动跳转）
- 后端：http://localhost:12409 （`/api/models`、`/api/research`、SSE）
- Milvus：http://localhost:19509 （仅 kb profile）

登录账号（首次启动自动建表写入）：

| 用户名 | 密码 |
| --- | --- |
| zhangsan | zhangsan |
| lisi | lisi |

## 启动 / 停止

```powershell
cd "C:\FinalProjectYeb\ZB02 Deepresearch"

# 启动核心四件套（backend + frontend + postgres + redis）
docker compose up -d --build

# 连同知识库 Milvus 一起启动
docker compose --profile kb up -d --build

# 停止（保留数据卷）
docker compose down

# 停止并清空数据（包括 PG / Redis / Milvus 数据）
docker compose down -v
```

改 `.env` 后必须重建容器才会生效：

```powershell
docker compose up -d --force-recreate backend
```

## 端口分配

| 服务 | 宿主机 | 容器 |
| --- | --- | --- |
| frontend | 12309 | 80 |
| backend | 12409 | 2024 |
| postgres | 12509 | 5432 |
| redis (stack) | 12609 | 6379 |
| milvus | 19509 | 19530 |

## 配置（backend/.env）

| 变量 | 说明 |
| --- | --- |
| `APP_TOKEN` | LLM Key（OpenAI 兼容） |
| `LLM_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `LLM_REASONING_EFFORT` | 当前端点要求 `enable_thinking=false` 时 `reasoning_effort` 不能是 low；设为 `off` 只发 `enable_thinking=false` |
| `DASHSCOPE_API_KEY` / `MCP_APP_ID` | Web 搜索走百炼应用（`Application.call`） |
| `EMBEDDING_*` | `text-embedding-v3`，1024 维，走同一个兼容端点 |
| `DATABASE_URL` / `REDIS_URL` / `MILVUS_URI` | 使用 compose 服务名 `postgres` / `redis` / `milvus` |
| `AVAILABLE_MODELS` | 前端下拉的模型列表，JSON 数组 |

## 已验证链路

1. 登录 → Cookie 会话 → `/api/whoami`；勾选“记住我”后 Cookie 与 Redis session 延长到 30 天，用户名保存在浏览器本地用于下次回填
2. `POST /api/research` 入队（Redis Streams）→ `GET /api/research/{task_id}/stream` SSE
3. 计划生成（逐 token 流式）→ `task_paused` 等人工确认
4. 确认后：查询生成 → 百炼 MCP 搜索 → 反思 → 提纲 → 初稿 → 评审 → 修订 → 引用润色 → `finalize_answer`
5. KB：Milvus 自动建集合 `research_facts`，事实写入与检索均已跑通（第二轮检索命中 2 条）

## 历史记录与状态持久化

研究历史以 **PostgreSQL 为唯一事实源**，Redis 只负责运行时状态：

| 存储 | 内容 | 生命周期 |
|---|---|---|
| PostgreSQL `research_records` | 每条研究的题目、问题、计划、最终报告、状态、当前阶段、步骤数 | 永久（`pgdata` 卷） |
| PostgreSQL `research_steps` | 每个 LangGraph 节点完成后追加一条快照（计划、查询、检索、反思、提纲、初稿、审稿、润色） | 永久（`pgdata` 卷） |
| Redis checkpoint | LangGraph 完整执行状态，用于暂停后继续跑 | 7 天（`refresh_on_read`） |
| Redis Stream | 任务队列与 SSE 事件流 | 任务事件保留 24 小时 |

写入时机：`task_queue._process_one_task` 每收到一个节点完成事件，就调用 `_persist_research_step` 写一条 `research_steps`，同时更新 `research_records` 的 `stage` / `stage_label` / `step_count`。所以任务跑到哪一步，库里就存到哪一步；即使 Redis 过期，历史页仍能看到每一步产出，只是不能再从该 checkpoint 续跑。

相关接口：

- `GET /api/history`：当前用户最近 50 条研究（标题、状态、当前阶段、步骤数）
- `GET /api/history/{thread_id}`：单条详情，含 `steps` 数组（每步节点名、中文名、产出内容）
- `DELETE /api/history/{thread_id}`：删除当前用户的一条研究；同一事务删除 `research_steps`、`user_threads`、`research_records`，随后清理 Redis 事件流、事件游标、任务锁与 LangGraph checkpoint。`running` 状态返回 409，避免删除仍在执行的任务。

前端入口：欢迎页下方“历史研究”列表；点击条目进入只读详情，可逐条展开每一步产出。已完成记录在顶部展示最终报告，未完成记录展示已完成步骤和当时的计划。列表项和详情页都提供删除入口，第一次点击进入页内确认状态，第二次点击才真正删除；进行中的研究删除按钮禁用。

实时状态：研究进行中，页面底部持续显示当前阶段、已运行时长和最近一次事件距今时间；超过 90 秒没有新事件会提示可能卡住的阶段，超过 5 分钟建议停止后从历史续跑。历史详情对 `running` 记录每 3 秒刷新，能看到阶段和步骤继续推进。

服务重启恢复：后端启动时会把上一进程遗留的 `running` 任务标记为 `failed`，并在 `stage_label` 后追加“（服务重启中断）”。历史里因此不会长期显示虚假的“进行中”，而是显示最后停在哪个阶段；步骤和已有报告不会被删除。若要清理这类中断记录，可在历史详情页删除（此时状态已不是 `running`）。

备份数据库：

```powershell
docker exec zb02-deepresearch-postgres-1 pg_dump -U postgres -d deepresearch > deepresearch_backup.sql
```

只要不执行 `docker compose down -v`，`pgdata` 卷会保留全部历史。

旧任务回填：`backend/scripts/backfill_history.py` 会把 Redis 里已有的事件流重放成历史记录和步骤，幂等执行：

```powershell
docker exec -w /app zb02-deepresearch-backend-1 python -m scripts.backfill_history
```

## 已知限制

- 百炼「应用」返回的是成文分析而不是结构化搜索结果，解析器只能从文本里抽链接；单次查询通常只能拿到 0–2 条来源，报告引用会偏少。要更多真实结果需要换成真正的搜索 API。
- 后端入口是 `uvicorn agent.app:app`（自定义 FastAPI），不是 `langgraph dev`，所以 `/threads`、`/runs`、`/assistants` 这些 LangGraph 原生端点不存在；前端主链路走自建 `/api/research` + SSE，不受影响。
- `MAX_RESEARCH_LOOPS` / `NUMBER_OF_INITIAL_QUERIES` 以 `.env` 为准，请求体里的同名字段不总能覆盖。
- 前端 `BrowserRouter` 没设 `basename`，`/login` 已由 nginx 单独兜底；如果以后改前端路由，记得同步 nginx。
- 当前是单机、单进程 worker，同一时刻只执行 1 个研究任务；多人同时提交会排队。容量边界见 [架构审查](docs/架构审查-2026-11-17.md)。
- 队列 pending 尚未自动回收：进程在任务执行中崩溃会留下未确认消息，启动恢复只改数据库状态，不接管 pending。
- 任务所有权尚未校验：携带他人 `task_id` 可以重新入队或订阅 SSE；修复方案见 [ADR-013](docs/架构决策ADR/013-task-ownership-and-queue-reclaim.md)。
- PostgreSQL、Redis、Milvus 端口默认发布到 `0.0.0.0`，并使用镜像默认凭据。只应在可信本机环境使用；接入共享网络前按架构审查 AR-05 改绑本机并替换凭据。
