"""DeepResearch多智能体
由三个子智能体组成：
1. 计划阶段（图内，包含人机交互）
2. 研究智能体（子图）— 查询 → 搜索 → 评估循环
3. 写作智能体（子图）— 提纲 → 草稿 → 引用和润色
原有的单体图已重构，每个阶段都成为一个自包含、可独立测试的子图。
"""

from dotenv import load_dotenv
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langchain_core.messages import AIMessage
from IPython.display import Image, display
from loguru import logger

from agent.configuration import Configuration
from agent.prompts import (
    get_current_date,
    plan_instructions,
    plan_reflection_instructions,
    quick_answer_instructions,
)
from agent.post import Post
from agent.state import OverallState
from agent.tools_and_schemas import PlanReflection
from agent.utils import (
    get_last_user_response,
    get_research_topic,
)
from agent.base_agent import Agent, JsonAgent
from agent.sub_agents import research_agent_graph, writer_agent_graph

load_dotenv()

GENERATE_PLAN_NODE = "generate_plan"
RESEARCH_AGENT_NODE = "research"
WRITER_AGENT_NODE = "write"
QUICK_ANSWER_NODE = "quick_answer"



async def generate_plan(state: OverallState, config: RunnableConfig) -> dict:
    """Generate a research plan based on the user's topic.

    Only runs when plan_status is "unconfirmed"; skipped on resubmit.
    """
    if state.get("plan_status", "unconfirmed") != "unconfirmed":
        return {}

    configurable = Configuration.from_runnable_config(config)
    agent = Agent(model_id=configurable.query_generator_model)
    agent.set_step_prompt(plan_instructions)

    # 如果配置中注入了 token 回调，则使用流式调用
    emit_token = config.get("configurable", {}).get("_emit_token")
    if emit_token:

        async def on_token(text: str) -> None:
            await emit_token(text, "generate_plan")

        response = await agent.astream_step(
            on_token,
            current_date=get_current_date(),
            research_topic=get_research_topic(
                state["messages"],
                [m.content for m in state.get("plan_messages", [])],
            ),
            research_proposal=state.get("plan", ""),
        )
    else:
        response = await agent.astep(
            current_date=get_current_date(),
            research_topic=get_research_topic(
                state["messages"],
                [m.content for m in state.get("plan_messages", [])],
            ),
            research_proposal=state.get("plan", ""),
        )
    response = Post.extract_pattern(response, pattern="markdown")
    logger.info(f"[MainGraph] 生成的计划 ({len(response)} 字)")

    return {
        "messages": [AIMessage(content=response)],
        "plan": response,
        "plan_status": "unconfirmed",
        "plan_messages": [AIMessage(content=response)],
    }


async def evaluate_plan(state: OverallState, config: RunnableConfig) -> str:
    """计划生成后的路由。

    返回值：

    "awaiting_plan_confirmation" — 停止，等待人工输入

    "replan" — 重新生成路线规划

    "confirm_plan" — 计划已提交确认，进入评估节点
    """
    if state.get("plan_status", "unconfirmed") == "unconfirmed":
        logger.info("[MainGraph] 等待用户确认计划")
        return "awaiting_plan_confirmation"

    if not state.get("plan"):
        logger.info("[MainGraph] 没有计划可评估 → 重新计划")
        return "replan"

    logger.info("[MainGraph] 计划已提交确认 → 评估")
    return "confirm_plan"


async def quick_answer(state: OverallState, config: RunnableConfig) -> dict:
    """仅搜索模式：基于一轮检索结果直接生成简短问答。

    跳过计划确认、提纲、初稿、审稿和润色，只保留：
    问题 → 查询 → 搜索 → 一轮反思 → 简短回答。
    """
    configurable = Configuration.from_runnable_config(config)
    reasoning_model = state.get("reasoning_model") or configurable.answer_model
    logger.info(f"[MainGraph] 仅搜索回答使用模型={reasoning_model}")

    sources = state.get("sources_gathered", []) or []
    sources_text = "\n".join(
        f"- {source.get('label') or source.get('value')}: "
        f"{source.get('value') or source.get('short_url')}"
        for source in sources[:20]
        if isinstance(source, dict)
    )

    agent = Agent(model_id=reasoning_model)
    agent.set_step_prompt(quick_answer_instructions)
    emit_token = config.get("configurable", {}).get("_emit_token")
    if emit_token:

        async def on_token(text: str) -> None:
            await emit_token(text, QUICK_ANSWER_NODE)

        raw = await agent.astream_step(
            on_token,
            current_date=get_current_date(),
            research_topic=get_research_topic(state["messages"]),
            summaries="\n---\n\n".join(state["web_search_result"]),
            sources=sources_text,
        )
    else:
        raw = await agent.astep(
            current_date=get_current_date(),
            research_topic=get_research_topic(state["messages"]),
            summaries="\n---\n\n".join(state["web_search_result"]),
            sources=sources_text,
        )
    answer = Post.extract_pattern(raw, pattern="markdown")

    # 模型偶尔仍会引用短链接；统一换回真实 URL，避免前端出现不可解析链接。
    cited_sources = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        short_url = source.get("short_url")
        value = source.get("value")
        if short_url and value and short_url in answer:
            answer = answer.replace(short_url, value)
            cited_sources.append(source)
    logger.info(
        f"[MainGraph] 仅搜索回答完成 ({len(answer)} 字, "
        f"{len(cited_sources or sources)} 个来源)"
    )

    return {
        "messages": [AIMessage(content=answer)],
        "sources_gathered": cited_sources or sources,
    }


