"""
Phase 1 Tests — 数据质量守门

测试:
1. URL 过滤: 聚合页 URL 被拒绝，详情页 URL 被放行
2. 标题检测: 聚合页标题被拒绝，正常岗位标题被放行
3. 数据库入库校验: 聚合标题不入库
"""

import tempfile
from pathlib import Path

import pytest

from src.scraper_tavily import _is_job_detail_url, _is_aggregate_title
from src.database import JobDatabase
from src.utils import compute_job_hash


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: _is_job_detail_url
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestIsJobDetailUrl:
    """URL 过滤测试"""

    # --- Indeed ---
    def test_indeed_viewjob_accepted(self):
        url = "https://dk.indeed.com/viewjob?jk=abc123&from=search"
        assert _is_job_detail_url(url) is True

    def test_indeed_rc_clk_accepted(self):
        url = "https://dk.indeed.com/rc/clk?jk=abc123"
        assert _is_job_detail_url(url) is True

    def test_indeed_search_page_rejected(self):
        url = "https://dk.indeed.com/jobs?q=student+intern&l=Denmark"
        assert _is_job_detail_url(url) is False

    def test_indeed_q_format_rejected(self):
        url = "https://dk.indeed.com/q-student-intern-l-denmark-jobs.html"
        assert _is_job_detail_url(url) is False

    def test_indeed_pagead_accepted(self):
        url = "https://dk.indeed.com/pagead/clk?mo=r&ad=-123"
        assert _is_job_detail_url(url) is True

    # --- LinkedIn ---
    def test_linkedin_jobs_view_accepted(self):
        url = "https://www.linkedin.com/jobs/view/3847291234"
        assert _is_job_detail_url(url) is True

    def test_linkedin_jobs_view_slug_accepted(self):
        url = "https://www.linkedin.com/jobs/view/python-intern-at-novo-nordisk-3847291234"
        assert _is_job_detail_url(url) is True

    def test_linkedin_jobs_search_rejected(self):
        url = "https://www.linkedin.com/jobs/search/?keywords=python+intern"
        assert _is_job_detail_url(url) is False

    def test_linkedin_jobs_collections_rejected(self):
        """collections 不再通过 — 通常是搜索结果页"""
        url = "https://www.linkedin.com/jobs/collections/recommended/"
        assert _is_job_detail_url(url) is False

    # --- Glassdoor ---
    def test_glassdoor_job_listing_accepted(self):
        url = "https://www.glassdoor.com/job-listing/python-intern-JV_IC123.htm"
        assert _is_job_detail_url(url) is True

    def test_glassdoor_search_page_rejected(self):
        """Glassdoor 搜索结果页 /Job/ (大写J) 不再通过"""
        url = "https://www.glassdoor.com/Job/student-intern-jobs-SRCH_KO0,14.htm"
        assert _is_job_detail_url(url) is False

    def test_glassdoor_srch_city_rejected(self):
        """Glassdoor SRCH_ 城市搜索页"""
        url = "https://www.glassdoor.com/Job/copenhagen-ai-engineer-jobs-SRCH_IL.0,10_IC2218704_KO11,22.htm"
        assert _is_job_detail_url(url) is False

    def test_glassdoor_srch_country_rejected(self):
        """Glassdoor SRCH_ 国家搜索页"""
        url = "https://www.glassdoor.com/Job/denmark-research-assistant-jobs-SRCH_IL.0,7_IN63_KO8,26.htm"
        assert _is_job_detail_url(url) is False

    # --- Wellfound ---
    def test_wellfound_specific_job_accepted(self):
        url = "https://wellfound.com/jobs/company-name/python-intern"
        assert _is_job_detail_url(url) is True

    def test_wellfound_jobs_root_rejected(self):
        url = "https://wellfound.com/jobs/"
        assert _is_job_detail_url(url) is False

    # --- Jobindex ---
    def test_jobindex_jobannonce_accepted(self):
        url = "https://www.jobindex.dk/jobannonce/12345/python-intern"
        assert _is_job_detail_url(url) is True

    def test_jobindex_search_rejected(self):
        url = "https://www.jobindex.dk/jobsoegning?q=python+intern"
        assert _is_job_detail_url(url) is False

    # --- Unknown platform ---
    def test_unknown_platform_accepted(self):
        url = "https://random-company.com/careers/python-intern"
        assert _is_job_detail_url(url) is True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: _is_aggregate_title
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestIsAggregateTitle:
    """聚合页标题检测"""

    def test_number_jobs_in_location(self):
        assert _is_aggregate_title("84 student intern Jobs in Denmark") is True

    def test_number_plus_jobs(self):
        assert _is_aggregate_title("75+ Python Developer jobs, ansættelse 22. februar 2026") is True

    def test_data_science_jobs_i_kobenhavn(self):
        assert _is_aggregate_title("Data Science Student jobs i København") is True

    def test_top_n_jobs(self):
        assert _is_aggregate_title("Top 10 AI intern jobs in Copenhagen") is True

    def test_number_positions_near(self):
        assert _is_aggregate_title("92 research assistant positions near Denmark") is True

    def test_number_jobs_no_location(self):
        """'17 Ai engineer jobs' — 数字 + jobs 但没有地点"""
        assert _is_aggregate_title("17 Ai engineer jobs") is True

    def test_bare_topic_jobs(self):
        """'Research assistant Jobs' — 纯主题 + Jobs，没有数字也没有地点"""
        assert _is_aggregate_title("Research assistant Jobs") is True

    def test_number_jobs_with_date(self):
        assert _is_aggregate_title("92 research assistant Jobs in Denmark, February 2026") is True

    # --- 正常岗位标题不应被拦截 ---
    def test_normal_intern_title(self):
        assert _is_aggregate_title("Python AI Intern") is False

    def test_normal_student_worker(self):
        assert _is_aggregate_title("Student Worker - Data Science") is False

    def test_normal_with_company(self):
        assert _is_aggregate_title("Machine Learning Intern at Novo Nordisk") is False

    def test_normal_danish(self):
        assert _is_aggregate_title("Studentermedhjælper inden for IT") is False

    def test_title_with_number_but_not_aggregate(self):
        """标题中有数字但不是聚合模式"""
        assert _is_aggregate_title("Junior Developer - 2 years experience") is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Database aggregate title rejection
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDatabaseAggregateRejection:
    """数据库入库校验：聚合标题不入库"""

    @pytest.fixture
    def db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        database = JobDatabase(db_path)
        yield database
        database.close()
        db_path.unlink(missing_ok=True)

    def test_aggregate_title_rejected(self, db):
        """聚合标题应被拒绝入库"""
        job_data = {
            "platform": "indeed",
            "platform_id": "test1",
            "title": "84 student intern Jobs in Denmark, February 2026",
            "company": "Indeed",
            "url": "https://indeed.com/jobs?q=student",
            "content_hash": compute_job_hash("Indeed", "84 student intern Jobs"),
            "jd_text": "some content",
            "posted_at": None,
        }
        result = db.insert_job(job_data)
        assert result is False

    def test_normal_title_accepted(self, db):
        """正常职位标题应被接受"""
        job_data = {
            "platform": "linkedin",
            "platform_id": "test2",
            "title": "Python AI Intern",
            "company": "Novo Nordisk",
            "url": "https://linkedin.com/jobs/view/12345",
            "content_hash": compute_job_hash("Novo Nordisk", "Python AI Intern"),
            "jd_text": "We are looking for...",
            "posted_at": None,
        }
        result = db.insert_job(job_data)
        assert result is True

    def test_number_plus_jobs_rejected(self, db):
        """75+ jobs 格式也应被拒绝"""
        job_data = {
            "platform": "glassdoor",
            "platform_id": "test3",
            "title": "75+ Python Developer jobs in Copenhagen",
            "company": "Glassdoor",
            "url": "https://glassdoor.com/Job/search",
            "content_hash": compute_job_hash("Glassdoor", "75+ Python Developer"),
            "jd_text": "content",
            "posted_at": None,
        }
        result = db.insert_job(job_data)
        assert result is False

    def test_duplicate_hash_rejected(self, db):
        """重复 content_hash 应被拒绝 (原有逻辑)"""
        job_data = {
            "platform": "linkedin",
            "platform_id": "test4",
            "title": "ML Engineer Intern",
            "company": "Maersk",
            "url": "https://linkedin.com/jobs/view/99999",
            "content_hash": compute_job_hash("Maersk", "ML Engineer Intern"),
            "jd_text": "JD content",
            "posted_at": None,
        }
        assert db.insert_job(job_data) is True
        assert db.insert_job(job_data) is False  # duplicate
