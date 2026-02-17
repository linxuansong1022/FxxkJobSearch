"""
SQLite 存储层

职责：
- 建表（自动迁移）
- 插入职位（自动去重，基于 content_hash）
- 按状态查询职位
- 更新职位状态和分析结果
"""

import sqlite3
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 建表语句
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,           -- linkedin / indeed / thehub
    platform_id TEXT,                 -- 原始平台ID
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    url TEXT,
    content_hash TEXT UNIQUE,         -- SHA256(normalize(company+title)) 用于去重
    jd_text TEXT,                     -- 原始JD文本
    analysis TEXT,                    -- Gemini 解析结果 (JSON字符串)
    resume_path TEXT,                 -- 生成的PDF路径
    status TEXT DEFAULT 'new',        -- new / analyzed / generated / skipped
    created_at TEXT DEFAULT (datetime('now'))
);
"""


class JobDatabase:
    """SQLite 数据库操作封装"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row  # 支持按列名访问
        self._init_tables()

    def _init_tables(self):
        """创建表（如果不存在）"""
        self.conn.execute(_CREATE_TABLE_SQL)
        self.conn.commit()

    def insert_job(self, job_data: dict) -> bool:
        """
        插入一条职位记录。如果 content_hash 已存在则跳过。

        Args:
            job_data: 包含以下字段的字典:
                - platform: str
                - platform_id: str (可选)
                - title: str
                - company: str
                - url: str
                - content_hash: str
                - jd_text: str (可选)

        Returns:
            True 如果成功插入，False 如果重复被跳过
        """
        try:
            self.conn.execute(
                """
                INSERT INTO jobs (platform, platform_id, title, company, url, content_hash, jd_text)
                VALUES (:platform, :platform_id, :title, :company, :url, :content_hash, :jd_text)
                """,
                job_data,
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            # content_hash 重复，跳过
            logger.debug(f"跳过重复职位: {job_data.get('title')} @ {job_data.get('company')}")
            return False

    def get_jobs_by_status(self, status: str) -> list[dict]:
        """获取指定状态的所有职位"""
        cursor = self.conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC",
            (status,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def update_job_analysis(self, job_id: int, analysis: dict):
        """更新职位的JD分析结果，状态改为 'analyzed'"""
        self.conn.execute(
            "UPDATE jobs SET analysis = ?, status = 'analyzed' WHERE id = ?",
            (json.dumps(analysis, ensure_ascii=False), job_id),
        )
        self.conn.commit()

    def update_job_resume(self, job_id: int, resume_path: str):
        """更新职位的简历路径，状态改为 'generated'"""
        self.conn.execute(
            "UPDATE jobs SET resume_path = ?, status = 'generated' WHERE id = ?",
            (resume_path, job_id),
        )
        self.conn.commit()

    def update_job_status(self, job_id: int, status: str):
        """直接更新职位状态"""
        self.conn.execute(
            "UPDATE jobs SET status = ? WHERE id = ?",
            (status, job_id),
        )
        self.conn.commit()

    def get_status_counts(self) -> dict[str, int]:
        """获取各状态的职位数量统计"""
        cursor = self.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        )
        return {row["status"]: row["cnt"] for row in cursor.fetchall()}

    def close(self):
        self.conn.close()