def route_entry(state: OverallState) -> str:
    """入口路由：仅搜索直接进研究子图，完整调研仍先出计划。"""
    if state.get("research_mode") == "search":
        return RESEARCH_AGENT_NODE
    return GENERATE_PLAN_NODE


def route_after_research(state: OverallState) -> str:
    """研究子图完成后：仅搜索直接回答，完整调研进入写作子图。"""
    if state.get("research_mode") == "search":
        return QUICK_ANSWER_NODE
    return WRITER_AGENT_NODE


async def confirm_plan(state: OverallState, config: RunnableConfig) -> dict:
    """评估计划确认并设置新鲜度等级。

    仅当 plan_status 为 "confirmed"（用户已提交确认）时进入此节点。
    根据用户消息中的关键词或 LLM 评估结果，决定是进入研究阶段还是重新规划。
    """
    configurable = Configuration.from_runnable_config(config)
    context = get_last_user_response(state["messages"])

    if "开始研究" in context or "需求确认" in context:
        logger.info("[MainGraph] plan explicitly confirmed → research")
        return {"fresh_level": "medium"}

    agent = JsonAgent(model_id=configurable.query_generator_model, keys=PlanReflection)
    agent.set_step_prompt(plan_reflection_instructions)
    result = await agent.astep(
        research_proposal=state.get("plan", ""),
        context=context,
    )
    if not isinstance(result, PlanReflection):
        logger.warning(
            f"[MainGraph] 计划评估模型调用失败（返回类型={type(result).__name__}），"
            f"默认进入研究阶段"
        )
        return {"fresh_level": "medium"}
    if result.satisfy:
        logger.info("[MainGraph] plan implicitly confirmed → research")
        return {"fresh_level": getattr(result, "fresh_level", "medium")}

    logger.info("[MainGraph] 计划未确认 → 重新计划")
    return {"plan_status": "unconfirmed"}


def route_after_confirm(state: OverallState) -> str:
    """计划确认后的路由：进入研究阶段或重新规划。"""
    if state.get("plan_status", "confirmed") == "unconfirmed":
        return "replan"
    return RESEARCH_AGENT_NODE

def build_graph(checkpointer=None):
    """编译研究智能体图。

    参数:
        checkpointer: LangGraph checkpointer 实例。langgraph dev 模式下不传，
                      自建服务传入 AsyncRedisSaver 等持久化实现。
    """
    builder = StateGraph(OverallState, config_schema=Configuration)

    builder.add_node(GENERATE_PLAN_NODE, generate_plan)
    builder.add_node("confirm_plan", confirm_plan)
    builder.add_node(QUICK_ANSWER_NODE, quick_answer)
    builder.add_node(
        "replan",
        lambda state, config: {"plan_status": "unconfirmed"},
    )
    builder.add_node(
        "awaiting_plan_confirmation",
        lambda state, config: state,
    )

    # -- 子图节点 --
    builder.add_node(RESEARCH_AGENT_NODE, research_agent_graph)
    builder.add_node(WRITER_AGENT_NODE, writer_agent_graph)

    # 完整调研：先出计划并等待人工确认；仅搜索：直接检索并回答。
    builder.add_conditional_edges(
        START,
        route_entry,
        [GENERATE_PLAN_NODE, RESEARCH_AGENT_NODE],
    )
    builder.add_conditional_edges(
        GENERATE_PLAN_NODE,
        evaluate_plan,
        ["confirm_plan", "replan", "awaiting_plan_confirmation"],
    )
    builder.add_conditional_edges(
        "confirm_plan",
        route_after_confirm,
        [RESEARCH_AGENT_NODE, "replan"],
    )
    builder.add_edge("replan", GENERATE_PLAN_NODE)
    builder.add_conditional_edges(
        RESEARCH_AGENT_NODE,
        route_after_research,
        [WRITER_AGENT_NODE, QUICK_ANSWER_NODE],
    )
    builder.add_edge(WRITER_AGENT_NODE, END)
    builder.add_edge(QUICK_ANSWER_NODE, END)

    return builder.compile(
        name="pro-research-agent",
        checkpointer=checkpointer,
    )


# langgraph dev 入口（不传 checkpointer，由平台自动注入）
graph = build_graph()
