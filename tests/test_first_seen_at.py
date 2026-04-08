"""
first_seen_at 列测试

验证:
1. 新插入的 job 自动获得 first_seen_at = 当前时间
2. notifier SQL 只会推近 2 天 first_seen_at 的岗位(老岗位即使被重新分析也不会再推)
3. filter._is_too_old 在 posted_at=None 时 fall back 到 first_seen_at
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.database import JobDatabase
from src.filter import _is_too_old
from src.utils import compute_job_hash


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    database = JobDatabase(db_path)
    yield database
    database.close()
    db_path.unlink(missing_ok=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Schema: first_seen_at auto-fill
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFirstSeenAtAutoFill:
    def test_new_insert_has_first_seen_at(self, db):
        job = {
            "platform": "test",
            "platform_id": "1",
            "title": "Python Intern",
            "company": "Acme",
            "url": "https://x",
            "content_hash": compute_job_hash("Acme", "Python Intern"),
            "jd_text": "JD",
            "posted_at": None,
        }
        assert db.insert_job(job) is True

        cursor = db.conn.execute(
            "SELECT first_seen_at FROM jobs WHERE platform_id='1'"
        )
        row = cursor.fetchone()
        assert row["first_seen_at"] is not None
        # 应该是 SQLite datetime('now') 格式
        parsed = datetime.strptime(row["first_seen_at"], "%Y-%m-%d %H:%M:%S")
        # 当前时间附近 5 分钟内
        delta = abs((datetime.now(timezone.utc).replace(tzinfo=None) - parsed).total_seconds())
        assert delta < 300


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# filter._is_too_old fall back behavior
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestIsTooOldFallback:
    def test_no_posted_at_uses_first_seen_at_old(self):
        # first_seen_at 是 30 天前 → 应该判定为 too old
        old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        assert _is_too_old(None, first_seen_at=old) is True

    def test_no_posted_at_uses_first_seen_at_fresh(self):
        # first_seen_at 是今天 → 不算 too old
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        assert _is_too_old(None, first_seen_at=fresh) is False

    def test_posted_at_takes_priority_over_first_seen_at(self):
        # posted_at 是今天(新),first_seen_at 是 30 天前 → posted_at 优先,不算旧
        posted = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        old_seen = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        assert _is_too_old(posted, first_seen_at=old_seen) is False

    def test_both_none_passes(self):
        # 兜底:两者都没有就放行(不阻挡)
        assert _is_too_old(None, first_seen_at=None) is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# notifier query: only push recent first_seen_at
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestNotifierFreshnessFilter:
    def _insert_analyzed(
        self, db: JobDatabase, idx: int, first_seen_offset_days: int
    ) -> None:
        """插入一条 analyzed 状态的岗位,first_seen_at 设为 N 天前。"""
        job = {
            "platform": "test",
            "platform_id": str(idx),
            "title": f"Role {idx}",
            "company": f"Company {idx}",
            "url": f"https://x/{idx}",
            "content_hash": compute_job_hash(f"Company {idx}", f"Role {idx}"),
            "jd_text": "JD",
            "posted_at": None,
        }
        assert db.insert_job(job) is True
        # 手动改 first_seen_at 模拟历史数据
        offset = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=first_seen_offset_days)
        ).strftime("%Y-%m-%d %H:%M:%S")
        db.conn.execute(
            "UPDATE jobs SET first_seen_at=?, status='analyzed', "
            "analysis=?, relevance='relevant' WHERE platform_id=?",
            (offset, json.dumps({"match_score": 0.9}), str(idx)),
        )
        db.conn.commit()

    def test_only_recent_jobs_returned_by_notifier_query(self, db):
        # 插入 3 条:今天 / 1 天前 / 5 天前
        self._insert_analyzed(db, 1, 0)
        self._insert_analyzed(db, 2, 1)
        self._insert_analyzed(db, 3, 5)

        # 复用 notifier 的 SQL
        cursor = db.conn.execute(
            """SELECT platform_id FROM jobs
               WHERE status='analyzed'
                 AND notified_at IS NULL
                 AND (first_seen_at IS NULL OR first_seen_at >= datetime('now', '-2 days'))
               ORDER BY id DESC"""
        )
        ids = {row["platform_id"] for row in cursor.fetchall()}
        assert ids == {"1", "2"}  # 5 天前那条被过滤
