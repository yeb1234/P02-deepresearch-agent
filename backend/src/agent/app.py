# mypy: disable - error - code = "no-untyped-def,misc"
import pathlib
import json
import uuid
import asyncio
import traceback
from contextlib import asynccontextmanager
from datetime import timezone
from fastapi import FastAPI, Response, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from agent.logger import setup_logger, log_request_details
from agent.configuration import (
    Configuration,
    get_default_model_id,
    load_available_models_from_env,
    resolve_model_id,
)
from agent.llm.llm import normalize_thinking_level
from agent.task_queue import enqueue_task, start_worker, read_task_events, mark_running_tasks_interrupted
from agent.auth.middleware import AuthMiddleware
from agent.auth.routes import router as auth_router
from agent.base_agent import JsonAgent
from agent.prompts import clarify_instructions, get_current_date
from agent.tools_and_schemas import ClarifyQuestion

# ── 应用 lifespan（管理后台任务启动/关闭）───────────────────────────


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    """应用级 lifespan：管理后台任务的启动和关闭."""
    try:
        await mark_running_tasks_interrupted()
    except Exception as exc:
        logger.warning(
            f"[TaskQueue] 启动恢复执行失败 ({type(exc).__name__}): {exc}"
        )
    try:
        asyncio.create_task(start_worker())
    except Exception as exc:
        logger.warning(f"[TaskQueue] worker 启动失败 ({type(exc).__name__}): {exc}")
    yield
    # 关闭数据库引擎
    try:
        from agent.db.engine import close_engine
        await close_engine()
    except Exception as exc:
        logger.warning(f"[数据库] 关闭引擎时出错 ({type(exc).__name__}): {exc}")


app = FastAPI(docs_url=None, redoc_url=None, lifespan=_app_lifespan)
setup_logger()

# ── 认证中间件（最先添加 = 最外层，拦截非登录请求）─────────────
app.add_middleware(AuthMiddleware)

# ── 注册认证路由 ───────────────────────────────────────────────────
app.include_router(auth_router)

# ── API 路由 ─────────────────────────────────────────────────────────

# 添加获取模型列表的API端点
@app.get("/api/models")
async def get_available_models():
    """获取可用的LLM模型列表"""
    try:
        # 直接从环境变量加载模型列表
        models = load_available_models_from_env()
        default_model_id = get_default_model_id()
        models_data = [
            {
                "model_id": model.model_id,
                "display_name": model.display_name,
                "icon": model.icon,
                "icon_color": model.icon_color,
                "is_default": model.model_id == default_model_id,
            }
            for model in models
        ]
        logger.info(f"返回模型列表: {models_data}")
        return JSONResponse(content={"models": models_data})
    except ValueError as e:
        # 配置解析错误（如 AVAILABLE_MODELS JSON 格式错误）
        logger.error(f"模型配置解析失败 (ValueError): {e}")
        return JSONResponse(
            content={"error": "模型配置格式错误，请检查 AVAILABLE_MODELS 环境变量", "details": str(e)},
            status_code=500
        )
    except Exception as e:
        # 未知异常 — 记录完整 traceback 用于排查
        logger.error(f"获取模型列表失败 ({type(e).__name__}): {e}")
        logger.error(traceback.format_exc())
        return JSONResponse(
            content={"error": "获取模型列表失败", "details": str(e)},
            status_code=500
        )

# 添加请求日志中间件
@app.middleware("http")
async def log_requests(request: Request, call_next):
    try:
        # 记录请求基本信息
        logger.info(f"收到用户请求：{request.method} {request.url}")

        # 如果是POST请求且有body，记录详细信息
        if request.method in ["POST", "PUT", "PATCH"]:
            body = await request.body()
            if body:
                try:
                    body_data = json.loads(body.decode())
                    log_request_details(body_data)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.debug(
                        f"无法解析请求体为JSON ({type(e).__name__}): "
                        f"{body[:200]!r}"
                    )
                    log_request_details(body.decode())
    except Exception as e:
        # 日志记录本身的错误不应影响请求处理
        logger.error(
            f"记录请求日志时出错 ({type(e).__name__}): {e}\n"
            f"{traceback.format_exc()}"
        )

    try:
        response = await call_next(request)
        return response
    except Exception as e:
        logger.error(
            f"处理请求时出错 ({type(e).__name__}): {e}\n"
            f"请求: {request.method} {request.url}\n"
            f"{traceback.format_exc()}"
        )
        raise


# ── 历史记录端点（欢迎页下方历史列表）──────────────────────────────

