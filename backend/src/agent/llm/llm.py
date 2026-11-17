import os
from contextvars import ContextVar
from loguru import logger

from openai import OpenAI, AsyncOpenAI
from openai import (
    APIError,
    APIConnectionError,
    RateLimitError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    APITimeoutError,
    InternalServerError,
)

from agent.exceptions import (
    LLMRateLimitError,
    LLMServerError,
    LLMNetworkError,
    LLMAuthError,
    LLMBadRequestError,
    LLMUnexpectedError,
)


def _translate_openai_error(exc: APIError) -> Exception:
    """将 OpenAI SDK 异常转换为 Agent 异常分类.

    映射规则：
      - 429 → LLMRateLimitError (Transient)
      - 5xx → LLMServerError (Transient)
      - 网络/超时 → LLMNetworkError (Transient)
      - 401 → LLMAuthError (Permanent)
      - 400/404 → LLMBadRequestError (Permanent)
      - 403 → LLMBadRequestError (Permanent)
    """
    # status_code 是 OpenAI SDK 异常的 property，部分子类（如 APIConnectionError）
    # 没有 response 属性，status_code 访问可能失败，需要安全读取
    try:
        status_code = exc.status_code
    except Exception:
        status_code = None

    if isinstance(exc, RateLimitError) or status_code == 429:
        return LLMRateLimitError(str(exc))
    if isinstance(exc, InternalServerError) or (status_code and status_code >= 500):
        return LLMServerError(str(exc))
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return LLMNetworkError(str(exc))
    if isinstance(exc, AuthenticationError) or status_code == 401:
        return LLMAuthError(str(exc))
    if isinstance(exc, PermissionDeniedError) or status_code == 403:
        return LLMBadRequestError(str(exc))
    if isinstance(exc, (BadRequestError, NotFoundError)) or status_code in (400, 404):
        return LLMBadRequestError(str(exc))

    # 未知 APIError — 保守归类为永久错误
    return LLMUnexpectedError(
        f"Unclassified OpenAI error (HTTP {status_code}): {exc}"
    )


# 任务级思考深度。默认 off：所有模型都尽量走非思考模式。
# 使用 ContextVar 而不是 os.environ，避免多个并发任务互相覆盖。
_THINKING_LEVEL = ContextVar("llm_thinking_level", default=None)

_VALID_THINKING_LEVELS = {"off", "standard", "deep"}


def normalize_thinking_level(level: str | None) -> str:
    """把 UI/环境变量里的不同叫法收敛成统一档位。"""
    value = (level or "off").strip().lower()
    if value in {"off", "none", "false", "disabled", "关闭"}:
        return "off"
    if value in {"standard", "medium", "balanced", "标准", "balanced_mode"}:
        return "standard"
    if value in {"deep", "high", "max", "xhigh", "深入"}:
        return "deep"
    return "off"


def set_thinking_level(level: str | None):
    """设置当前异步任务的思考深度，返回 token 供 reset 使用。"""
    return _THINKING_LEVEL.set(normalize_thinking_level(level))


def reset_thinking_level(token) -> None:
    try:
        _THINKING_LEVEL.reset(token)
    except (ValueError, LookupError):
        pass


def _thinking_params(model_id: str = "") -> dict:
    """按模型厂商返回“关闭思考链”的请求参数。

    - Qwen / 通用 OpenAI 兼容端点：enable_thinking=False
    - DeepSeek V4 系列：thinking={"type":"disabled"}（该字段优先于 reasoning_effort）
    - 如果任务显式选择 standard/deep，才按厂商支持的档位打开思考：
      * DeepSeek：high / max
      * Qwen：high / xhigh
    默认 off，确保所有模型都尽量不产生 reasoning token。
    """
    level = normalize_thinking_level(
        _THINKING_LEVEL.get()
        or os.getenv("LLM_THINKING_LEVEL")
        or os.getenv("LLM_REASONING_EFFORT")
        or "off"
    )
    model = (model_id or "").lower()

    if level == "off":
        if "deepseek" in model:
            # DeepSeek V4 默认开启思考；必须显式 disabled，否则 reasoning_effort 仍会思考
            return {"thinking": {"type": "disabled"}}
        # Qwen 系及大多数 OpenAI 兼容端点识别 enable_thinking
        return {"enable_thinking": False}

    if "deepseek" in model:
        return {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "max" if level == "deep" else "high",
        }

    # Qwen3.8 文档：medium/high → high，xhigh/max → xhigh
    return {
        "enable_thinking": True,
        "reasoning_effort": "xhigh" if level == "deep" else "high",
    }



class OpenAICompatibleLLM:

    def __init__(self, model_id=""):
        self.model_id = model_id

    def generate_response(self, query):
        client = OpenAI(
            api_key=os.getenv('APP_TOKEN'),
            base_url=os.getenv("LLM_BASE_URL"),
            timeout=float(os.getenv("LLM_REQUEST_TIMEOUT", "240")),
            max_retries=0,
        )
        logger.debug(f"本次访问LLM模型为：{self.model_id}")

        try:
            response = client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {
                        "role": "user",
                        "content": query
                    }
                ],
                extra_body=_thinking_params(self.model_id),
            )
        except APIError as e:
            raise _translate_openai_error(e) from e

        content = response.choices[0].message.content
        if content is None:
            raise LLMUnexpectedError("LLM returned empty content (None)")

        return content

    async def agenerate_response(self, query):
        """异步生成 LLM 响应——不阻塞事件循环."""
        client = AsyncOpenAI(
            api_key=os.getenv('APP_TOKEN'),
            base_url=os.getenv("LLM_BASE_URL"),
            timeout=float(os.getenv("LLM_REQUEST_TIMEOUT", "240")),
            max_retries=0,
        )

        try:
            response = await client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {
                        "role": "user",
                        "content": query
                    }
                ],
                extra_body=_thinking_params(self.model_id),
            )
        except APIError as e:
            raise _translate_openai_error(e) from e

        content = response.choices[0].message.content
        if content is None:
            raise LLMUnexpectedError("LLM returned empty content (None)")

        return content

    async def astream_response(self, query):
        """异步流式生成 LLM 响应——逐 token yield，不阻塞事件循环."""
        client = AsyncOpenAI(
            api_key=os.getenv('APP_TOKEN'),
            base_url=os.getenv("LLM_BASE_URL"),
            timeout=float(os.getenv("LLM_REQUEST_TIMEOUT", "240")),
            max_retries=0,
        )
        logger.debug(f"本次流式访问LLM模型为：{self.model_id}")

        try:
            stream = await client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": query},
                ],
                stream=True,
                stream_options={"include_usage": True},
                extra_body=_thinking_params(self.model_id),
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except APIError as e:
            raise _translate_openai_error(e) from e

