"""
分层记忆系统 — 对应 hello-agents 第八章 (记忆与检索)

三层记忆架构:
1. WorkingMemory:   当前任务的活跃上下文 (token-受限)
2. ShortTermMemory: 本次运行的执行轨迹 (内存中)
3. LongTermMemory:  跨运行的知识积累 (SQLite 持久化)

设计理念:
- 参照人类记忆: 工作记忆 → 短期记忆 → 长期记忆
- 记忆整合 (consolidate): 将有价值的短期记忆转化为长期记忆
- 相关性检索 (recall): 基于文本相似度从长期记忆中提取相关经验
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class MemoryEntry:
    """记忆条目"""
    content: str
    source: str  # agent_name
    memory_type: str  # "trajectory", "reflection", "insight"
    importance: float = 0.5  # 0-1 重要性
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass
class RunRecord:
    """Agent 运行记录"""
    agent_name: str
    task: str
    summary: str
    trajectory_json: str
    timestamp: float = field(default_factory=time.time)
    metrics: dict = field(default_factory=dict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Working Memory (Token-constrained context window)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class WorkingMemory:
    """
    工作记忆 — 当前任务的活跃上下文

    类比人类: 你正在思考的内容, 容量有限。
    功能: 管理 LLM 上下文窗口, 自动压缩过长的历史。
    """

    def __init__(self, max_tokens: int = 8000):
        self.max_tokens = max_tokens
        self._entries: list[str] = []
        self._estimated_tokens: int = 0

    def add(self, content: str):
        """添加内容到工作记忆"""
        # 粗略估算 token 数 (1 token ≈ 4 chars for English, 2 for CJK)
        estimated = len(content) // 3
        self._entries.append(content)
        self._estimated_tokens += estimated

        # 超出上限时, 压缩旧记忆
        while self._estimated_tokens > self.max_tokens and len(self._entries) > 1:
            removed = self._entries.pop(0)
            self._estimated_tokens -= len(removed) // 3

    def get_context(self) -> str:
        """获取当前工作记忆上下文"""
        return "\n".join(self._entries)

    def clear(self):
        """清空工作记忆"""
        self._entries.clear()
        self._estimated_tokens = 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Short-term Memory (In-memory, current run)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ShortTermMemory:
    """
    短期记忆 — 本次运行的执行轨迹

    类比人类: 今天发生的事情, 还没有被整理和归档。
    功能: 临时存储本次运行的所有 Agent 行为和观察。
    """

    def __init__(self, max_entries: int = 100):
        self.max_entries = max_entries
        self._entries: list[MemoryEntry] = []

    def add(self, entry: MemoryEntry):
        """添加记忆条目"""
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            # 丢弃最旧的低重要性记忆
            self._entries.sort(key=lambda e: e.importance, reverse=True)
            self._entries = self._entries[: self.max_entries]

    def get_recent(self, n: int = 10) -> list[MemoryEntry]:
        """获取最近 N 条记忆"""
        return self._entries[-n:]

    def get_by_source(self, source: str) -> list[MemoryEntry]:
        """获取某个 Agent 的记忆"""
        return [e for e in self._entries if e.source == source]

    def get_high_importance(self, threshold: float = 0.7) -> list[MemoryEntry]:
        """获取高重要性记忆 (用于整合到长期记忆)"""
        return [e for e in self._entries if e.importance >= threshold]

    def clear(self):
        self._entries.clear()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Long-term Memory (SQLite persistent storage)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


_MEMORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    importance REAL DEFAULT 0.5,
    timestamp REAL NOT NULL,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    task TEXT NOT NULL,
    summary TEXT,
    trajectory TEXT,
    metrics TEXT DEFAULT '{}',
    timestamp REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


class LongTermMemory:
    """
    长期记忆 — 跨运行的知识积累

    类比人类: 已经被整理和归档的经验知识。
    功能: 持久化存储 Agent 运行记录和重要洞察。
    检索: 基于关键词匹配 (可扩展为向量检索)。
    """

    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        # 增加 timeout 并且启用 WAL 模式以支持更高并发，防止 database is locked
        self.conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        """初始化记忆表"""
        self.conn.execute(_MEMORY_TABLE_SQL)
        self.conn.execute(_RUNS_TABLE_SQL)
        self.conn.commit()

    def store(self, entry: MemoryEntry):
        """存储一条长期记忆"""
        self.conn.execute(
            """INSERT INTO agent_memory 
               (content, source, memory_type, importance, timestamp, metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                entry.content,
                entry.source,
                entry.memory_type,
                entry.importance,
                entry.timestamp,
                json.dumps(entry.metadata, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def store_run(self, record: RunRecord):
        """存储一次运行记录"""
        self.conn.execute(
            """INSERT INTO agent_runs
               (agent_name, task, summary, trajectory, metrics, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                record.agent_name,
                record.task,
                record.summary,
                record.trajectory_json,
                json.dumps(record.metrics, ensure_ascii=False),
                record.timestamp,
            ),
        )
        self.conn.commit()

    def recall(self, query: str, k: int = 5) -> list[str]:
        """
        检索相关记忆

        当前实现: 基于关键词匹配 (SQLite LIKE)
        TODO: 升级为向量检索 (text-embedding-004 + ChromaDB)
        """
        keywords = query.lower().split()[:5]  # 取前5个关键词
        conditions = " OR ".join(
            [f"LOWER(content) LIKE ?" for _ in keywords]
        )
        params = [f"%{kw}%" for kw in keywords]

        cursor = self.conn.execute(
            f"""SELECT content, importance, timestamp 
                FROM agent_memory 
                WHERE {conditions}
                ORDER BY importance DESC, timestamp DESC
                LIMIT ?""",
            params + [k],
        )
        return [row["content"] for row in cursor.fetchall()]

    def get_recent_runs(self, n: int = 5) -> list[dict]:
        """获取最近 N 次运行记录"""
        cursor = self.conn.execute(
            """SELECT * FROM agent_runs 
               ORDER BY timestamp DESC LIMIT ?""",
            (n,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        self.conn.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Unified Memory System
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class MemorySystem:
    """
    统一记忆系统 — 整合三层记忆

    为 Agent 提供:
    - recall():       检索相关历史经验
    - commit_run():   提交本次运行结果
    - consolidate():  将短期记忆整合到长期记忆
    """

    def __init__(self, db_path: Path | str):
        self.working = WorkingMemory(max_tokens=8000)
        self.short_term = ShortTermMemory(max_entries=100)
        self.long_term = LongTermMemory(db_path)

    def recall(self, query: str, k: int = 5) -> list[str]:
        """
        混合检索 — 短期 + 长期记忆

        Priority: 短期记忆 (本次运行) → 长期记忆 (历史)
        """
        results = []

        # 从短期记忆检索
        for entry in self.short_term.get_recent(k):
            results.append(entry.content)

        # 从长期记忆检索
        long_term_results = self.long_term.recall(query, k=k - len(results))
        results.extend(long_term_results)

        return results[:k]

    def commit_run(
        self,
        agent_name: str,
        task: str,
        summary: str,
        trajectory: list = None,
    ):
        """提交一次 Agent 运行结果"""
        # 存入短期记忆
        self.short_term.add(
            MemoryEntry(
                content=f"[{agent_name}] {task} → {summary}",
                source=agent_name,
                memory_type="trajectory",
                importance=0.6,
            )
        )

        # 存入长期记忆 (运行记录)
        trajectory_json = json.dumps(
            [
                {
                    "type": str(s.step_type.value) if hasattr(s, "step_type") else "unknown",
                    "content": s.content if hasattr(s, "content") else str(s),
                }
                for s in (trajectory or [])
            ],
            ensure_ascii=False,
        )

        self.long_term.store_run(
            RunRecord(
                agent_name=agent_name,
                task=task,
                summary=summary,
                trajectory_json=trajectory_json,
            )
        )

    def consolidate(self):
        """
        记忆整合 — 将短期记忆中高价值的转为长期记忆

        对应 hello-agents 第八章的"记忆形成的认知过程"
        """
        high_importance = self.short_term.get_high_importance(threshold=0.7)
        for entry in high_importance:
            self.long_term.store(entry)
            logger.debug(f"记忆整合: [{entry.source}] {entry.content[:100]}")

        logger.info(f"整合了 {len(high_importance)} 条高价值记忆到长期记忆")

    def close(self):
        self.long_term.close()