def _iso_utc(dt):
    """返回前端可直接本地化的时间字符串。

    数据库容器时钟是 UTC，但实际写入的是宿主机的本地墙上时间；asyncpg 读取时
    会按 +08:00 再包一层，前端 new Date() 会再加 8 小时。这里统一转成不带时区的
    墙上时间，避免历史记录时间显示偏移。
    """
    if not dt:
        return None
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


@app.get("/api/history")
async def list_research_history(request: Request):
    """返回当前用户最近的调研记录（按时间倒序，最多 50 条）."""
    user_id = int(getattr(request.state, "user_id", 0) or 0)
    if not user_id:
        return JSONResponse(content={"items": []})

    try:
        from sqlalchemy import select

        from agent.db.engine import get_session_factory
        from agent.db.models import ResearchRecord

        async with get_session_factory()() as session:
            rows = (
                await session.execute(
                    select(ResearchRecord)
                    .where(ResearchRecord.user_id == user_id)
                    .order_by(ResearchRecord.created_at.desc())
                    .limit(50)
                )
            ).scalars().all()

        items = [
            {
                "id": row.id,
                "thread_id": row.thread_id,
                "title": row.title or row.question or "未命名调研",
                "question": row.question or "",
                "status": row.status,
                "has_answer": bool(row.answer),
                "stage": row.stage,
                "stage_label": row.stage_label,
                "step_count": row.step_count or 0,
                "created_at": _iso_utc(row.created_at),
                "updated_at": _iso_utc(row.updated_at),
            }
            for row in rows
        ]
        return JSONResponse(content={"items": items})
    except Exception as exc:
        logger.error(f"[历史记录] 查询失败 ({type(exc).__name__}): {exc}")
        logger.error(traceback.format_exc())
        return JSONResponse(
            content={"error": f"历史记录查询失败: {exc}"},
            status_code=500,
        )


@app.get("/api/history/{thread_id}")
async def get_research_history_detail(thread_id: str, request: Request):
    """返回单条调研记录的完整内容（问题、计划、最终报告）."""
    user_id = int(getattr(request.state, "user_id", 0) or 0)
    if not user_id:
        return JSONResponse(content={"error": "未登录"}, status_code=401)

    try:
        from sqlalchemy import select

        from agent.db.engine import get_session_factory
        from agent.db.models import ResearchRecord, ResearchStep

        async with get_session_factory()() as session:
            row = (
                await session.execute(
                    select(ResearchRecord).where(
                        ResearchRecord.user_id == user_id,
                        ResearchRecord.thread_id == thread_id,
                    )
                )
            ).scalar_one_or_none()

            steps = []
            if row is not None:
                step_rows = (
                    await session.execute(
                        select(ResearchStep)
                        .where(
                            ResearchStep.user_id == user_id,
                            ResearchStep.thread_id == thread_id,
                        )
                        .order_by(ResearchStep.seq.asc())
                    )
                ).scalars().all()
                steps = [
                    {
                        "seq": step.seq,
                        "node": step.node,
                        "label": step.label or step.node,
                        "content": step.content or "",
                        "created_at": _iso_utc(step.created_at),
                    }
                    for step in step_rows
                ]

        if row is None:
            return JSONResponse(content={"error": "记录不存在"}, status_code=404)

        return JSONResponse(content={
            "id": row.id,
            "thread_id": row.thread_id,
            "title": row.title or row.question or "未命名调研",
            "question": row.question or "",
            "plan": row.plan or "",
            "answer": row.answer or "",
            "status": row.status,
            "stage": row.stage,
            "stage_label": row.stage_label,
            "step_count": row.step_count or 0,
            "steps": steps,
            "created_at": _iso_utc(row.created_at),
            "updated_at": _iso_utc(row.updated_at),
        })
    except Exception as exc:
        logger.error(f"[历史记录] 详情查询失败 ({type(exc).__name__}): {exc}")
        logger.error(traceback.format_exc())
        return JSONResponse(
            content={"error": f"历史记录详情查询失败: {exc}"},
            status_code=500,
        )


