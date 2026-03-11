"""
Phase 2 Tests: JD Backfill

验证:
- jd_fetcher 模块的 HTML 解析和平台选择器
- analyzer 的 JD 长度门槛 (200 chars)
- main.py pipeline 中 backfill 步骤的集成
- tools/__init__.py 中 backfill tool 的注册
"""

import asyncio
import re
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: jd_fetcher._fetch_single_jd
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFetchViaHttpx:
    """Test _fetch_via_httpx HTML parsing per platform selector."""

    @pytest.mark.asyncio
    async def test_indeed_selector(self):
        from src.jd_fetcher import _fetch_via_httpx

        html = """<html><body>
        <div id="jobDescriptionText">
            <p>We are looking for a Python developer with expertise in
            machine learning and deep learning. You will work on
            cutting-edge NLP projects at our Copenhagen office.
            Requirements: 3+ years Python, PyTorch, TensorFlow.</p>
        </div>
        </body></html>"""

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _fetch_via_httpx(mock_client, "https://indeed.com/viewjob?jk=123", "indeed")
        assert result is not None
        assert "Python developer" in result
        assert len(result) > 50

    @pytest.mark.asyncio
    async def test_linkedin_selector(self):
        from src.jd_fetcher import _fetch_via_httpx

        html = """<html><body>
        <div class="description__text">
            <p>Senior Backend Engineer at Novo Nordisk. Build scalable
            microservices using Python, FastAPI, and Kubernetes. Join our
            digital transformation team in Copenhagen. Minimum 2 years
            of experience required.</p>
        </div>
        </body></html>"""

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _fetch_via_httpx(mock_client, "https://linkedin.com/jobs/view/123", "linkedin")
        assert result is not None
        assert "Backend Engineer" in result

    @pytest.mark.asyncio
    async def test_fallback_to_main_element(self):
        from src.jd_fetcher import _fetch_via_httpx

        html = """<html><body>
        <main>
            <h1>Data Analyst Intern</h1>
            <p>Amazing opportunity to work with big data pipelines
            using Python and SQL. You'll analyze customer behavior
            patterns and build dashboards. Located in Aarhus.</p>
        </main>
        </body></html>"""

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _fetch_via_httpx(mock_client, "https://example.com/job/1", "unknown")
        assert result is not None
        assert "Data Analyst" in result

    @pytest.mark.asyncio
    async def test_non_200_returns_none(self):
        from src.jd_fetcher import _fetch_via_httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _fetch_via_httpx(mock_client, "https://indeed.com/viewjob?jk=999", "indeed")
        assert result is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        from src.jd_fetcher import _fetch_via_httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection timeout"))

        result = await _fetch_via_httpx(mock_client, "https://indeed.com/viewjob?jk=123", "indeed")
        assert result is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: backfill_missing_jds integration with DB
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBackfillMissingJds:
    """Test backfill_missing_jds with an in-memory database."""

    def _make_db(self):
        """Create an in-memory JobDatabase for testing."""
        from src.database import JobDatabase
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db = JobDatabase(Path(tmp.name))
        return db

    def _insert_test_job(self, db, job_id, platform, url, jd_text=None, relevance="relevant", status="new"):
        """Insert a test job directly via SQL."""
        from src.utils import compute_job_hash
        content_hash = compute_job_hash(f"test-{job_id}", platform)
        db.conn.execute(
            """INSERT INTO jobs (id, platform, title, company, url, content_hash, jd_text, relevance, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, platform, f"Test Job {job_id}", "TestCo", url, content_hash, jd_text, relevance, status),
        )
        db.conn.commit()

    @pytest.mark.asyncio
    async def test_backfill_updates_short_jd(self):
        from src.jd_fetcher import backfill_missing_jds

        db = self._make_db()
        # Use a non-JS-heavy platform so httpx path is tried first
        self._insert_test_job(db, 1, "jobindex", "https://jobindex.dk/jobannonce/123", jd_text="Short")

        long_jd = "A " * 150  # 300 chars, above MIN_JD_LENGTH

        html = f'<html><body><div class="jobad-content">{long_jd}</div></body></html>'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("src.jd_fetcher.httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_resp)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client_instance

            count = await backfill_missing_jds(db)

        assert count == 1
        row = db.conn.execute("SELECT jd_text FROM jobs WHERE id = 1").fetchone()
        assert row is not None
        assert len(row["jd_text"]) >= 200

        db.close()

    @pytest.mark.asyncio
    async def test_backfill_skips_already_long_jd(self):
        from src.jd_fetcher import backfill_missing_jds

        db = self._make_db()
        long_jd = "Existing complete JD content. " * 20  # ~600 chars
        self._insert_test_job(db, 1, "linkedin", "https://linkedin.com/jobs/view/123", jd_text=long_jd)

        count = await backfill_missing_jds(db)
        assert count == 0

        db.close()

    @pytest.mark.asyncio
    async def test_backfill_skips_irrelevant_jobs(self):
        from src.jd_fetcher import backfill_missing_jds

        db = self._make_db()
        self._insert_test_job(db, 1, "indeed", "https://indeed.com/viewjob?jk=123",
                              jd_text="Short", relevance="irrelevant")

        count = await backfill_missing_jds(db)
        assert count == 0

        db.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: analyzer JD length threshold
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestAnalyzerThreshold:
    """Test that analyzer rejects JDs shorter than 200 chars."""

    @pytest.mark.asyncio
    async def test_short_jd_returns_none(self):
        from src.analyzer import analyze_single_jd
        client = MagicMock()
        semaphore = asyncio.Semaphore(1)
        result = await analyze_single_jd(client, "Too short JD text.", semaphore)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_jd_returns_none(self):
        from src.analyzer import analyze_single_jd
        client = MagicMock()
        semaphore = asyncio.Semaphore(1)
        result = await analyze_single_jd(client, "", semaphore)
        assert result is None

    @pytest.mark.asyncio
    async def test_none_jd_returns_none(self):
        from src.analyzer import analyze_single_jd
        client = MagicMock()
        semaphore = asyncio.Semaphore(1)
        result = await analyze_single_jd(client, None, semaphore)
        assert result is None

    @pytest.mark.asyncio
    async def test_199_chars_returns_none(self):
        from src.analyzer import analyze_single_jd
        client = MagicMock()
        semaphore = asyncio.Semaphore(1)
        jd = "x" * 199
        result = await analyze_single_jd(client, jd, semaphore)
        assert result is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Backfill tool registration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBackfillToolRegistration:
    """Verify backfill_jds tool is registered in tools/__init__.py source."""

    def _read_tools_source(self):
        from pathlib import Path
        return Path("src/tools/__init__.py").read_text()

    def test_backfill_in_analyst_tools(self):
        src = self._read_tools_source()
        assert "BACKFILL_JDS" in src
        assert "ANALYST_TOOLS" in src
        # Verify BACKFILL_JDS is in ANALYST_TOOLS list
        import re
        match = re.search(r"ANALYST_TOOLS\s*=\s*\[([^\]]+)\]", src)
        assert match, "ANALYST_TOOLS definition not found"
        assert "BACKFILL_JDS" in match.group(1)

    def test_backfill_in_all_tools(self):
        src = self._read_tools_source()
        match = re.search(r"ALL_TOOLS\s*=\s*\[([^\]]+)\]", src)
        assert match, "ALL_TOOLS definition not found"
        assert "BACKFILL_JDS" in match.group(1)

    def test_backfill_spec_defined(self):
        src = self._read_tools_source()
        assert 'name="backfill_jds"' in src
        assert "handle_backfill_jds" in src
