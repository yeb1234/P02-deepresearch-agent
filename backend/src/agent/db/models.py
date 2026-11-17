"""数据库表模型（纯 SQL 表定义，不含 ORM 关系）."""
from __future__ import annotations

from sqlalchemy import Column, Integer, String, DateTime, Text, func, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(Base):
    """用户表."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), nullable=False, unique=True, index=True)
    password = Column(String(256), nullable=False)  # bcrypt 哈希
    created_at = Column(DateTime, server_default=func.now())


class UserThread(Base):
    """用户与研究线程的关联表（应用层管理 user_id → thread_id 映射）."""
    __tablename__ = "user_threads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    thread_id = Column(String(128), nullable=False)
    title = Column(Text, nullable=True)  # 研究主题，用于前端展示历史记录
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "thread_id", name="uq_user_threads_user_thread"),
        {"sqlite_autoincrement": True},
    )


class ResearchRecord(Base):
    """一次深度调研的历史记录（欢迎页历史列表的数据源）.

    status:
      - paused    计划已生成，等待用户确认
      - completed 已完成，answer 为最终报告
      - failed    执行失败
    """
    __tablename__ = "research_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    thread_id = Column(String(128), nullable=False, unique=True, index=True)
    title = Column(Text, nullable=True)      # 列表展示用的短标题
    question = Column(Text, nullable=True)   # 用户原始问题
    plan = Column(Text, nullable=True)       # 研究计划
    answer = Column(Text, nullable=True)     # 最终报告（Markdown）
    status = Column(String(32), nullable=False, default="paused")
    stage = Column(String(64), nullable=True)        # 当前 LangGraph 节点
    stage_label = Column(Text, nullable=True)        # 当前步骤的中文名
    step_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ResearchStep(Base):
    """调研过程的分步快照：每个 LangGraph 节点完成后追加一条.

    与 research_records 分离，保证“做到哪步存哪步”，
    即使 Redis checkpoint 过期，历史里仍能看到每一步产出。
    """
    __tablename__ = "research_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    thread_id = Column(String(128), nullable=False, index=True)
    seq = Column(Integer, nullable=False)            # 该 thread 内的步骤序号
    node = Column(String(64), nullable=False)        # LangGraph 节点名
    label = Column(Text, nullable=True)              # 中文步骤名
    content = Column(Text, nullable=True)            # 该步骤的可读产出
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("thread_id", "seq", name="uq_research_steps_thread_seq"),
    )