@app.delete("/api/history/{thread_id}")
async def delete_research_history(thread_id: str, request: Request):
    """删除一条历史调研及其过程快照、用户关联和 Redis 状态."""
    user_id = int(getattr(request.state, "user_id", 0) or 0)
    if not user_id:
        return JSONResponse(content={"error": "未登录"}, status_code=401)

    try:
        from sqlalchemy import delete, select

        from agent.db.engine import get_session_factory
        from agent.db.models import ResearchRecord, ResearchStep, UserThread

        async with get_session_factory()() as session:
            row = (
                await session.execute(
                    select(ResearchRecord).where(
                        ResearchRecord.user_id == user_id,
                        ResearchRecord.thread_id == thread_id,
                    )
                )
            ).scalar_one_or_none()

            if row is None:
                return JSONResponse(content={"error": "记录不存在"}, status_code=404)
            if row.status == "running":
                return JSONResponse(
                    content={"error": "这次调研仍在进行中，暂不能删除"},
                    status_code=409,
                )

            await session.execute(
                delete(ResearchStep).where(
                    ResearchStep.user_id == user_id,
                    ResearchStep.thread_id == thread_id,
                )
            )
            await session.execute(
                delete(UserThread).where(
                    UserThread.user_id == user_id,
                    UserThread.thread_id == thread_id,
                )
            )
            await session.execute(
                delete(ResearchRecord).where(
                    ResearchRecord.user_id == user_id,
                    ResearchRecord.thread_id == thread_id,
                )
            )
            await session.commit()

        # 尽力清理 Redis 事件流、游标、互斥锁和 LangGraph checkpoint。
        # 即使失败也不影响数据库里的历史已经被删除。
        try:
            from agent.task_queue import delete_task_artifacts
            await delete_task_artifacts(thread_id)
        except Exception as exc:
            logger.warning(
                f"[历史记录] 删除 Redis 状态失败 thread_id={thread_id[:8]}...: "
                f"{type(exc).__name__}: {exc}"
            )

        logger.info(f"[历史记录] 已删除 thread_id={thread_id[:8]}...")
        return JSONResponse(content={"ok": True, "thread_id": thread_id})
    except Exception as exc:
        logger.error(f"[历史记录] 删除失败 ({type(exc).__name__}): {exc}")
        logger.error(traceback.format_exc())
        return JSONResponse(
            content={"error": f"历史记录删除失败: {exc}"},
            status_code=500,
        )


# ── 异步研究端点（任务队列 + SSE）────────────────────────────────────

def _clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    """把客户端传入的数字收敛到安全区间，避免异常参数放大检索轮次和成本。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


@app.post("/api/clarify")
async def clarify_requirement(request: Request):
    """一次返回一个关键澄清问题，供前端逐题点选。

    请求体：
    {
        "topic": "用户原始课题",
        "answers": [{"question": "...", "answer": "..."}]
    }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"error": "请求体必须是 JSON 格式"}, status_code=400)

    topic = str(body.get("topic") or "").strip()
    if not topic:
        return JSONResponse(content={"error": "topic 不能为空"}, status_code=400)
    if len(topic) > 2000:
        topic = topic[:2000]

    answers = body.get("answers")
    if not isinstance(answers, list):
        answers = []
    answers = [
        {
            "question": str(item.get("question") or "").strip()[:200],
            "answer": str(item.get("answer") or "").strip()[:500],
        }
        for item in answers
        if isinstance(item, dict)
    ]

    # 最多 3 轮，防止澄清阶段无限延长
    if len(answers) >= 3:
        return JSONResponse(
            content={"done": True, "reason": "已达到最多 3 轮澄清", "progress": 100}
        )

    answers_text = "（暂无）" if not answers else "\n".join(
        f"{index + 1}. 问：{item['question']}\n   答：{item['answer']}"
        for index, item in enumerate(answers)
    )

    try:
        configurable = Configuration.from_runnable_config(None)
        agent = JsonAgent(
            model_id=configurable.query_generator_model,
            keys=ClarifyQuestion,
        )
        agent.set_step_prompt(clarify_instructions)
        result = await agent.astep(
            current_date=get_current_date(),
            research_topic=topic,
            answers=answers_text,
            round_index=str(len(answers) + 1),
        )
        if not isinstance(result, ClarifyQuestion):
            raise ValueError(f"澄清结果类型异常: {type(result).__name__}")
    except Exception as exc:
        logger.warning(f"[Clarify] 生成澄清问题失败 ({type(exc).__name__}): {exc}")
        return JSONResponse(
            content={
                "done": True,
                "reason": "澄清服务暂不可用，按原问题开始研究",
                "progress": 100,
            }
        )

    options = [
        {
            "label": option.label[:40],
            "description": option.description[:120],
            "recommended": bool(option.recommended),
        }
        for option in result.options
    ][:4]
    done = bool(result.done) or not str(result.question or "").strip() or not options

    return JSONResponse(
        content={
            "done": done,
            "question": "" if done else str(result.question).strip(),
            "reason": str(result.reason or "").strip()[:200],
            "options": [] if done else options,
            "allow_other": bool(result.allow_other) if not done else False,
            "progress": 100 if done else max(0, min(100, int(result.progress or (len(answers) + 1) * 30))),
            "round": len(answers) + 1,
        }
    )


