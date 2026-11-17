import os
import re
import copy
import random
import traceback
import time
import threading
import asyncio
from typing import Callable, Awaitable
from loguru import logger
from agent.llm.llm import OpenAICompatibleLLM
from dashscope import Application
from agent.post import Post
import json
from agent.search_cache import get_cached, set_cached
from agent.search_normalizer import normalize_search_results, normalize_stats
from agent.exceptions import (
    TransientError,
    PermanentError,
    MCPRateLimitError,
    MCPAuthError,
    MCPAccessDeniedError,
    MCPParseError,
    MCPEmptyResultError,
    MCPServerError,
)


class RateLimiter:
    """
    简单的令牌桶速率限制器
    用于控制API请求频率，避免触发429错误
    """
    def __init__(self, max_qps: float = 15.0):
        """
        初始化速率限制器

        Args:
            max_qps: 最大每秒请求数，默认15 QPS
        """
        self.max_qps = max_qps
        self.min_interval = 1.0 / max_qps  # 最小请求间隔（秒）
        self.last_request_time = 0
        self.lock = threading.Lock()
        self._alock = asyncio.Lock()  # async variant
        logger.info(f"速率限制器已初始化: 最大QPS={max_qps}, 最小间隔={self.min_interval:.3f}秒")

    def acquire(self):
        """
        获取请求许可，如果频率超限则等待

        Returns:
            float: 实际等待的时间（秒）
        """
        with self.lock:
            current_time = time.time()
            time_since_last = current_time - self.last_request_time

            if time_since_last < self.min_interval:
                wait_time = self.min_interval - time_since_last
                logger.debug(f"速率限制：需要等待 {wait_time:.3f} 秒")
                time.sleep(wait_time)
                self.last_request_time = time.time()
                return wait_time
            else:
                self.last_request_time = current_time
                return 0.0

    async def aacquire(self):
        """异步获取请求许可——不阻塞事件循环."""
        async with self._alock:
            current_time = time.time()
            time_since_last = current_time - self.last_request_time

            if time_since_last < self.min_interval:
                wait_time = self.min_interval - time_since_last
                logger.debug(f"速率限制(异步)：需要等待 {wait_time:.3f} 秒")
                await asyncio.sleep(wait_time)
                self.last_request_time = time.time()
                return wait_time
            else:
                self.last_request_time = current_time
                return 0.0


# 全局速率限制器实例（单例模式）
_web_search_rate_limiter = None

def get_web_search_rate_limiter(max_qps: float = None) -> RateLimiter:
    """
    获取全局Web搜索速率限制器实例

    Args:
        max_qps: 最大QPS，如果为None则从环境变量读取或使用默认值

    Returns:
        RateLimiter: 速率限制器实例
    """
    global _web_search_rate_limiter

    if _web_search_rate_limiter is None:
        if max_qps is None:
            # 从环境变量读取，默认为12 QPS（留有余量）
            max_qps = float(os.getenv("WEB_SEARCH_MAX_QPS", "12"))
        _web_search_rate_limiter = RateLimiter(max_qps=max_qps)

    return _web_search_rate_limiter


# ── 重试辅助函数 ──────────────────────────────────────────────────────────

def _backoff_delay(attempt: int, base: float = 1.5, cap: float = 30.0) -> float:
    """指数退避 + 抖动：base * 2^attempt，上限 cap，附加 0~30% 随机抖动.

    429/5xx 这类集群级限流往往需要更长的冷却时间，线性增长不够用。
    """
    delay = min(cap, base * (2 ** attempt))
    return delay + random.uniform(0, delay * 0.3)


