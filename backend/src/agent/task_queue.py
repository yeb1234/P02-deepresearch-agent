"""基于 Redis Streams 的任务队列和事件流。

职责：
  - 任务入队：XADD research:tasks
  - 后台消费：XREADGROUP + 执行 LangGraph 图 + XACK
  - 事件发布：XADD research:events:{task_id}
  - SSE 读取：XREAD research:events:{task_id}

整个模块在 FastAPI 进程内以 asyncio 协程运行，无需独立 Worker 进程。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import redis.asyncio as redis
from langchain_core.messages import HumanMessage, AIMessage
from loguru import logger

from agent.graph import build_graph
from agent.llm.llm import reset_thinking_level, set_thinking_level
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
TASK_STREAM = "research:tasks"
EVENT_STREAM_PREFIX = "research:events"
# 每个 task 的“本次订阅起点”：避免历史续跑时把上一次的 task_paused/finalize 重放给前端
EVENT_CURSOR_PREFIX = "research:event_cursor"
# 同一 task_id 正在执行时的互斥锁，防止重复点击“探索”把同一个任务并发跑多遍
ACTIVE_TASK_PREFIX = "research:active"
CONSUMER_GROUP = "research-workers"
# 单个任务的事件流最大保留条数，防止无限增长
EVENT_STREAM_MAXLEN = 500
# XREAD 阻塞超时（毫秒），避免空轮询
STREAM_BLOCK_MS = 5000

# ── 子图内部节点名 → 前端事件名映射 ──────────────────────────────
# 重构为子图架构后，graph.astream() 需启用 subgraphs=True 才能获取子图内部事件。
# 子图内部节点名与前端约定的扁平事件名不一致，此处做翻译。
_SUBGRAPH_EVENT_MAP = {
    "generate_queries": "generate_query",
    "web_search": "web_research",
    "critique": "reflection",
}

# ── 每个可持久化节点的中文步骤名 ──────────────────────────────────
_STEP_LABELS = {
    "generate_plan": "生成研究计划",
    "confirm_plan": "确认研究计划",
    "generate_query": "生成搜索查询",
    "web_research": "网络检索",
    "reflection": "反思与信息缺口",
    "outline": "生成报告提纲",
    "draft": "撰写/修订初稿",
    "critic_review": "审稿评分",
    "cite_and_polish": "引用与润色",
    "quick_answer": "整理回答",
}
# 这些节点代表子图/阶段边界或仅做状态路由，不单独落步骤
_SKIP_STEP_NODES = {
    "research",
    "write",
    "replan",
    "awaiting_plan_confirmation",
}

_redis: redis.Redis | None = None
_persistent_graph = None  # 延迟初始化，带 Redis checkpoint 持久化


async def _get_redis() -> redis.Redis:
    """延迟获取 Redis 连接（复用同一个连接池）."""
    global _redis
    if _redis is None:
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def _get_graph():
    """获取带 Redis 持久化的编译图（延迟初始化 + 单例）."""
    global _persistent_graph
    if _persistent_graph is None:
        checkpointer = AsyncRedisSaver(
            REDIS_URL,
            ttl={"default_ttl": 60 * 24 * 7, "refresh_on_read": True},
        )
        await checkpointer.asetup()
        _persistent_graph = build_graph(checkpointer=checkpointer)
        logger.info("[TaskQueue] 已创建 AsyncRedisSaver 持久化图（TTL=7天）")
    return _persistent_graph


async def delete_task_artifacts(task_id: str) -> None:
    """删除一个调研线程在 Redis 中的事件流、游标、互斥锁和 checkpoint."""
    r = await _get_redis()
    await r.delete(
        f"{EVENT_STREAM_PREFIX}:{task_id}",
        f"{EVENT_CURSOR_PREFIX}:{task_id}",
        f"{ACTIVE_TASK_PREFIX}:{task_id}",
    )

    graph = await _get_graph()
    checkpointer = getattr(graph, "checkpointer", None)
    if checkpointer is not None and hasattr(checkpointer, "adelete_thread"):
        await checkpointer.adelete_thread(task_id)

    logger.info(f"[TaskQueue] 已清理任务状态 task_id={task_id[:8]}...")


async def mark_running_tasks_interrupted(reason: str = "服务重启中断") -> int:
    """把上次进程遗留的 running 任务标记为 failed，避免历史列表永远显示“进行中”.

    进程重启后，原 worker 已不存在，旧任务不会再有事件推进；
    保留 stage/stage_label，让历史里能直接看到最后停在哪一步。
    """
    from sqlalchemy import select

    from agent.db.engine import get_session_factory
    from agent.db.models import ResearchRecord

    marked = 0
    try:
        async with get_session_factory()() as session:
            rows = (
                await session.execute(
                    select(ResearchRecord).where(ResearchRecord.status == "running")
                )
            ).scalars().all()
            for row in rows:
                base = (row.stage_label or row.stage or "未知阶段").strip()
                if reason not in base:
                    row.stage_label = f"{base}（{reason}）"
                row.status = "failed"
                marked += 1
            await session.commit()
        if marked:
            logger.warning(
                f"[TaskQueue] 启动恢复：{marked} 个遗留 running 任务已标记为中断"
            )
    except Exception as exc:
        logger.warning(
            f"[TaskQueue] 启动恢复失败 ({type(exc).__name__}): {exc}"
        )
    return marked


# ═══════════════════════════════════════════════════════════════════════
# 任务入队
# ═══════════════════════════════════════════════════════════════════════

async def enqueue_task(
    messages: list[dict],
    initial_search_query_count: int,
    max_research_loops: int,
    reasoning_model: str,
    thinking_level: str = "off",
    plan_status: str = "unconfirmed",
    plan: str = "",
    task_id: str = "",
    user_id: int = 0,
    research_mode: str = "report",
) -> str:
    """将研究任务写入 Redis Stream，返回 task_id。

    调用方（POST /api/research）拿到 task_id 后立即返回给前端。
    如果传入 task_id 则复用，否则生成新的 UUID。
    """
    r = await _get_redis()
    if not task_id:
        task_id = str(uuid.uuid4())
    task_payload = json.dumps({
        "task_id": task_id,
        "messages": messages,
        "initial_search_query_count": initial_search_query_count,
        "max_research_loops": max_research_loops,
        "reasoning_model": reasoning_model,
        "thinking_level": thinking_level,
        "plan_status": plan_status,
        "plan": plan,
        "user_id": user_id,
        "research_mode": "search" if research_mode == "search" else "report",
    })

    # 记录本次订阅起点：续跑时跳过上一次已经消费过的 task_paused/finalize 事件。
    # 新任务没有历史事件，游标为 0，前端仍能收到本次生成的计划。
    try:
        stream_key = f"{EVENT_STREAM_PREFIX}:{task_id}"
        last_events = await r.xrevrange(stream_key, count=1)
        cursor = last_events[0][0] if last_events else "0"
        await r.set(
            f"{EVENT_CURSOR_PREFIX}:{task_id}",
            cursor,
            ex=86400,
        )
    except Exception as exc:
        logger.warning(f"[TaskQueue] 设置事件游标失败 task_id={task_id[:8]}...: {exc}")

    await r.xadd(TASK_STREAM, {"task": task_payload}, maxlen=1000)
    logger.info(f"[TaskQueue] 任务已入队 task_id={task_id[:8]}...")
    return task_id


# ═══════════════════════════════════════════════════════════════════════
# 后台消费协程（FastAPI startup 时启动）
# ═══════════════════════════════════════════════════════════════════════

async def start_worker() -> None:
    """启动后台消费协程，不阻塞 HTTP 请求处理。

    调用方式（在 app.py 的 startup 事件中）：
        asyncio.create_task(start_worker())
    """
    r = await _get_redis()
    await _ensure_consumer_group(r)
    logger.info("[TaskQueue] worker 协程已启动，等待任务...")
    while True:
        try:
            await _process_one_task(r)
        except asyncio.CancelledError:
            logger.info("[TaskQueue] worker 协程被取消")
            break
        except Exception as exc:
            logger.error(f"[TaskQueue] worker 异常 ({type(exc).__name__}): {exc}")
            await asyncio.sleep(1)  # 短暂等待后继续


async def _ensure_consumer_group(r: redis.Redis) -> None:
    """创建 Consumer Group（如果不存在）."""
    try:
        await r.xgroup_create(TASK_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
        logger.info(f"[TaskQueue] Consumer Group '{CONSUMER_GROUP}' 已创建")
    except redis.ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.debug(f"[TaskQueue] Consumer Group 已存在，跳过创建")
        else:
            raise


def _clip_text(value, limit: int = 8000) -> str:
    text = "" if value is None else str(value)
    if len(text) > limit:
        return text[:limit] + "\n…（内容过长，已截断）"
    return text


def _extract_ai_content(output: dict) -> str:
    """从节点输出的 messages 中取最后一条 AI 文本."""
    messages = output.get("messages") or []
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
            msg_type = msg.get("type")
        else:
            msg_type = getattr(msg, "type", None)
        if content and (msg_type == "ai" or msg_type is None):
            return _clip_text(content)
    return ""


def _format_step_content(node: str, output: dict) -> str:
    """把节点输出转成历史详情里可读的文本."""
    if not isinstance(output, dict):
        return _clip_text(output)

    if node == "generate_plan":
        return _clip_text(output.get("plan") or "")
    if node == "confirm_plan":
        return f"计划已确认，新鲜度等级：{output.get('fresh_level', 'medium')}"
    if node == "generate_query":
        queries = output.get("search_query") or []
        return _clip_text("\n".join(f"- {q}" for q in queries))
    if node == "web_research":
        sources = output.get("sources_gathered") or []
        summaries = output.get("web_search_result") or []
        parts = [f"检索到 {len(sources)} 条来源"]
        for source in sources[:20]:
            if isinstance(source, dict):
                label = source.get("label") or source.get("value") or ""
                url = source.get("value") or ""
                parts.append(f"- {label}  {url}".strip())
            else:
                parts.append(f"- {source}")
        if summaries:
            parts.append("")
            parts.append("摘要：")
            parts.append("\n---\n".join(str(item) for item in summaries[:3]))
        return _clip_text("\n".join(parts))
    if node == "reflection":
        sufficient = output.get("is_sufficient")
        gap = output.get("knowledge_gap") or ""
        follow = output.get("follow_up_queries") or []
        return _clip_text(
            f"信息是否充足：{'是' if sufficient else '否'}\n"
            f"知识缺口：{gap or '无'}\n"
            f"后续查询：{', '.join(str(q) for q in follow) or '无'}"
        )
    if node == "outline":
        return _clip_text(output.get("report_outline") or "")
    if node == "draft":
        return _clip_text(output.get("report_draft") or "")
    if node == "critic_review":
        feedback = output.get("critic_feedback") or ""
        score = output.get("critic_score")
        head = f"审稿评分：{score}/10" if score is not None else "审稿完成"
        return _clip_text(f"{head}\n\n{feedback}" if feedback else head)
    if node == "cite_and_polish":
        return _extract_ai_content(output)
    if node == "quick_answer":
        return _extract_ai_content(output)
    return _clip_text(json.dumps(output, default=str, ensure_ascii=False))


async def _get_max_step_seq(task_id: str) -> int:
    """取该 thread 已落库的最大步骤序号，保证续跑时继续追加."""
    try:
        from sqlalchemy import func, select

        from agent.db.engine import get_session_factory
        from agent.db.models import ResearchStep

        async with get_session_factory()() as session:
            value = (
                await session.execute(
                    select(func.max(ResearchStep.seq)).where(
                        ResearchStep.thread_id == task_id
                    )
                )
            ).scalar()
        return int(value or 0)
    except Exception as exc:
        logger.warning(f"[TaskQueue] 读取步骤序号失败: {exc}")
        return 0


async def _persist_research_step(
    task: dict,
    node: str,
    output: dict,
    seq: int,
) -> None:
    """每个 LangGraph 节点完成后，追加一条步骤快照."""
    if node in _SKIP_STEP_NODES:
        return

    label = _STEP_LABELS.get(node, node)
    content = _format_step_content(node, output)

    await _persist_research_record(
        task,
        "running",
        plan=output.get("plan") if isinstance(output, dict) else None,
        stage=node,
        stage_label=label,
        step_count=seq,
    )

    try:
        from agent.db.engine import get_session_factory
        from agent.db.models import ResearchStep

        async with get_session_factory()() as session:
            session.add(ResearchStep(
                user_id=int(task.get("user_id") or 0),
                thread_id=task["task_id"],
                seq=seq,
                node=node,
                label=label,
                content=content,
            ))
            await session.commit()
    except Exception as exc:
        logger.warning(
            f"[TaskQueue] 写步骤失败 task_id={task.get('task_id', '')[:8]}... "
            f"node={node} ({type(exc).__name__}): {exc}"
        )


async def _persist_research_record(
    task: dict,
    status: str,
    answer: str | None = None,
    plan: str | None = None,
    stage: str | None = None,
    stage_label: str | None = None,
    step_count: int | None = None,
) -> None:
    """把调研进度写入 research_records，供欢迎页历史列表读取。

    任何数据库异常都只记日志，不能影响研究任务本身。
    """
    user_id = task.get("user_id") or 0
    if not user_id:
        return

    try:
        from sqlalchemy import select

        from agent.db.engine import get_session_factory
        from agent.db.models import ResearchRecord

        question = ""
        for msg in task.get("messages", []):
            if msg.get("type") == "human" and str(msg.get("content", "")).strip():
                question = str(msg["content"]).strip()
                break

        title = " ".join(question.split())[:120]
        plan_text = plan if plan is not None else (task.get("plan") or "")

        async with get_session_factory()() as session:
            row = (
                await session.execute(
                    select(ResearchRecord).where(
                        ResearchRecord.thread_id == task["task_id"]
                    )
                )
            ).scalar_one_or_none()

            if row is None:
                row = ResearchRecord(
                    user_id=user_id,
                    thread_id=task["task_id"],
                    title=title,
                    question=question,
                    plan=plan_text,
                    answer=answer,
                    status=status,
                    stage=stage,
                    stage_label=stage_label,
                    step_count=step_count or 0,
                )
                session.add(row)
            else:
                row.user_id = user_id
                if title:
                    row.title = title
                if question:
                    row.question = question
                if plan_text:
                    row.plan = plan_text
                if answer:
                    row.answer = answer
                if stage:
                    row.stage = stage
                if stage_label:
                    row.stage_label = stage_label
                if step_count is not None:
                    row.step_count = step_count
                row.status = status

            await session.commit()
    except Exception as exc:
        logger.warning(
            f"[TaskQueue] 写历史记录失败 task_id={task.get('task_id', '')[:8]}... "
            f"({type(exc).__name__}): {exc}"
        )


async def _process_one_task(r: redis.Redis) -> None:
    """消费并执行一个任务."""
    # 从 Consumer Group 读取一条待处理任务
    result = await r.xreadgroup(
        CONSUMER_GROUP,
        "worker-1",  # 消费者名称（多实例时每实例一个唯一名）
        {TASK_STREAM: ">"},
        block=STREAM_BLOCK_MS,
        count=1,
    )
    if not result:
        return  # 超时，没有任务

    stream_name, messages = result[0]
    redis_msg_id, payload = messages[0]
    task = json.loads(payload["task"])
    task_id = task["task_id"]

    # 同一个 task_id 只允许一个执行实例；重复入队直接确认丢弃，避免重复落步骤
    active_key = f"{ACTIVE_TASK_PREFIX}:{task_id}"
    acquired = await r.set(active_key, "1", nx=True, ex=3600)
    if not acquired:
        logger.warning(f"[TaskQueue] 任务已在执行，跳过重复入队 task_id={task_id[:8]}...")
        await r.xack(TASK_STREAM, CONSUMER_GROUP, redis_msg_id)
        return

    logger.info(f"[TaskQueue] 开始执行任务 task_id={task_id[:8]}...")

    # 当前任务的思考深度，供 LLM 层读取；任务结束在 finally 中恢复上下文
    thinking_token = set_thinking_level(task.get("thinking_level", "off"))

    try:
        # 重建消息列表（保留完整历史及原始 ID，确保 add_messages 按 ID 正确去重）
        messages = [
            HumanMessage(content=m["content"], id=m.get("id")) if m.get("type") == "human"
            else AIMessage(content=m["content"], id=m.get("id")) if m.get("type") == "ai"
            else None
            for m in task["messages"]
        ]
        messages = [m for m in messages if m is not None]

        # 这个函数将大模型流式输出的token借助Redis也流式输出到前端页面（供节点内 Agent 使用）
        async def emit_token(text: str, node: str) -> None:
            """将 LLM token 推送到 Redis 事件流."""
            try:
                event_json = json.dumps(
                    {"token": {"text": text, "node": node}},
                    ensure_ascii=False,
                )
                await r.xadd(
                    f"{EVENT_STREAM_PREFIX}:{task_id}",
                    {"event": event_json},
                    maxlen=EVENT_STREAM_MAXLEN,
                )
            except Exception as exc:
                logger.warning(f"[TaskQueue] token事件发送失败 node={node}: {exc}")

        config = {
            "configurable": {
                "thread_id": task_id,  # 将 task_id 作为 LangGraph 的 checkpoint thread_id
                "initial_search_query_count": task["initial_search_query_count"],
                "max_research_loops": task["max_research_loops"],
                "reasoning_model": task["reasoning_model"],
                "_emit_token": emit_token,
            }
        }

        # 图初始状态（支持 plan 确认后的 resume 场景）
        initial_state: dict = {
            "messages": messages,
            "plan_status": task.get("plan_status", "unconfirmed"),
            "plan": task.get("plan", ""),
            "initial_search_query_count": task["initial_search_query_count"],
            "max_research_loops": task["max_research_loops"],
            # 关键：把前端选择的模型写进图状态，否则 Writer/Research 会回退到默认模型
            "reasoning_model": task.get("reasoning_model", ""),
            "research_mode": task.get("research_mode", "report"),
        }

        # 执行图（Checkpointer可绑定 Redis，通过 thread_id 实现会话隔离）
        # subgraphs=True: 穿透子图边界，获取内部节点事件
        # 事件格式: (namespace, {node_name: node_output})
        # event = (("research:abc123",), {"generate_queries": {...}})
        #         └──── namespace ────┘  └──────── data ──────────┘
        #   - 父图节点:  namespace = (),     data = {"generate_plan": {...}}
        #   - 子图内部:  namespace = ("research:<id>",), data = {"generate_queries": {...}}
        final_answer_event = None
        generated_plan = task.get("plan") or ""
        step_seq = await _get_max_step_seq(task_id)
        persistent_graph = await _get_graph()
        async for event in persistent_graph.astream(initial_state, config, subgraphs=True):
            # ── 解析 subgraphs=True 的 2 元组格式 ─────────────────────
            #避免处理非节点事件，如：子图进入/退出时的边界事件、LangGraph调度层面的内部生命周期事件
            if not isinstance(event, tuple) or len(event) != 2:
                continue

            namespace: tuple = event[0]
            data: dict = event[1]

            if not isinstance(data, dict) or not data:
                continue

            # 从 data 中提取原始节点名和节点输出
            node_name = list(data.keys())[0]
            node_output = data[node_name]

            # 空输出（如 node 因 plan 已确认而直接 return {}）不产生事件和步骤
            if not node_output:
                continue

            # 跳过 LangGraph 内部事件
            if not node_name or str(node_name).startswith("__"):
                continue

            # ── 事件名翻译：子图内部节点 → 前端扁平事件名 ────────────
            is_subgraph = len(namespace) > 0
            frontend_event_name = (
                _SUBGRAPH_EVENT_MAP.get(node_name, node_name)
                if is_subgraph
                else node_name
            )
            translated_event = {frontend_event_name: node_output}

            # 记录研究计划，暂停时可写入历史
            if frontend_event_name == "generate_plan" and isinstance(node_output, dict):
                generated_plan = node_output.get("plan") or generated_plan

            # 节点完成 → 立刻把这一步写进 Postgres（做到哪步存哪步）
            if frontend_event_name not in _SKIP_STEP_NODES:
                step_seq += 1
                await _persist_research_step(
                    task, frontend_event_name, node_output, step_seq
                )

            # 写入 Redis 事件流（前端根据事件名更新 UI，如气泡的显示，页面文本的合并等等或关闭SSE连接）
            event_json = json.dumps(translated_event, default=str, ensure_ascii=False)
            await r.xadd(
                f"{EVENT_STREAM_PREFIX}:{task_id}",
                {"event": event_json},
                maxlen=EVENT_STREAM_MAXLEN,
            )

            # 如果是 writer 子图返回（包含最终报告）或仅搜索直接回答，提取为 finalize_answer
            if frontend_event_name in {"write", "quick_answer"} and isinstance(node_output, dict):
                if "messages" in node_output:
                    # 子图返回的消息列表可能包含历史消息（由 add_messages reducer 累积），
                    # 取最后一条 AI 消息作为最终报告内容
                    msgs = node_output["messages"]
                    ai_msgs = [m for m in msgs if getattr(m, "type", None) == "ai"]
                    final_msg = ai_msgs[-1] if ai_msgs else (msgs[-1] if msgs else None)
                    if final_msg:
                        final_answer_event = {
                            "finalize_answer": {
                                "messages": [
                                    {"content": final_msg.content, "type": "ai"}
                                ]
                            }
                        }

        logger.info(f"[TaskQueue] 任务完成 task_id={task_id[:8]}...")

        # 判断是正常完成还是暂停在 Plan 确认
        if final_answer_event:
            # 图正常完成 → 发射 finalize_answer
            event_json = json.dumps(final_answer_event, default=str, ensure_ascii=False)
            await r.xadd(
                f"{EVENT_STREAM_PREFIX}:{task_id}",
                {"event": event_json},
                maxlen=EVENT_STREAM_MAXLEN,
            )
            answer_text = ""
            try:
                answer_text = final_answer_event["finalize_answer"]["messages"][0]["content"]
            except (KeyError, IndexError, TypeError):
                answer_text = ""
            await _persist_research_record(
                task,
                "completed",
                answer=answer_text,
                plan=generated_plan,
                stage="completed",
                stage_label="调研完成",
            )
        else:
            # 图执行完毕但未生成最终答案 → 在 Plan 确认处暂停
            pause_json = json.dumps({"task_paused": True}, ensure_ascii=False)
            await r.xadd(
                f"{EVENT_STREAM_PREFIX}:{task_id}",
                {"event": pause_json},
                maxlen=EVENT_STREAM_MAXLEN,
            )
            await _persist_research_record(
                task,
                "paused",
                plan=generated_plan,
                stage="awaiting_plan_confirmation",
                stage_label="等待确认计划",
            )

    except Exception as exc:
        logger.error(f"[TaskQueue] 任务失败 task_id={task_id[:8]}... ({type(exc).__name__}): {exc}")
        # 将错误作为事件推送给前端
        error_json = json.dumps({"error": str(exc)}, ensure_ascii=False)
        await r.xadd(
            f"{EVENT_STREAM_PREFIX}:{task_id}",
            {"event": error_json},
            maxlen=EVENT_STREAM_MAXLEN,
        )
        await _persist_research_record(
            task, "failed", stage="failed", stage_label="执行失败"
        )

    finally:
        # 恢复任务级思考深度上下文，避免影响同一 worker 的后续任务
        reset_thinking_level(thinking_token)
        # 确认消息已处理
        await r.xack(TASK_STREAM, CONSUMER_GROUP, redis_msg_id)
        await r.delete(active_key)
        # 清理事件流（设置过期，24 小时后自动删除）
        await r.expire(f"{EVENT_STREAM_PREFIX}:{task_id}", 86400)


# ═══════════════════════════════════════════════════════════════════════
# SSE 事件读取
# ═══════════════════════════════════════════════════════════════════════

async def read_task_events(task_id: str, last_event_id: str = "0"):
    """Generator: 从 Redis Stream 读取任务事件，用于 SSE 推送。

    支持断线重连：客户端通过 SSE 的 Last-Event-ID header 传入 last_event_id，
    服务端从该 ID 之后开始推送。
    """
    r = await _get_redis()
    stream_key = f"{EVENT_STREAM_PREFIX}:{task_id}"
    current_id = last_event_id

    # 没有显式 Last-Event-ID 时，从入队时记录的游标开始读：
    # 首次任务游标为 0，能收到完整过程；续跑任务跳过上一次的暂停/完成事件。
    if current_id in ("", "0"):
        try:
            cursor = await r.get(f"{EVENT_CURSOR_PREFIX}:{task_id}")
            if cursor:
                current_id = cursor
        except Exception as exc:
            logger.warning(f"[TaskQueue] 读取事件游标失败 task_id={task_id[:8]}...: {exc}")

    while True:
        try:
            result = await r.xread(
                {stream_key: current_id},
                block=STREAM_BLOCK_MS,
                count=10,
            )
            if result:
                for _, messages in result:
                    for msg_id, data in messages:
                        event_str = data.get("event", "{}")
                        current_id = msg_id
                        yield event_str

                        # 检查是否为终止事件（错误 / finalize_answer / task_paused）
                        try:
                            event = json.loads(event_str)
                            if any(k in event for k in ("error", "finalize_answer", "task_paused")):
                                return  # 任务结束，关闭 SSE 连接
                        except json.JSONDecodeError:
                            pass

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(f"[TaskQueue] SSE 读取异常 ({type(exc).__name__}): {exc}")
            await asyncio.sleep(1)
