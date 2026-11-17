"""对同一批搜索 MCP 结果，比较不同模型的三段适配能力。

用法：
    docker exec zb02-deepresearch-backend-1 python /app/scripts/model_mcp_probe.py

关注点：
1. 查询生成：是否稳定输出 SearchQueryList；
2. 搜索结果摘要：是否保留原始链接、是否幻觉出新链接、是否按 text 围栏输出；
3. 反思评估：是否稳定输出 Reflection。

这个脚本只调用模型和缓存中的 MCP 结果，不产生新的搜索请求。
"""

from __future__ import annotations

import json
import re
import time

from agent.base_agent import Agent, JsonAgent, WebSearchAgent
from agent.llm.llm import reset_thinking_level, set_thinking_level
from agent.post import Post
from agent.prompts import (
    get_current_date,
    query_writer_instructions,
    reflection_instructions,
    web_searcher_instructions,
)
from agent.tools_and_schemas import Reflection, SearchQueryList

MODELS = [
    "qwen-plus-latest",
    "qwen3.8-flash",
    "deepseek-v4.1-flash",
]

TOPIC = "DeepSeek 成立 Harness 团队与产品战略"
SEARCH_QUERY = "DeepSeek 成立 Harness 团队 2026"


def _timed(fn):
    started = time.perf_counter()
    try:
        value = fn()
        return value, time.perf_counter() - started, None
    except Exception as exc:  # noqa: BLE001 - 诊断脚本需要保留原始异常
        return None, time.perf_counter() - started, f"{type(exc).__name__}: {exc}"


def _as_text(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def main() -> None:
    results = WebSearchAgent().step(SEARCH_QUERY, count=8)
    if not results:
        raise SystemExit("MCP 没有返回结果，先用真实查询预热缓存")

    input_urls = {item["url"] for item in results if item.get("url")}
    raw_results = json.dumps(
        [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in results
        ],
        ensure_ascii=False,
        indent=2,
    )
    print(f"\n搜索返回 {len(results)} 条，原始字符数 {len(raw_results)}")

    token = set_thinking_level("off")
    try:
        for model in MODELS:
            print(f"\n===== {model} =====")

            query_agent = JsonAgent(model_id=model, keys=SearchQueryList)
            query_agent.set_step_prompt(query_writer_instructions)
            query_result, query_elapsed, query_error = _timed(
                lambda: query_agent.step(
                    current_date=get_current_date(),
                    research_topic=TOPIC,
                    number_queries=3,
                    research_proposal="重点覆盖团队组建、产品定位、时间线。",
                    known_facts="",
                )
            )
            queries = getattr(query_result, "query", []) if query_result else []
            print(
                f"查询生成: {query_elapsed:.2f}s, "
                f"type={type(query_result).__name__}, "
                f"count={len(queries)}, queries={queries[:5]}, error={query_error}"
            )

            summary_agent = Agent(model_id=model)
            summary_agent.set_step_prompt(web_searcher_instructions)
            summary, summary_elapsed, summary_error = _timed(
                lambda: summary_agent.step(
                    query=SEARCH_QUERY,
                    current_date=get_current_date(),
                    web_search_result=raw_results,
                )
            )
            summary_text = Post.extract_pattern(_as_text(summary), pattern="text")
            out_urls = set(
                re.findall(r"https?://[^\s<>\"'）)\]，,]+", summary_text or "")
            )
            print(
                f"摘要生成: {summary_elapsed:.2f}s, "
                f"chars={len(summary_text or '')}, "
                f"urls={len(out_urls)}, "
                f"urls_not_in_mcp={sorted(out_urls - input_urls)[:5]}, "
                f"error={summary_error}"
            )
            print(f"摘要前 180 字: {(summary_text or '')[:180]!r}")

            critique_agent = JsonAgent(model_id=model, keys=Reflection)
            critique_agent.set_step_prompt(reflection_instructions)
            critique, critique_elapsed, critique_error = _timed(
                lambda: critique_agent.step(
                    current_date=get_current_date(),
                    number_queries=3,
                    research_topic=TOPIC,
                    summaries=summary_text or "",
                    research_proposal="重点覆盖团队组建、产品定位、时间线。",
                )
            )
            print(
                f"反思评估: {critique_elapsed:.2f}s, "
                f"type={type(critique).__name__}, "
                f"sufficient={getattr(critique, 'is_sufficient', None)}, "
                f"follow_up={getattr(critique, 'follow_up_queries', [])[:3]}, "
                f"error={critique_error}"
            )
    finally:
        reset_thinking_level(token)


if __name__ == "__main__":
    main()