def _retry_with_classified_errors(
    callable_fn,
    max_attempts: int = 3,
    base_delay: float = 1.5,
    error_prefix: str = "调用",
):
    """对可恢复错误重试，对不可恢复错误快速失败.

    Args:
        callable_fn: 无参数的可调用对象
        max_attempts: 最大尝试次数
        base_delay: 每次重试的基础等待时间（指数增长）
        error_prefix: 日志中的错误描述前缀

    Returns:
        callable_fn 的返回值，重试耗尽时返回 None

    Raises:
        PermanentError: 遇到不可恢复错误时直接抛出
    """

    for attempt in range(max_attempts):
        try:
            return callable_fn()
        except PermanentError:
            # 不可恢复错误 — 不重试，直接向上传播
            raise
        except TransientError as e:
            if attempt < max_attempts - 1:
                delay = _backoff_delay(attempt, base_delay)
                logger.warning(
                    f"{error_prefix}瞬时错误（尝试 {attempt + 1}/{max_attempts}），"
                    f"{delay:.1f}s 后重试：{e}"
                )
                time.sleep(delay)
            else:
                logger.error(
                    f"{error_prefix}重试{max_attempts}次全部失败：{e}"
                )
        except Exception as e:
            # 未知异常 — 保守处理：记录详细日志后重试
            if attempt < max_attempts - 1:
                delay = _backoff_delay(attempt, base_delay)
                logger.warning(
                    f"{error_prefix}未知错误（尝试 {attempt + 1}/{max_attempts}），"
                    f"{delay:.1f}s 后重试：{e}\n{traceback.format_exc()}"
                )
                time.sleep(delay)
            else:
                logger.error(
                    f"{error_prefix}重试{max_attempts}次全部失败（未知错误）：{e}\n"
                    f"{traceback.format_exc()}"
                )

    # 所有重试耗尽 — 返回 None 而非抛异常，保持向后兼容
    return None


