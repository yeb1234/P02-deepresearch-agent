# ADR-013: 补齐任务所有权校验与队列 pending 回收

## Status

Proposed

## Context

当前系统已经支持多账号登录，但异步任务的 `task_id` 仍由客户端回传，服务端只校验 UUID 格式，不校验归属：

- `POST /api/research` 接受任意 `task_id`，并把当前 `user_id` 一起入队；
- `GET /api/research/{task_id}/stream` 订阅前不查 `user_threads`；
- `_persist_research_record` 在已有记录上直接覆盖 `user_id`。

这会让知道他人 `task_id` 的登录用户把任务挂到自己历史下，或读取他人的 SSE 事件。`task_id` 是 UUID，但不是授权凭证。

同时，`task_queue.py` 只使用 `XREADGROUP ... ">"`，没有 `XAUTOCLAIM` / `XCLAIM`。进程在任务执行中崩溃时，消息会永久留在 pending；启动时的 `mark_running_tasks_interrupted()` 只把数据库 `running` 改为 `failed`，不接管队列消息。运行时已观察到 3 条 pending，idle 约 6.6–7.9 小时，对应任务已失败。

ADR-008 选择 Redis Streams 作为任务队列时，解决了“少一个中间件、与 SSE 回放天然契合”的问题，但没有定义投递语义的收口方式。本 ADR 补齐这部分。

## Decision

### 1. 任务所有权

- 新任务的 `task_id` 由服务端生成；客户端只能在“续跑/确认计划”场景回传 `task_id`。
- 回传 `task_id` 时，服务端必须校验 `user_threads(user_id, thread_id)` 存在；不存在返回 `404`，不进入入队逻辑。
- SSE 订阅前做同样的归属校验；不存在或不属于当前用户返回 `404`。
- `research_records` 的 `user_id` 在任务生命周期内不可变。写入时如果发现归属不一致，拒绝更新并记录告警。

### 2. 队列回收

- 启动时，在 `mark_running_tasks_interrupted()` 之后执行一次 pending 扫描：用 `XAUTOCLAIM` 按最小 idle（60s）读取消息，确认对应任务已失败后 `XACK`；如果决定重跑，则重新入队并清理旧消息。
- 运行期增加低频回收循环（60s 一次，最小 idle 10min），处理“进程存活但任务卡死”的情况。
- 回收时清理 `research:active:{task_id}` 锁，避免崩溃后锁残留 1 小时。
- consumer 名改为 `worker-{hostname}-{pid}-{shortuuid}`，为后续多副本部署做准备；当前仍保持单 worker。

### 3. SSE 生命周期

- `read_task_events` 返回 `(msg_id, event)`，SSE 输出 `id:` 字段，让浏览器重连时真正携带 `Last-Event-ID`。
- 每 15–30s 输出注释心跳，避免 Nginx `proxy_read_timeout 300s` 断开空闲连接。
- 订阅前检查任务存在性；未知任务在有限时间内结束，不长期占用连接。
- token 事件与生命周期事件分开存储，或给 token 单独设置容量上限，避免长报告把计划/来源事件挤出。

### 4. 部署硬化

以下措施与所有权/回收分开实施，但同属“边界收口”：

- 数据库、Redis、Milvus 端口默认绑定 `127.0.0.1`；
- Compose 凭据从 `.env` 读取，不使用镜像默认口令；Redis 增加密码；
- Cookie 增加 `secure` 开关，HTTPS 环境开启；
- 登录失败按 `IP + username` 限速，5 次/15 分钟；
- 初始化测试账号改为显式开关，默认关闭。

## Consequences

### Positive

- 多用户隔离从“UUID 难猜”变成服务端强制校验，历史与事件不再可越权访问。
- 进程重启后队列不会留下永久 pending，`XLEN` 与真实待处理量一致。
- SSE 重连不再重复回放整个事件流，长任务的时间线更稳定。
- 局域网暴露面收缩，默认部署不再是“数据库裸奔”。

### Negative

- `submit_research` 与 `stream_research` 增加一次数据库查询；当前数据量下可忽略。
- pending 回收需要决定“重跑还是标记失败”。默认选择标记失败 + `XACK`，避免重复消耗 LLM 成本；需要自动恢复时再显式重投。
- consumer 名唯一化后，旧 `worker-1` 的 pending 需要一次性迁移或清理。
- 端口绑定本机后，局域网直连数据库的用法需要改为 SSH 隧道或显式覆盖。

### Neutral

- 不引入新的中间件，不拆微服务。
- 单 worker、单任务并发的容量边界保持不变，直到真实出现多用户并发。

## Alternatives Considered

**继续信任客户端 `task_id`，只靠 UUID 不可猜**

- 拒绝：UUID 会出现在历史详情、日志、截图和分享内容中，不能作为授权凭证；多账号场景下必须服务端校验。

**把任务所有权编码进签名 token**

- 暂不采用：需要额外的签名密钥与过期策略，当前用数据库关联表查询更直接；如果未来把 API 开放给第三方，再考虑签名令牌。

**改用 Celery / RabbitMQ 替代 Redis Streams**

- 拒绝：会引入新的 broker 与运维面，当前问题不是吞吐不足，而是 Redis Streams 的 pending 语义没有收口；换中间件不能自动解决所有权问题。

**每次启动把所有 pending 重投**

- 暂不采用：崩溃任务可能已经执行过半，重投会重复调用 LLM 与搜索，成本不可控。默认标记失败，由用户从历史续跑；只有确认幂等的任务才自动重投。

**为多副本提前拆独立 worker 服务**

- 拒绝：当前是单机单用户场景，拆分属于过度设计；先做 consumer 名唯一化与回收，等出现真实并发再拆。

## Trade-offs

选择“标记失败 + 清理 pending”而不是“自动重跑”，优先保证成本可控和状态可解释，牺牲了一部分自动恢复能力。选择“单 worker + 唯一 consumer 名”而不是“多副本 + 并发消费”，优先保证当前部署简单，牺牲了并发容量。

## References

- [架构审查 2026-11-17](../架构审查-2026-11-17.md)
- [ADR-008 Redis Streams 任务队列](008-redis-streams-task-queue.md)
- `backend/src/agent/app.py`
- `backend/src/agent/task_queue.py`
- `backend/src/agent/auth/routes.py`
- `backend/src/agent/db/init_db.py`
- `docker-compose.yml`
- `frontend/nginx.conf`
