"""
Phase 4 Tests: Notification Quality

验证:
- _is_valid_job_url 过滤聚合页 URL
- send_daily_report 提取 analysis 中的详细字段
- 增量通知 (notified_at 标记，不重复推送)
- 消息格式包含 match_reason, hard_skills, role_type
"""

import json
import re
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestIsValidJobUrl:
    """Test URL validation before notification."""

    def test_valid_indeed_job(self):
        from src.notifier import _is_valid_job_url
        assert _is_valid_job_url("https://indeed.com/viewjob?jk=abc123") is True

    def test_indeed_search_rejected(self):
        from src.notifier import _is_valid_job_url
        assert _is_valid_job_url("https://indeed.com/jobs?q=python+intern") is False

    def test_indeed_q_format_rejected(self):
        from src.notifier import _is_valid_job_url
        assert _is_valid_job_url("https://indeed.com/q-python-intern-l-denmark.html") is False

    def test_linkedin_search_rejected(self):
        from src.notifier import _is_valid_job_url
        assert _is_valid_job_url("https://linkedin.com/jobs/search?keywords=python") is False

    def test_linkedin_view_accepted(self):
        from src.notifier import _is_valid_job_url
        assert _is_valid_job_url("https://linkedin.com/jobs/view/123456") is True

    def test_glassdoor_search_rejected(self):
        from src.notifier import _is_valid_job_url
        # Capital J in /Job/ is the search page
        assert _is_valid_job_url("https://glassdoor.com/Job/copenhagen-python-jobs.htm") is False

    def test_glassdoor_listing_accepted(self):
        from src.notifier import _is_valid_job_url
        assert _is_valid_job_url("https://glassdoor.com/job-listing/python-developer-123.htm") is True

    def test_empty_url_rejected(self):
        from src.notifier import _is_valid_job_url
        assert _is_valid_job_url("") is False
        assert _is_valid_job_url(None) is False

    def test_short_url_rejected(self):
        from src.notifier import _is_valid_job_url
        assert _is_valid_job_url("http://a") is False


class TestNotificationContent:
    """Test that notification messages include analysis details."""

    def _make_db(self):
        from src.database import JobDatabase
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db = JobDatabase(Path(tmp.name))
        return db

    def _insert_analyzed_job(self, db, job_id, title, company, url, analysis_dict, notified_at=None):
        from src.utils import compute_job_hash
        content_hash = compute_job_hash(f"notif-test-{job_id}", "test")
        db.conn.execute(
            """INSERT INTO jobs (id, platform, title, company, url, content_hash,
                                jd_text, relevance, status, analysis, notified_at)
               VALUES (?, 'test', ?, ?, ?, ?, 'Full JD text here...', 'relevant', 'analyzed', ?, ?)""",
            (job_id, title, company, url, content_hash,
             json.dumps(analysis_dict), notified_at),
        )
        db.conn.commit()

    @patch("src.notifier._send_message")
    def test_message_includes_match_reason(self, mock_send):
        db = self._make_db()
        analysis = {
            "match_score": 0.85,
            "match_reason": "Strong Python background matches",
            "hard_skills": ["Python", "PyTorch", "FastAPI"],
            "role_type": "Internship",
            "summary": "AI Intern at Novo Nordisk"
        }
        self._insert_analyzed_job(db, 1, "AI Intern", "Novo Nordisk",
                                  "https://linkedin.com/jobs/view/123", analysis)

        from src.notifier import send_daily_report
        send_daily_report(db)

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "Strong Python background" in msg
        assert "Python" in msg  # skills
        assert "Internship" in msg  # role_type
        assert "Novo Nordisk" in msg
        db.close()

    @patch("src.notifier._send_message")
    def test_message_includes_hard_skills(self, mock_send):
        db = self._make_db()
        analysis = {
            "match_score": 0.9,
            "hard_skills": ["Python", "Docker", "Kubernetes", "Go", "Terraform"],
            "role_type": "Internship",
        }
        self._insert_analyzed_job(db, 1, "DevOps Intern", "Maersk",
                                  "https://indeed.com/viewjob?jk=def456", analysis)

        from src.notifier import send_daily_report
        send_daily_report(db)

        msg = mock_send.call_args[0][0]
        assert "🔑" in msg
        # Should truncate to 4 skills max
        assert "Python" in msg
        assert "Docker" in msg
        db.close()