class Agent:
    step_prompt = """{prompt}"""
    def __init__(self, model_id="deepseek-v4-flash"):
        self.llm = OpenAICompatibleLLM(model_id=model_id)

    def __call__(self, prompt):
        response = self.llm.generate_response(prompt)
        return response

    async def acall(self, prompt):
        """异步调用 LLM."""
        return await self.llm.agenerate_response(prompt)

    def set_step_prompt(self, prompt):
        self.step_prompt = prompt

    def step(self, **kwargs):
        step_prompt = self.prompt_format(self.step_prompt, **kwargs)

        def _attempt():
            response = self(step_prompt)
            return self.post_process(response)

        return _retry_with_classified_errors(
            _attempt,
            error_prefix="大模型调用",
        ) or ""

    async def astep(self, **kwargs):
        """异步执行 Agent step——不阻塞事件循环."""
        step_prompt = self.prompt_format(self.step_prompt, **kwargs)
        for attempt in range(3):
            try:
                response = await self.acall(step_prompt)
                response = self.post_process(response)
                return response
            except PermanentError as e:
                logger.error(
                    f"大模型调用永久错误，放弃重试：{e}\n"
                    f"错误类型: {type(e).__name__}"
                )
                raise
            except TransientError as e:
                if attempt < 2:
                    delay = _backoff_delay(attempt, 1.5)
                    logger.warning(
                        f"大模型瞬时错误（尝试 {attempt + 1}/3），"
                        f"{delay:.1f}s 后重试：{e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"大模型调用重试3次全部失败：{e}\n"
                        f"错误类型: {type(e).__name__}"
                    )
            except Exception as e:
                if attempt < 2:
                    delay = _backoff_delay(attempt, 1.5)
                    logger.warning(
                        f"大模型调用未知错误（尝试 {attempt + 1}/3），"
                        f"{delay:.1f}s 后重试：{e}\n{traceback.format_exc()}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"大模型调用重试3次全部失败（未知错误）：{e}\n"
                        f"错误类型: {type(e).__name__}\n"
                        f"{traceback.format_exc()}"
                    )
        return ""

    async def astream_step(self, on_token: Callable[[str], Awaitable[None]], **kwargs):
        """异步流式执行 Agent step——逐 token 回调。

        先尝试 3 次流式调用；全部失败则回退到非流式 astep()，
        并将完整结果作为单次回调传递给 on_token。

        Args:
            on_token: 每收到一个 token 时调用的异步回调
            **kwargs: 转发给 prompt_format
        """
        step_prompt = self.prompt_format(self.step_prompt, **kwargs)

        # ── 流式重试 ────────────────────────────────────────────
        for attempt in range(3):
            try:
                full_response = ""
                async for token in self.llm.astream_response(step_prompt):
                    full_response += token
                    try:
                        await on_token(token)
                    except Exception:
                        pass  # 回调失败不能中断流式调用
                response = self.post_process(full_response)
                return response
            except PermanentError:
                raise
            except TransientError as e:
                if attempt < 2:
                    delay = _backoff_delay(attempt, 1.5)
                    logger.warning(
                        f"流式大模型调用瞬时错误（尝试 {attempt + 1}/3），"
                        f"{delay:.1f}s 后重试：{e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"流式大模型调用重试3次全部失败：{e}")
            except Exception as e:
                if attempt < 2:
                    delay = _backoff_delay(attempt, 1.5)
                    logger.warning(
                        f"流式大模型调用未知错误（尝试 {attempt + 1}/3），"
                        f"{delay:.1f}s 后重试：{e}\n{traceback.format_exc()}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"流式大模型调用重试3次全部失败（未知错误）：{e}\n"
                        f"{traceback.format_exc()}"
                    )

        # ── 回退：非流式调用 ─────────────────────────────────────
        logger.warning("流式大模型调用全部失败，回退到非流式调用")
        response = await self.astep(**kwargs)
        try:
            await on_token(response)
        except Exception:
            pass
        return response

    def post_process(self, response):
        return response

    def prompt_format(self, prompt, **kwargs):
        prompt_ = copy.deepcopy(prompt)
        for k in kwargs.keys():
            rep = "{"+k+"}"
            prompt_ = prompt_.replace(rep, str(kwargs[k]))
        return prompt_


class JsonAgent(Agent):
    def __init__(self, model_id="deepseek-v4-flash", keys=None):
        super().__init__(model_id)
        self.keys = keys

    def post_process(self, response):
        result = json.loads(Post.extract_pattern(response, pattern="json"))
        if not self.keys:
            return result
        return self.keys(**result)


class MCPAgent(Agent):

    def step(self, **kwargs):
        try:
            step_prompt = self.step_prompt.format(**kwargs)
        except (KeyError, ValueError) as e:
            logger.warning(f"MCP prompt格式化失败（缺少参数 {e}），使用原始prompt")
            step_prompt = self.step_prompt

        def _attempt():
            response = Application.call(
                api_key=os.getenv("DASHSCOPE_API_KEY") or os.getenv("APP_TOKEN"),
                app_id=os.getenv("MCP_APP_ID"),
                prompt=step_prompt,
                biz_params=kwargs,
            )
            result = self.post_process(response)
            if result is None:
                raise MCPEmptyResultError("MCP返回结果不正确（None）")
            return result

        return _retry_with_classified_errors(
            _attempt,
            base_delay=2.0,
            error_prefix="MCP调用",
        )

    async def astep(self, **kwargs):
        """异步 MCP 调用——dashscope 同步 API 通过 to_thread 包裹."""
        try:
            step_prompt = self.step_prompt.format(**kwargs)
        except (KeyError, ValueError) as e:
            logger.warning(f"MCP prompt格式化失败（缺少参数 {e}），使用原始prompt")
            step_prompt = self.step_prompt

        for attempt in range(3):
            try:
                response = await asyncio.to_thread(
                    Application.call,
                    api_key=os.getenv("APP_TOKEN"),
                    app_id=os.getenv("MCP_APP_ID"),
                    prompt=step_prompt,
                    biz_params=kwargs,
                )
                response = self.post_process(response)
                if response is None:
                    raise MCPEmptyResultError("MCP返回结果不正确（None）")
                return response
            except PermanentError:
                raise
            except TransientError as e:
                if attempt < 2:
                    delay = _backoff_delay(attempt, 2.0)
                    logger.warning(
                        f"MCP瞬时错误（尝试 {attempt + 1}/3），"
                        f"{delay:.1f}s 后重试：{e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"MCP调用重试3次全部失败：{e}\n"
                        f"错误类型: {type(e).__name__}"
                    )
            except Exception as e:
                if attempt < 2:
                    delay = _backoff_delay(attempt, 2.0)
                    logger.warning(
                        f"MCP调用未知错误（尝试 {attempt + 1}/3），"
                        f"{delay:.1f}s 后重试：{e}\n{traceback.format_exc()}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"MCP调用重试3次全部失败（未知错误）：{e}\n"
                        f"{traceback.format_exc()}"
                    )
        return None

    def post_process(self, response):
        if response.status_code == 200:
            response = json.loads(response.output.text)
            return response
        else:
            logger.error(f"MCP调用失败，HTTP状态码: {response.status_code}，响应: {response}")
            return None


class WebSearchAgent(MCPAgent):
    @staticmethod
    def _build_search_request(prompt: str, count: int) -> str:
        """把检索 query 包成“只返回结构化结果”的请求。

        当前 MCP_APP_ID 对应的是百炼智能体应用，它默认会把搜索结果改写成
        分析文字。直接传 query 时，复杂问题经常不给链接，后处理只能拿到空结果。
        这里约束输出格式，让应用内部的 bailian_web_search 结果原样返回。
        """
        return (
            "你是联网搜索工具，不要写分析、总结或建议。\n"
            f"请实际联网搜索：{prompt}\n"
            f"只返回一个 JSON 数组，最多 {count} 条，不要用 Markdown 表格。\n"
            '每项格式：{"title":"","url":"https://...","snippet":"..."}\n'
            "要求：\n"
            "1. 每项必须有可直接访问的 http(s) 链接；\n"
            "2. snippet 保留原文关键片段，不要改写；\n"
            "3. 没有结果时只返回 []。\n"
        )

    def step(self, prompt, **kwargs):
        count = int(kwargs.get("count", 10) or 10)
        step_prompt = self._build_search_request(prompt, count)

        # ── cache lookup ──────────────────────────────────────────
        cached = get_cached(prompt)
        if cached is not None:
            return self._normalize_pages(cached)

        api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("APP_TOKEN")
        app_id = os.getenv("MCP_APP_ID")

        # 获取速率限制器
        rate_limiter = get_web_search_rate_limiter()

        def _attempt():
            # 在发送请求前进行速率限制检查
            wait_time = rate_limiter.acquire()
            if wait_time > 0:
                logger.debug(f"速率限制等待: {wait_time:.3f}秒")

            response = Application.call(
                api_key=api_key,
                app_id=app_id,
                prompt=step_prompt,
                biz_params=kwargs,
            )
            result = self.post_process(response)

            # ── cache store ────────────────────────────────────
            if result:
                set_cached(prompt, result=result)

            return result

        return _retry_with_classified_errors(
            _attempt,
            base_delay=2.0,
            error_prefix="Web搜索",
        )

    async def astep(self, prompt, **kwargs):
        """异步 Web 搜索——asyncio.to_thread 包裹同步 MCP API."""
        count = int(kwargs.get("count", 10) or 10)
        step_prompt = self._build_search_request(prompt, count)

        # ── cache lookup ──────────────────────────────────────────
        cached = get_cached(prompt)
        if cached is not None:
            return self._normalize_pages(cached)

        api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("APP_TOKEN")
        app_id = os.getenv("MCP_APP_ID")
        rate_limiter = get_web_search_rate_limiter()

        for attempt in range(3):
            try:
                wait_time = await rate_limiter.aacquire()
                if wait_time > 0:
                    logger.debug(f"速率限制等待(异步): {wait_time:.3f}秒")

                response = await asyncio.to_thread(
                    Application.call,
                    api_key=api_key,
                    app_id=app_id,
                    prompt=step_prompt,
                    biz_params=kwargs,
                )
                response = self.post_process(response)

                if response:
                    set_cached(prompt, result=response)

                return response
            except PermanentError as e:
                logger.error(
                    f"Web搜索永久错误，放弃重试：{e}\n"
                    f"错误类型: {type(e).__name__}, 请求ID: {getattr(e, 'request_id', 'N/A')}"
                )
                raise
            except TransientError as e:
                if attempt < 2:
                    is_429 = "429" in str(e).lower() or isinstance(e, MCPRateLimitError)
                    delay = 5 * (attempt + 1) if is_429 else 2.0 * (attempt + 1)
                    logger.warning(
                        f"Web搜索瞬时错误（尝试 {attempt + 1}/3），"
                        f"{delay:.1f}s 后重试：{e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"Web搜索重试3次全部失败：{e}\n"
                        f"错误类型: {type(e).__name__}"
                    )
            except Exception as e:
                error_msg = str(e)
                if attempt < 2:
                    if "429" in error_msg:
                        delay = 5 * (attempt + 1)
                    else:
                        delay = _backoff_delay(attempt, 2.0)
                    logger.warning(
                        f"Web搜索未知错误（尝试 {attempt + 1}/3），"
                        f"{delay:.1f}s 后重试：{e}\n{traceback.format_exc()}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"Web搜索重试3次全部失败（未知错误）：{e}\n"
                        f"错误类型: {type(e).__name__}\n"
                        f"{traceback.format_exc()}"
                    )
        return None

    def _normalize_pages(self, pages: list[dict]) -> list[dict]:
        before = len(pages or [])
        normalized = normalize_search_results(pages)
        if before != len(normalized):
            logger.info(
                f"[WebSearch] 结果归一化 {normalize_stats(before, len(normalized))}"
            )
        return normalized

    def post_process(self, response):
        if response is None:
            raise MCPEmptyResultError("Web搜索结果不正确（response为None）")
        if response.status_code == 200:
            raw_text = getattr(response.output, "text", "") or ""
            # 智能体经常把 JSON 包在 ```json ... ``` 里，先剥掉围栏再解析
            if raw_text.lstrip().startswith("```"):
                raw_text = re.sub(
                    r"^\s*```(?:json)?\s*", "", raw_text, count=1, flags=re.I
                )
                raw_text = re.sub(r"\s*```\s*$", "", raw_text, count=1)
            try:
                # 解析第一层JSON；增强搜索偶尔会在 JSON 前后附带说明文字
                first_level = _parse_json_payload(raw_text)
                if first_level is None:
                    raise json.JSONDecodeError("未找到可解析的 JSON", raw_text, 0)

                # 检查API是否返回错误
                if "result" in first_level and first_level["result"].get("isError"):
                    error_content = first_level["result"]["content"][0]["text"] if "content" in first_level["result"] else "未知错误"
                    try:
                        error_detail = json.loads(error_content)
                        status = error_detail.get("status", "unknown")
                        request_id = error_detail.get("request_id", "unknown")

                        if status == 429:
                            logger.error(f"API速率限制(429)，请求ID: {request_id}。建议增加请求间隔或联系API提供商提高配额")
                            raise MCPRateLimitError(
                                f"API请求频率超限(429)，请稍后重试",
                                request_id=request_id,
                            )
                        elif status == 401:
                            logger.error(f"API认证失败(401)，请求ID: {request_id}")
                            raise MCPAuthError(
                                f"API认证失败，请检查APP_TOKEN配置",
                                request_id=request_id,
                            )
                        elif status == 403:
                            logger.error(f"API访问被拒绝(403)，请求ID: {request_id}")
                            raise MCPAccessDeniedError(
                                f"API访问被拒绝，请检查权限配置",
                                request_id=request_id,
                            )
                        else:
                            logger.error(f"API返回错误状态 {status}，请求ID: {request_id}，详情: {error_detail}")
                            raise MCPServerError(
                                f"API返回错误(状态码: {status})",
                                request_id=request_id,
                            )
                    except json.JSONDecodeError:
                        logger.error(f"API返回错误，但无法解析错误详情: {error_content}")
                        raise MCPParseError(f"API调用失败: {error_content}")

                # 尝试不同的路径获取pages数据
                pages = None

                # 路径1: result.content[0].text -> pages
                if "result" in first_level and "content" in first_level["result"]:
                    content_text = first_level["result"]["content"][0]["text"]
                    second_level = json.loads(content_text)
                    if "pages" in second_level:
                        pages = second_level["pages"]

                # 路径2: 直接在第一层查找pages
                elif "pages" in first_level:
                    pages = first_level["pages"]

                # 路径3: 查找data.pages
                elif "data" in first_level and "pages" in first_level["data"]:
                    pages = first_level["data"]["pages"]

                if pages is None and isinstance(first_level, dict):
                    for key in ("results", "data", "items", "list"):
                        cand = first_level.get(key)
                        if isinstance(cand, list):
                            pages = cand
                            break
                if pages is None and isinstance(first_level, list):
                    pages = first_level

                if pages is None:
                    results = (
                        _extract_results_from_jsonish_text(raw_text, count=10)
                        or _extract_results_from_text(raw_text, count=10)
                    )
                    if results:
                        logger.info(f"[WebSearch] 应用返回文本结果 {len(results)} 条，已结构化")
                        return self._normalize_pages(results)
                    logger.warning(f"无法从响应中提取pages数据，按空结果处理，响应结构: {json.dumps(first_level, ensure_ascii=False)[:300]}")
                    return []

                # 确保pages是列表
                if not isinstance(pages, list):
                    logger.error(f"pages不是列表类型: {type(pages)}")
                    raise MCPParseError("Web搜索结果格式错误")

                # 提取需要的字段
                processed_pages = []
                for page in pages:
                    if isinstance(page, dict):
                        processed_pages.append({
                            "snippet": page.get("snippet") or page.get("summary") or page.get("description") or "",
                            "title": page.get("title") or page.get("name") or "",
                            "url": page.get("url") or page.get("link") or page.get("href") or ""
                        })

                return self._normalize_pages(processed_pages)
            except json.JSONDecodeError as e:
                raw_text = getattr(response.output, "text", "") or ""
                results = (
                    _extract_results_from_jsonish_text(raw_text, count=10)
                    or _extract_results_from_text(raw_text, count=10)
                )
                if results:
                    logger.info(f"[WebSearch] 应用返回文本结果 {len(results)} 条，已结构化")
                    return self._normalize_pages(results)
                logger.error(f"JSON解析失败: {e}, 原始响应: {raw_text[:500]}")
                # 应用返回的是成文分析而非链接列表：按“无搜索结果”处理，避免整条任务被打死
                logger.warning("[WebSearch] 应用返回内容不含可用链接，本次按空结果处理")
                return []
            except (KeyError, IndexError, TypeError) as e:
                logger.error(f"数据结构解析失败: {e}, 响应内容: {response.output.text[:500]}")
                return []
        else:
            logger.error(f"MCP调用失败，HTTP状态码: {response.status_code}，响应: {response}")
            raise MCPServerError(f"Web搜索API调用失败(HTTP {response.status_code})")


_URL_RE = re.compile(r"https?://[^\s<>\"'）)\]】，,]+")
_MD_PREFIX = re.compile(r"^[\s\-*>#\d\.、\)]+")
_JSON_ITEM_RE = re.compile(
    r'"title"\s*:\s*"(?P<title>.*?)"\s*,\s*"url"\s*:\s*"(?P<url>https?://[^"]+)"'
    r'\s*,\s*"snippet"\s*:\s*"(?P<snippet>.*?)"\s*\}',
    re.S,
)


def _extract_results_from_jsonish_text(text: str, count: int = 10) -> list[dict] | None:
    """从“字段像 JSON、但引号未转义”的返回中按字段抽取结果."""
    results: list[dict] = []
    seen: set[str] = set()
    for match in _JSON_ITEM_RE.finditer(text or ""):
        url = (match.group("url") or "").strip().rstrip(".,;:)")
        if not url or url in seen:
            continue
        seen.add(url)
        results.append({
            "title": (match.group("title") or "").replace('\\"', '"').strip()[:120] or url,
            "url": url,
            "snippet": (match.group("snippet") or "").replace('\\"', '"').strip()[:200],
        })
        if len(results) >= count:
            break
    return results or None


def _parse_json_payload(text: str):
    """从智能体返回中定位并解析 JSON（容忍前后说明文字与代码围栏）."""
    if not text:
        return None

    candidates = []
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.I | re.S)
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(text.strip())

    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            pass

        # 整段不是 JSON 时，从第一个数组/对象起始符开始尝试 raw_decode
        for marker in ("[", "{"):
            idx = candidate.find(marker)
            if idx < 0:
                continue
            try:
                value, _ = decoder.raw_decode(candidate[idx:])
                return value
            except (json.JSONDecodeError, TypeError):
                continue
    return None


