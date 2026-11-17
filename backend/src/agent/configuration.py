import os
import json
from pydantic import BaseModel, Field
from typing import Any, Optional, List
from langchain_core.runnables import RunnableConfig

# 模型ID常量
MODEL_ID_DEFAULT = "qwen-plus-latest"
MODEL_ID_FLASH = "qwen3.8-flash"
MODEL_ID_PLUS = "qwen3.8-flash"
MODEL_ID_MAX = "deepseek-v4-pro"
MODEL_ID_JUDEG = "deepseek-v4-pro"

class ModelConfig(BaseModel):
    """模型配置项"""
    model_id: str = Field(..., description="模型ID")
    display_name: str = Field(..., description="显示名称")
    icon: str = Field(default="Zap", description="图标类型(Zap/Cpu)")
    icon_color: str = Field(default="yellow-400", description="图标颜色")
    is_default: bool = Field(default=False, description="是否为默认模型")


def load_available_models_from_env() -> List[ModelConfig]:
    """从环境变量加载可用模型列表"""
    default_models = [
        ModelConfig(
            model_id=MODEL_ID_DEFAULT,
            display_name="Qwen-Plus",
            icon="Zap",
            icon_color="orange-400",
            is_default=True,
        ),
        ModelConfig(
            model_id=MODEL_ID_FLASH,
            display_name="Qwen3.8-Flash",
            icon="Zap",
            icon_color="yellow-400",
        ),
        ModelConfig(
            model_id="deepseek-v4.1-flash",
            display_name="DeepSeek V4.1 Flash",
            icon="Zap",
            icon_color="blue-400",
        ),
    ]
    models_json = os.getenv("AVAILABLE_MODELS")
    
    if not models_json:
        # 默认模型列表
        return default_models
    
    try:
        models_data = json.loads(models_json)
        return [ModelConfig(**model) for model in models_data]
    except Exception as e:
        print(f"警告: 解析AVAILABLE_MODELS失败，使用默认模型列表。错误: {e}")
        return default_models


def get_default_model_id() -> str:
    """获取默认模型ID.

    优先级：
    1. DEFAULT_MODEL_ID 环境变量（且必须在 AVAILABLE_MODELS 列表中）
    2. AVAILABLE_MODELS 中 is_default=true 的模型
    3. 列表第一项
    4. 代码兜底常量
    """
    models = load_available_models_from_env()
    explicit = (os.getenv("DEFAULT_MODEL_ID") or "").strip()
    if explicit and any(model.model_id == explicit for model in models):
        return explicit

    flagged = next((model for model in models if model.is_default), None)
    if flagged:
        return flagged.model_id

    if models:
        return models[0].model_id
    return MODEL_ID_DEFAULT


def resolve_model_id(model_id: Optional[str]) -> str:
    """把客户端传入的模型ID收敛到服务端白名单内.

    未传、拼写错误或不在 AVAILABLE_MODELS 中的模型一律回退到默认模型，
    避免任意模型ID进入任务队列或造成未知计费。
    """
    models = load_available_models_from_env()
    allowed = {model.model_id for model in models}
    candidate = (model_id or "").strip()
    if candidate in allowed:
        return candidate
    return get_default_model_id()

def get_flash_model_id() -> str:
    """优先选择真正的 Flash 模型用于查询生成/搜索摘要.

    旧实现取“第一个 Zap 图标”，而默认列表第一项是 Qwen-Plus，
    会把搜索中间层错误地路由到非 Flash 模型。
    """
    models = load_available_models_from_env()
    for model in models:
        label = f"{model.model_id} {model.display_name}".lower()
        if "flash" in label:
            return model.model_id
    for model in models:
        if model.icon == "Zap":
            return model.model_id
    return models[0].model_id if models else MODEL_ID_FLASH  # 兜底默认值


def get_plus_model_id() -> str:
    """获取居中的模型ID"""
    models = load_available_models_from_env()
    if models:
        middle_index = len(models) // 2
        return models[middle_index].model_id
    return MODEL_ID_PLUS  # 兜底默认值

