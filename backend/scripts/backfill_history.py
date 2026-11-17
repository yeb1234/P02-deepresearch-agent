"""Backfill research history from existing Redis event streams.

Usage (inside the backend container):
    python -m scripts.backfill_history

The live writer persists every LangGraph node into `research_steps`. Records created
before that change only have an event stream in Redis, so this one-off migration
replays those streams into `research_records` / `research_steps`. It is idempotent:
records that already have steps are left untouched.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from datetime import datetime, timedelta

import redis
from sqlalchemy import delete, select, text

from agent.db.engine import get_session_factory
from agent.db.models import ResearchRecord, ResearchStep
from agent.task_queue import (
    _SKIP_STEP_NODES,
    _STEP_LABELS,
    _format_step_content,
    REDIS_URL,
)

EVENT_PATTERN = "research:events:*"
MESSAGE_PREFIX = re.compile(r"^content=(\S.*?)(?: additional_kwargs=|$)", re.S)


def message_content(message) -> str:
    if isinstance(message, dict):
        value = message.get("content")
        return str(value) if value else ""
    raw = str(message or "")
    match = MESSAGE_PREFIX.match(raw)
    if match:
        literal = match.group(1).strip()
        try:
            value = ast.literal_eval(literal)
            if isinstance(value, str):
                return value
        except Exception:
            return literal.strip("'\"")
    return raw


def last_message_content(messages) -> str:
    for message in reversed(messages or []):
        content = message_content(message)
        if content.strip():
            return content
    return ""


def first_human_content(messages) -> str:
    for message in messages or []:
        content = message_content(message)
        if content.strip():
            return content
    return ""


def read_stream(client: redis.Redis, thread_id: str):
    entries = client.xrange(f"research:events:{thread_id}")
    events = []
    for ts, data in entries:
        try:
            payload = json.loads(data.get("event", ""))
        except Exception:
            continue
        events.append((int(str(ts).split("-")[0]), payload))
    return events


def replay(thread_id: str, events, fallback_plan: str = ""):
    """Turn event stream into (question, plan, answer, status, steps)."""
    question = ""
    plan = fallback_plan
    answer = ""
    status = "paused"
    token_buffers: dict[str, str] = {}
    steps: list[tuple[str, str]] = []
    seen_plan_step = False

    for ts_ms, event in events:
        if "token" in event:
            token = event.get("token") or {}
            node = token.get("node") or ""
            token_buffers[node] = token_buffers.get(node, "") + str(token.get("text", ""))
            continue

        name = next(iter(event.keys()), "")
        value = event.get(name)

        if name == "generate_plan" and isinstance(value, dict):
            messages = value.get("messages") or []
            if not question:
                question = first_human_content(messages)
            candidate = last_message_content(messages)
            if candidate and not plan:
                plan = candidate

        if name == "awaiting_plan_confirmation" and isinstance(value, dict):
            messages = value.get("messages") or []
            if not question:
                question = first_human_content(messages)
            candidate = last_message_content(messages)
            if candidate and not plan:
                plan = candidate

        if name == "research" and isinstance(value, dict):
            if not question:
                question = first_human_content(value.get("messages") or [])
            if not plan and value.get("plan"):
                plan = str(value.get("plan"))

        if name == "write" and isinstance(value, dict) and not question:
            question = first_human_content(value.get("messages") or [])

        if name == "finalize_answer" and isinstance(value, dict):
            candidate = last_message_content(value.get("messages") or [])
            if candidate:
                answer = candidate
            status = "completed"

        if name == "cite_and_polish" and isinstance(value, dict):
            candidate = last_message_content(value.get("messages") or [])
            if candidate:
                answer = candidate

        if name == "error":
            status = "failed"
            if value:
                steps.append(("error", str(value)))

        if name == "task_paused":
            status = "paused"

    if not plan and token_buffers.get("generate_plan"):
        plan = token_buffers["generate_plan"].strip()
        plan = plan.removeprefix("```markdown").removesuffix("```").strip()

    # Rebuild steps in stream order, excluding aggregated/state-only nodes.
    if plan:
        steps.append(("generate_plan", _format_step_content("generate_plan", {"plan": plan})))
    for _ts_ms, event in events:
        if "token" in event:
            continue
        name = next(iter(event.keys()), "")
        value = event.get(name)
        if name in _SKIP_STEP_NODES or name in {"generate_plan", "error"}:
            continue
        if name not in _STEP_LABELS:
            continue
        content = _format_step_content(name, value if isinstance(value, dict) else {"value": value})
        if not content.strip():
            continue
        steps.append((name, content))

    # Drop adjacent duplicates produced by resumed runs.
    deduped: list[tuple[str, str]] = []
    for node, content in steps:
        if deduped and deduped[-1][0] == node and deduped[-1][1] == content:
            continue
        deduped.append((node, content))

    if status == "completed" and not answer:
        status = "failed"
    if not deduped and answer:
        deduped.append(("finalize_answer", answer))
    return question, plan, answer, status, deduped


async def run() -> None:
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    session_factory = get_session_factory()

    async with session_factory() as session:
        threads = (
            await session.execute(
                text(
                    "SELECT user_id, thread_id, title, created_at "
                    "FROM user_threads ORDER BY created_at"
                )
            )
        ).all()

        for user_id, thread_id, title, created_at in threads:
            events = read_stream(client, thread_id)
            if not events:
                print(f"skip {thread_id[:8]}: no events")
                continue

            existing = (
                await session.execute(
                    select(ResearchRecord).where(ResearchRecord.thread_id == thread_id)
                )
            ).scalar_one_or_none()

            if existing and (existing.step_count or 0) > 0:
                print(f"skip {thread_id[:8]}: already has {existing.step_count} steps")
                continue

            existing_plan = existing.plan if existing else ""
            question, plan, answer, status, steps = replay(thread_id, events, existing_plan or "")
            if not question:
                question = title or ""
            if not plan and existing_plan:
                plan = existing_plan

            if existing:
                record = existing
                await session.execute(
                    delete(ResearchStep).where(ResearchStep.thread_id == thread_id)
                )
            else:
                record = ResearchRecord(user_id=int(user_id), thread_id=thread_id)
                session.add(record)

            final_status = status
            if answer and status == "completed":
                final_status = "completed"
            elif status == "failed":
                final_status = "failed"

            last_node, last_content = steps[-1] if steps else ("", "")
            record.title = title or question
            record.question = question
            record.plan = plan
            record.answer = answer or None
            record.status = final_status
            record.step_count = len(steps)
            if final_status == "completed":
                record.stage = "completed"
                record.stage_label = "调研完成"
            elif final_status == "failed":
                record.stage = "error"
                record.stage_label = "执行失败"
            else:
                record.stage = last_node or "generate_plan"
                record.stage_label = _STEP_LABELS.get(record.stage, "等待确认计划")
            record.created_at = created_at or datetime.utcnow()
            record.updated_at = record.created_at + timedelta(seconds=max(len(steps), 1))

            for index, (node, content) in enumerate(steps, start=1):
                session.add(
                    ResearchStep(
                        user_id=int(user_id),
                        thread_id=thread_id,
                        seq=index,
                        node=node,
                        label=(
                            "执行失败"
                            if node == "error"
                            else "生成最终报告"
                            if node == "finalize_answer"
                            else _STEP_LABELS.get(node, node)
                        ),
                        content=content,
                        created_at=record.created_at + timedelta(seconds=index),
                    )
                )

            await session.commit()
            print(
                f"backfilled {thread_id[:8]} status={final_status} "
                f"steps={len(steps)} answer_len={len(answer or '')} title={title}"
            )


if __name__ == "__main__":
    asyncio.run(run())
