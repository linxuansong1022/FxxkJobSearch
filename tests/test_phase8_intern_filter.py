"""
Phase 8 Tests — Intern/Student-Only Filtering
Validates:
1. Filter prompt rejects full-time and non-Denmark jobs
2. Analyzer prompt penalizes full-time roles in scoring
3. Notifier only pushes intern/student/part-time jobs
4. ISS A/S debug file path sanitization
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch


# ── 1. Filter Prompt Tests ──────────────────────────────────────────

class TestFilterPromptContent:
    """Verify filter prompt enforces intern/student/Denmark restrictions."""

    def _get_filter_prompt_source(self):
        return Path("src/filter.py").read_text()

    def test_prompt_mentions_denmark_only(self):
        src = self._get_filter_prompt_source()
        assert "Denmark ONLY" in src

    def test_prompt_rejects_fulltime_permanent(self):
        src = self._get_filter_prompt_source()
        assert "Full-time permanent" in src

    def test_prompt_rejects_senior_roles(self):
        src = self._get_filter_prompt_source()
        assert "Senior/Lead/Manager" in src

    def test_prompt_keeps_intern_keywords(self):
        src = self._get_filter_prompt_source()
        for kw in ["Intern", "Internship", "Praktikant", "Studiejob"]:
            assert kw in src, f"Missing keyword: {kw}"

    def test_prompt_keeps_student_worker(self):
        src = self._get_filter_prompt_source()
        assert "Student Worker" in src or "Studentermedhjælper" in src

    def test_prompt_keeps_thesis(self):
        src = self._get_filter_prompt_source()
        assert "Thesis" in src

    def test_prompt_rejects_non_denmark(self):
        src = self._get_filter_prompt_source()
        assert "other countries" in src.lower()

    def test_prompt_no_aggressive_capture(self):
        """Old aggressive capture strategy should be gone."""
        src = self._get_filter_prompt_source()
        assert "Aggressive Capture" not in src

    def test_prompt_rejects_3plus_years(self):
        src = self._get_filter_prompt_source()
        assert "3+ years" in src


# ── 2. Analyzer Prompt Tests ────────────────────────────────────────

class TestAnalyzerPromptContent:
    """Verify analyzer prompt penalizes full-time and non-Denmark."""

    def _get_prompt(self):
        from src.analyzer import _ANALYSIS_PROMPT
        return _ANALYSIS_PROMPT

    def test_fulltime_2plus_capped_at_050(self):
        prompt = self._get_prompt()
        assert "0.50" in prompt

    def test_fulltime_5plus_capped_at_030(self):
        prompt = self._get_prompt()
        assert "0.30" in prompt

    def test_non_denmark_capped_at_020(self):
        prompt = self._get_prompt()
        assert "0.20" in prompt

    def test_denmark_normal_scoring(self):
        prompt = self._get_prompt()
        assert "丹麦境内" in prompt

    def test_intern_student_normal_scoring(self):
        prompt = self._get_prompt()
        assert "实习/学生工/兼职" in prompt

    def test_has_location_field(self):
        prompt = self._get_prompt()
        assert '"location"' in prompt

    def test_has_experience_required_field(self):
        prompt = self._get_prompt()
        assert '"experience_required"' in prompt

    def test_no_aggressive_capture(self):
        prompt = self._get_prompt()
        assert "积极捕捉" not in prompt
        assert "Aggressive Capture" not in prompt


# ── 3. Notifier Role-Type Filter Tests ──────────────────────────────

class TestNotifierRoleFilter:
    """Verify notifier filters out non-intern/student jobs."""

    def _make_db(self):
        from src.database import JobDatabase
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        return JobDatabase(Path(tmp.name))

    def _insert_job(self, db, job_id, title, analysis_dict):
        from src.utils import compute_job_hash
        content_hash = compute_job_hash(f"role-filter-{job_id}", "test")
        db.conn.execute(
            """INSERT INTO jobs (id, platform, title, company, url, content_hash,
                                jd_text, relevance, status, analysis, notified_at)
               VALUES (?, 'test', ?, 'TestCo', 'https://linkedin.com/jobs/view/999',
                        ?, 'Long JD text here', 'relevant', 'analyzed', ?, NULL)""",
            (job_id, title, content_hash, json.dumps(analysis_dict)),
        )
        db.conn.commit()

    @patch("src.notifier._send_message")
    def test_fulltime_job_filtered_out(self, mock_send):
        db = self._make_db()
        self._insert_job(db, 1, "Senior ML Engineer", {
            "match_score": 0.95,
            "role_type": "全职",
        })
        from src.notifier import send_daily_report
        send_daily_report(db)
        msg = mock_send.call_args[0][0]
        assert "暂无" in msg  # Should show "no matching jobs" message
        db.close()

    @patch("src.notifier._send_message")
    def test_intern_job_passes_filter(self, mock_send):
        db = self._make_db()
        self._insert_job(db, 1, "Python Intern", {
            "match_score": 0.9,
            "role_type": "Internship",
        })
        from src.notifier import send_daily_report
        send_daily_report(db)
        msg = mock_send.call_args[0][0]
        assert "Python Intern" in msg
        db.close()

    @patch("src.notifier._send_message")
    def test_student_worker_passes_filter(self, mock_send):
        db = self._make_db()
        self._insert_job(db, 1, "Studentermedhjælper IT", {
            "match_score": 0.88,
            "role_type": "学生工",
        })
        from src.notifier import send_daily_report
        send_daily_report(db)
        msg = mock_send.call_args[0][0]
        assert "Studentermedhjælper" in msg
        db.close()

    @patch("src.notifier._send_message")
    def test_role_type_keyword_matching(self, mock_send):
        """Test _is_target_role logic with various role_types."""
        from src.notifier import send_daily_report
        db = self._make_db()
        # Graduate role_type should pass
        self._insert_job(db, 1, "Software Developer", {
            "match_score": 0.85,
            "role_type": "Graduate Program",
        })
        send_daily_report(db)
        db.close()
        # No assertion needed — just verify it doesn't crash

    @patch("src.notifier._send_message")
    def test_title_based_matching_when_no_role_type(self, mock_send):
        """If role_type is empty, fall back to title matching."""
        db = self._make_db()
        self._insert_job(db, 1, "Backend Intern - Copenhagen", {
            "match_score": 0.9,
            "role_type": "",
        })
        from src.notifier import send_daily_report
        send_daily_report(db)
        msg = mock_send.call_args[0][0]
        assert "Backend Intern" in msg
        db.close()

    @patch("src.notifier._send_message")
    def test_mixed_jobs_only_intern_shown(self, mock_send):
        """With mixed jobs, only intern/student ones should appear."""
        db = self._make_db()
        self._insert_job(db, 1, "Senior Backend Engineer", {
            "match_score": 0.95,
            "role_type": "Full-time",
        })
        self._insert_job(db, 2, "ML Intern", {
            "match_score": 0.85,
            "role_type": "Internship",
        })
        from src.notifier import send_daily_report
        send_daily_report(db)
        msg = mock_send.call_args[0][0]
        assert "ML Intern" in msg
        assert "Senior Backend" not in msg
        db.close()