def get_judge_model_id() -> str:
    """评估 Judge 的兜底模型；显式覆盖走 EVAL_MODEL / JUDGE_MODEL。"""
    return MODEL_ID_JUDEG


def get_review_model_id() -> str:
    """评审/评估专用模型：默认 DeepSeek V4.1 Flash。

    常规生成继续沿用用户选择的 Qwen；只有质量门禁（反思评估、初稿评审）
    走更强的 DeepSeek，避免“写作模型自己审自己”。
    """
    explicit = (os.getenv("REVIEW_MODEL") or "").strip()
    if explicit:
        return resolve_model_id(explicit)
    models = load_available_models_from_env()
    for model in models:
        label = f"{model.model_id} {model.display_name}".lower()
        if "deepseek" in label and "4.1" in label and "flash" in label:
            return model.model_id
    return "deepseek-v4.1-flash"

class Configuration(BaseModel):
    """agent的配置."""

    # 可用模型列表配置（从环境变量加载）
    available_models: List[ModelConfig] = Field(
        default_factory=load_available_models_from_env,
        metadata={"description": "可用的LLM模型列表"},
    )

    query_generator_model: str = Field(
        default_factory=get_flash_model_id,
        metadata={
            "description": "用于Agent查询生成的LLM的名称."
        },
    )

    search_summary_model: str = Field(
        default_factory=get_flash_model_id,
        metadata={
            "description": "用于搜索 MCP 结果摘要的LLM名称；与最终写作模型解耦."
        },
    )

    reflection_model: str = Field(
        default_factory=get_plus_model_id,
        metadata={
            "description": "用于Agent反思的LLM的名称."
        },
    )

    review_model: str = Field(
        default_factory=get_review_model_id,
        metadata={
            "description": "用于反思评估与初稿评审的LLM名称；默认 DeepSeek V4.1 Flash."
        },
    )

    answer_model: str = Field(
        default_factory=get_default_model_id,
        metadata={
            "description": "用于Agent生成答案的LLM模型名称."
        },
    )

    number_of_initial_queries: int = Field(
        default=2,
        metadata={"description": "要生成的初始搜索查询数量."},
    )

    max_research_loops: int = Field(
        default=2,
        metadata={"description": "要执行的最大research循环次数."},
    )

    # ── 交叉编码器重排序配置 ──────────────────────────────────────
    reranker_kb_enabled: bool = Field(
        default=False,
        metadata={"description": "是否在 KB 检索（FactStore.query）中启用交叉编码器精排"},
    )

    reranker_web_enabled: bool = Field(
        default=False,
        metadata={"description": "是否在 Web 搜索（_web_search）中启用交叉编码器精排"},
    )

    reranker_model: str = Field(
        default="gte-rerank",
        metadata={"description": "重排序模型ID（DashScope TextReRank 模型）"},
    )

    reranker_api_key: str = Field(
        default="",
        metadata={"description": "重排序API密钥（留空则复用 DASHSCOPE_API_KEY → APP_TOKEN）"},
    )

    reranker_top_k: int = Field(
        default=5,
        metadata={"description": "重排序后保留的文档数量"},
    )

    reranker_min_score: float = Field(
        default=0.0,
        metadata={"description": "重排序后过滤的最低相关性分数（0.0-1.0）"},
    )

    @classmethod
    def from_runnable_config(
        cls, config: Optional[RunnableConfig] = None
    ) -> "Configuration":
        """从RunnableConfig创建配置实例."""
        configurable = (
            config["configurable"] if config and "configurable" in config else {}
        )

        raw_values: dict[str, Any] = {}
        for name in cls.model_fields.keys():
            # 跳过 available_models，它应该从环境变量直接加载
            if name == "available_models":
                continue
            env_value = os.environ.get(name.upper())
            config_value = configurable.get(name)
            raw_values[name] = env_value if env_value is not None else config_value

        values = {k: v for k, v in raw_values.items() if v is not None}

        return cls(**values)
