"""
SQLite 存储层

职责：
- 建表 + 自动迁移新字段
- 插入职位（自动去重，基于 content_hash）
- 按状态查询职位
- 更新职位状态和分析结果
"""

import sqlite3
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    platform_id TEXT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    url TEXT,
    content_hash TEXT UNIQUE,
    jd_text TEXT,
    posted_at TEXT,              -- 职位发布时间 (ISO格式)
    relevance TEXT,              -- relevant / irrelevant / unscored
    analysis TEXT,
    resume_path TEXT,
    status TEXT DEFAULT 'new',   -- new / filtered / analyzed / generated / skipped
    created_at TEXT DEFAULT (datetime('now'))
);
"""

# 迁移：给已有表加新字段
_MIGRATIONS = [
    "ALTER TABLE jobs ADD COLUMN posted_at TEXT",
    "ALTER TABLE jobs ADD COLUMN relevance TEXT DEFAULT 'unscored'",
]


class JobDatabase:
    """SQLite 数据库操作封装"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_tables()
        self._run_migrations()

    def _init_tables(self):
        self.conn.execute(_CREATE_TABLE_SQL)
        self.conn.commit()

    def _run_migrations(self):
        """安全地添加新字段（如果不存在）"""
        for sql in _MIGRATIONS:
            try:
                self.conn.execute(sql)
                self.conn.commit()
            except sqlite3.OperationalError:
                pass  # 字段已存在，跳过

    def insert_job(self, job_data: dict) -> bool:
        """
        插入一条职位记录。如果 content_hash 已存在则跳过。

        job_data 字段:
            platform, platform_id, title, company, url,
            content_hash, jd_text, posted_at (可选)
        """
        try:
            self.conn.execute(
                """
                INSERT INTO jobs (platform, platform_id, title, company, url,
                                  content_hash, jd_text, posted_at)
                VALUES (:platform, :platform_id, :title, :company, :url,
                        :content_hash, :jd_text, :posted_at)
                """,
                job_data,
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            logger.debug(f"跳过重复: {job_data.get('title')} @ {job_data.get('company')}")
            return False

    def get_jobs_by_status(self, status: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC",
            (status,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_unscored_jobs(self) -> list[dict]:
        """获取还没有做过相关性评分的职位"""
        cursor = self.conn.execute(
            "SELECT * FROM jobs WHERE relevance IS NULL OR relevance = 'unscored' ORDER BY id",
        )
        return [dict(row) for row in cursor.fetchall()]

    def update_job_relevance(self, job_id: int, relevance: str, status: str = None):
        """更新职位相关性标记，可选同时更新状态"""
        if status:
            self.conn.execute(
                "UPDATE jobs SET relevance = ?, status = ? WHERE id = ?",
                (relevance, status, job_id),
            )
        else:
            self.conn.execute(
                "UPDATE jobs SET relevance = ? WHERE id = ?",
                (relevance, job_id),
            )
        self.conn.commit()

    def update_job_analysis(self, job_id: int, analysis: dict):
        self.conn.execute(
            "UPDATE jobs SET analysis = ?, status = 'analyzed' WHERE id = ?",
            (json.dumps(analysis, ensure_ascii=False), job_id),
        )
        self.conn.commit()

    def update_job_jd(self, job_id: int, jd_text: str):
        """补全JD文本"""
        self.conn.execute(
            "UPDATE jobs SET jd_text = ? WHERE id = ?",
            (jd_text, job_id),
        )
        self.conn.commit()

    def update_job_resume(self, job_id: int, resume_path: str):
        self.conn.execute(
            "UPDATE jobs SET resume_path = ?, status = 'generated' WHERE id = ?",
            (resume_path, job_id),
        )
        self.conn.commit()

    def update_job_status(self, job_id: int, status: str):
        self.conn.execute(
            "UPDATE jobs SET status = ? WHERE id = ?",
            (status, job_id),
        )
        self.conn.commit()

    def get_status_counts(self) -> dict[str, int]:
        cursor = self.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        )
        return {row["status"]: row["cnt"] for row in cursor.fetchall()}

    def get_relevance_counts(self) -> dict[str, int]:
        cursor = self.conn.execute(
            "SELECT relevance, COUNT(*) as cnt FROM jobs GROUP BY relevance"
        )
        return {row["relevance"] or "unscored": row["cnt"] for row in cursor.fetchall()}

    def get_relevant_jobs_summary(self) -> list[dict]:
        """获取所有标记为 relevant 的职位摘要"""
        cursor = self.conn.execute(
            """SELECT id, platform, title, company, url, posted_at, status
               FROM jobs WHERE relevance = 'relevant'
               ORDER BY posted_at DESC NULLS LAST, id DESC""",
        )
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        self.conn.close()