@app.post("/api/research")
async def submit_research(request: Request):
    """提交研究任务，立即返回 task_id 和 SSE 流地址.

    请求体示例：
    {
        "messages": [{"type": "human", "content": "分析AI芯片市场趋势"}],
        "initial_search_query_count": 3,
        "max_research_loops": 3,
        "reasoning_model": "qwen-plus-latest",
        "thinking_level": "off"
    }
    """
    user_id: int = getattr(request.state, "user_id", 0)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            content={"error": "请求体必须是 JSON 格式"},
            status_code=400,
        )

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return JSONResponse(
            content={"error": "messages 不能为空"},
            status_code=400,
        )
    if not any(
        isinstance(item, dict)
        and item.get("type") == "human"
        and str(item.get("content", "")).strip()
        for item in messages
    ):
        return JSONResponse(
            content={"error": "messages 中至少需要一条非空 human 消息"},
            status_code=400,
        )

    task_id = str(body.get("task_id") or "").strip()
    if task_id:
        try:
            task_id = str(uuid.UUID(task_id))
        except (ValueError, TypeError, AttributeError):
            return JSONResponse(
                content={"error": "task_id 格式不正确"},
                status_code=400,
            )

    plan = body.get("plan", "")
    plan = plan if isinstance(plan, str) else ""
    plan_status = body.get("plan_status", "unconfirmed")
    if plan_status not in {"unconfirmed", "confirmed"}:
        plan_status = "unconfirmed"
    # 没有计划内容时不允许直接跳过人工确认
    if plan_status == "confirmed" and not plan.strip():
        plan_status = "unconfirmed"

    research_mode = str(body.get("research_mode") or "report").strip().lower()
    if research_mode not in {"search", "report"}:
        research_mode = "report"
    # 仅搜索模式跳过计划确认，不需要计划内容
    if research_mode == "search":
        plan_status = "unconfirmed"

    initial_search_query_count = _clamp_int(
        body.get("initial_search_query_count"), 2, 1, 5
    )
    max_research_loops = _clamp_int(body.get("max_research_loops"), 2, 1, 10)
    if research_mode == "search":
        # 服务端强制收敛到一轮检索，避免调用方把“仅搜索”当完整调研跑。
        initial_search_query_count = min(initial_search_query_count, 3)
        max_research_loops = 1

    try:
        task_id = await enqueue_task(
            messages=messages,
            initial_search_query_count=initial_search_query_count,
            max_research_loops=max_research_loops,
            reasoning_model=resolve_model_id(body.get("reasoning_model")),
            thinking_level=normalize_thinking_level(body.get("thinking_level")),
            plan_status=plan_status,
            plan=plan,
            task_id=task_id,
            user_id=user_id,
            research_mode=research_mode,
        )
    except Exception as exc:
        logger.error(f"[TaskQueue] 任务入队失败 ({type(exc).__name__}): {exc}")
        return JSONResponse(
            content={"error": f"任务提交失败: {exc}"},
            status_code=503,
        )

    # 建立用户与 task（thread）的关联
    try:
        from agent.db.engine import get_session_factory
        from agent.db.models import UserThread
        from sqlalchemy import select

        # 从用户输入中提取主题摘要作为 title（取第一条 human 消息 = 用户原始问题）
        messages = body.get("messages", [])
        title_text = str(body.get("title") or "").strip()[:256]
        if not title_text:
            for m in messages:
                if m.get("type") == "human" and m.get("content", "").strip():
                    title_text = m["content"].strip()[:256]
                    break

        async with get_session_factory()() as session:
            # 检查是否已存在（同一事务内先查后插）
            existing = await session.execute(
                select(UserThread).where(
                    UserThread.user_id == user_id,
                    UserThread.thread_id == task_id,
                )
            )
            if existing.scalar_one_or_none() is None:
                session.add(UserThread(
                    user_id=user_id,
                    thread_id=task_id,
                    title=title_text,
                ))
                await session.commit()
    except Exception as exc:
        logger.warning(f"[用户关联] 写入 user_threads 失败 ({type(exc).__name__}): {exc}")
        # 不影响任务提交的响应，关联可以后续补录

    return JSONResponse(content={
        "task_id": task_id,
        "stream_url": f"/api/research/{task_id}/stream",
    })


@app.get("/api/research/{task_id}/stream")
async def stream_research(task_id: str, request: Request):
    """SSE 端点：推送研究进度事件。

    客户端断开后可用 Last-Event-ID header 重连，不会丢失中间事件。
    """
    last_event_id = request.headers.get("Last-Event-ID", "0")

    async def event_generator():
        try:
            async for event_str in read_task_events(task_id, last_event_id):
                yield f"data: {event_str}\n\n"
        except Exception as exc:
            logger.warning(f"[TaskQueue] SSE 推送异常 ({type(exc).__name__}): {exc}")
            yield f"data: {{\"error\": \"{exc}\"}}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        },
    )