def _clean_line(text: str) -> str:
    """去掉 Markdown 序号、加粗、链接前缀等噪声."""
    cleaned = _MD_PREFIX.sub("", (text or "").strip())
    cleaned = cleaned.replace("**", "").strip()
    cleaned = re.sub(r"^(链接|来源|网址|URL)[:：]\s*", "", cleaned, flags=re.I)
    return cleaned.strip(" -*_`")


def _extract_results_from_text(text: str, count: int = 10) -> list[dict] | None:
    """把应用返回的自然语言/Markdown 列表解析成 [{title,url,snippet}].

    百炼智能体应用默认返回人话，而不是旧版 MCP 的 JSON 结构，
    这里按链接位置回溯标题、顺带取一行摘要。
    """
    if not text:
        return None

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    results: list[dict] = []
    seen: set[str] = set()

    for idx, line in enumerate(lines):
        match = _URL_RE.search(line)
        if not match:
            continue

        url = match.group(0).rstrip(".,;:)]}")
        if url in seen:
            continue
        seen.add(url)

        title = ""
        prefix = _clean_line(line[: match.start()])
        if len(prefix) >= 3:
            title = prefix
        else:
            # 典型结构：标题行 → 说明行 → 链接行，优先取链接上方两行
            for j in (idx - 2, idx - 1, idx - 3):
                if j < 0:
                    continue
                cand = lines[j]
                if _URL_RE.search(cand) or cand in ("---", "----"):
                    continue
                cleaned = _clean_line(cand)
                if len(cleaned) >= 3:
                    title = cleaned
                    break

        snippet = ""
        if idx + 1 < len(lines):
            nxt = lines[idx + 1]
            if not _URL_RE.search(nxt) and nxt not in ("---", "----"):
                snippet = _clean_line(nxt)

        results.append({
            "title": (title or url)[:120],
            "url": url,
            "snippet": snippet[:200],
        })
        if len(results) >= count:
            break

    return results or None


if __name__ == '__main__':
    agent = WebSearchAgent()
    response = agent.step(prompt="稳定币", count=10)
    logger.info(response)