class TestIncrementalNotification:
    """Test that already-notified jobs are not re-sent."""

    def _make_db(self):
        from src.database import JobDatabase
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db = JobDatabase(Path(tmp.name))
        return db

    def _insert_analyzed_job(self, db, job_id, title, analysis_dict, notified_at=None):
        from src.utils import compute_job_hash
        content_hash = compute_job_hash(f"incr-test-{job_id}", "test")
        db.conn.execute(
            """INSERT INTO jobs (id, platform, title, company, url, content_hash,
                                jd_text, relevance, status, analysis, notified_at)
               VALUES (?, 'test', ?, 'TestCo', 'https://linkedin.com/jobs/view/999',
                        ?, 'Long JD text', 'relevant', 'analyzed', ?, ?)""",
            (job_id, title, content_hash,
             json.dumps(analysis_dict), notified_at),
        )
        db.conn.commit()

    @patch("src.notifier._send_message")
    def test_already_notified_not_resent(self, mock_send):
        db = self._make_db()
        analysis = {"match_score": 0.9, "role_type": "Internship"}

        # Already notified
        self._insert_analyzed_job(db, 1, "Old Intern Job", analysis,
                                  notified_at="2026-01-01T00:00:00")
        # New job, not yet notified
        self._insert_analyzed_job(db, 2, "New Intern Job", analysis, notified_at=None)

        from src.notifier import send_daily_report
        send_daily_report(db)

        msg = mock_send.call_args[0][0]
        assert "New Intern Job" in msg
        assert "Old Intern Job" not in msg
        db.close()

    @patch("src.notifier._send_message")
    def test_notified_at_gets_set(self, mock_send):
        db = self._make_db()
        analysis = {"match_score": 0.85, "role_type": "Internship"}
        self._insert_analyzed_job(db, 1, "Test Intern Job", analysis)

        from src.notifier import send_daily_report
        send_daily_report(db)

        row = db.conn.execute("SELECT notified_at FROM jobs WHERE id = 1").fetchone()
        assert row["notified_at"] is not None
        db.close()

    @patch("src.notifier._send_message")
    def test_aggregate_url_filtered_from_notification(self, mock_send):
        db = self._make_db()
        analysis = {"match_score": 0.9}

        # Insert job with aggregate URL
        from src.utils import compute_job_hash
        content_hash = compute_job_hash("agg-url-test", "test")
        db.conn.execute(
            """INSERT INTO jobs (id, platform, title, company, url, content_hash,
                                jd_text, relevance, status, analysis, notified_at)
               VALUES (1, 'test', 'Python Intern', 'TestCo',
                        'https://indeed.com/jobs?q=python+intern',
                        ?, 'Long JD text', 'relevant', 'analyzed', ?, NULL)""",
            (content_hash, json.dumps(analysis)),
        )
        db.conn.commit()

        from src.notifier import send_daily_report
        send_daily_report(db)

        # Should have sent "no high score" message since the only job had aggregate URL
        msg = mock_send.call_args[0][0]
        assert "暂无" in msg
        db.close()


class TestNotifiedAtMigration:
    """Test that notified_at column exists in database."""

    def test_notified_at_column_exists(self):
        from src.database import JobDatabase
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db = JobDatabase(Path(tmp.name))

        # Check column exists by querying PRAGMA
        cursor = db.conn.execute("PRAGMA table_info(jobs)")
        columns = [row[1] for row in cursor.fetchall()]
        assert "notified_at" in columns
        db.close()
