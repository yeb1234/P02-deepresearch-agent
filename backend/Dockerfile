FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY src ./src
COPY scripts ./scripts

# 按 pyproject.toml 的依赖清单安装（外加运行时必需但未写入 pyproject 的 loguru/redis/ipython/uvicorn）
RUN pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple && pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    "langgraph>=0.2.6" \
    "langchain>=0.3.19" \
    "langchain-core>=0.3" \
    "langgraph-sdk>=0.1.57" \
    "langgraph-checkpoint-redis" \
    "langgraph-checkpoint" \
    openai \
    "python-dotenv>=1.0.1" \
    fastapi \
    "uvicorn[standard]" \
    "sqlalchemy[asyncio]>=2.0" \
    "asyncpg>=0.30.0" \
    bcrypt \
    passlib \
    dashscope \
    pymilvus \
    loguru \
    redis \
    ipython

COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh

EXPOSE 2024

ENTRYPOINT ["./docker-entrypoint.sh"]
