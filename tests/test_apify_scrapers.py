"""
Apify Scrapers Tests

覆盖:
1. item → job_data 字段映射(LinkedIn + Indeed,以及 schema drift 兜底)
2. compute_job_hash 用于去重
3. 重复 (company, title) 第二次插入返回 False
4. max_items_total 预算硬阀生效

所有测试都用 mock 模拟 Apify client,不发真实 API 请求。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.database import JobDatabase
from src.scraper_apify_indeed import (
    _build_run_input as _indeed_build_run_input,
    _map_item_to_job as _map_indeed_item,
    scrape_apify_indeed,
)
from src.scraper_apify_linkedin import (
    _build_run_input as _linkedin_build_run_input,
    _map_item_to_job as _map_linkedin_item,
    scrape_apify_linkedin,
)
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
# LinkedIn item mapping
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestLinkedInMapping:
    def test_canonical_fields_mapped(self):
        item = {
            "id": "12345",
            "title": "Python AI Intern",
            "companyName": "Novo Nordisk",
            "link": "https://linkedin.com/jobs/view/12345",
            "descriptionText": "We are looking for a Python intern...",
            "postedAt": "2026-04-01",
        }
        job = _map_linkedin_item(item)
        assert job is not None
        assert job["platform"] == "apify_linkedin"
        assert job["platform_id"] == "12345"
        assert job["title"] == "Python AI Intern"
        assert job["company"] == "Novo Nordisk"
        assert job["url"] == "https://linkedin.com/jobs/view/12345"
        assert job["posted_at"] == "2026-04-01"
        assert job["jd_text"]
        assert job["content_hash"] == compute_job_hash(
            "Novo Nordisk", "Python AI Intern"
        )

    def test_alternate_field_names_handled(self):
        """Schema drift: actor 可能用 jobTitle/company/url 等替代字段。"""
        item = {
            "jobTitle": "Backend Engineer Intern",
            "company": "Maersk",
            "url": "https://linkedin.com/jobs/view/999",
            "description": "Join our team",
        }
        job = _map_linkedin_item(item)
        assert job is not None
        assert job["title"] == "Backend Engineer Intern"
        assert job["company"] == "Maersk"
        assert job["posted_at"] is None  # 没给就是 None,不是空字符串

    def test_missing_title_skipped(self):
        item = {"companyName": "Foo", "link": "https://x"}
        assert _map_linkedin_item(item) is None

    def test_missing_company_skipped(self):
        item = {"title": "Bar", "link": "https://x"}
        assert _map_linkedin_item(item) is None

    def test_run_input_uses_query_and_max(self):
        run_input = _linkedin_build_run_input("Python Intern", 25)
        assert run_input["title"] == "Python Intern"
        assert run_input["rows"] == 25
        # location 必须从 config 来
        assert run_input["location"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Indeed item mapping
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestIndeedMapping:
    def test_canonical_fields_mapped(self):
        item = {
            "id": "abc",
            "positionName": "Data Engineer Student",
            "company": "Pleo",
            "url": "https://dk.indeed.com/viewjob?jk=abc",
            "description": "Help us build pipelines",
            "postingDateParsed": "2026-04-02T10:00:00Z",
        }
        job = _map_indeed_item(item)
        assert job is not None
        assert job["platform"] == "apify_indeed"
        assert job["title"] == "Data Engineer Student"
        assert job["company"] == "Pleo"
        assert job["posted_at"] == "2026-04-02T10:00:00Z"
        assert job["content_hash"] == compute_job_hash("Pleo", "Data Engineer Student")

    def test_alternate_field_names_handled(self):
        item = {
            "title": "ML Intern",
            "companyName": "Trifork",
            "externalApplyLink": "https://dk.indeed.com/viewjob?jk=zzz",
        }
        job = _map_indeed_item(item)
        assert job is not None
        assert job["url"].startswith("https://dk.indeed.com")

    def test_missing_required_fields_skipped(self):
        assert _map_indeed_item({"positionName": "x"}) is None
        assert _map_indeed_item({"company": "y"}) is None

    def test_run_input_country_and_location(self):
        run_input = _indeed_build_run_input("ML Intern", 10)
        assert run_input["position"] == "ML Intern"
        assert run_input["country"] == "DK"
        assert run_input["maxItems"] == 10


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DB integration: dedup
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDedup:
    def test_same_company_title_inserted_once(self, db):
        item = {
            "id": "1",
            "title": "Python Intern",
            "companyName": "Acme",
            "link": "https://linkedin.com/jobs/view/1",
        }
        job = _map_linkedin_item(item)
        assert db.insert_job(job) is True

        # 同一岗位换个 url / id 重复抓到,应该被去重
        item2 = {
            "id": "2",
            "title": "Python Intern",
            "companyName": "Acme",
            "link": "https://linkedin.com/jobs/view/2",
        }
        job2 = _map_linkedin_item(item2)
        assert db.insert_job(job2) is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Budget cap (max_items_total)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestBudgetCap:
    """验证 max_items_total 预算硬阀:不管 actor 返回多少,最多消耗 N 条。"""

    def _make_fake_items(self, n: int, prefix: str) -> list[dict]:
        return [
            {
                "id": f"{prefix}-{i}",
                "title": f"Role {i}",
                "companyName": f"Company {i}",
                "link": f"https://linkedin.com/jobs/view/{i}",
            }
            for i in range(n)
        ]

    def test_linkedin_respects_max_items_total(self, db, monkeypatch):
        """限制只有 5 个 query,每个最多 3 条,总上限 7 → 最多消耗 7 条。"""
        import sys
        import types

        import config

        monkeypatch.setattr(config, "APIFY_API_TOKEN", "fake-token")
        monkeypatch.setattr(
            config,
            "APIFY_CONFIG",
            {
                "linkedin_actor": "bebity/linkedin-jobs-scraper",
                "indeed_actor": "misceres/indeed-scraper",
                "max_items_per_query": 3,
                "max_items_total": 7,
                "time_range": "past24Hours",
                "country": "DK",
                "location": "Denmark",
                "run_timeout_secs": 60,
            },
        )
        monkeypatch.setattr(
            config,
            "SEARCH_QUERIES",
            ["q1", "q2", "q3", "q4", "q5"],
        )

        # 用一个 fake apify_client 模块替换真实包(scraper 是惰性 import)
        mock_client = MagicMock()
        mock_client.actor.return_value.call.return_value = {
            "defaultDatasetId": "ds-1"
        }

        def fresh_iter(*_args, **_kwargs):
            return iter(self._make_fake_items(100, "linkedin"))

        mock_client.dataset.return_value.iterate_items.side_effect = fresh_iter

        fake_module = types.ModuleType("apify_client")
        fake_module.ApifyClient = MagicMock(return_value=mock_client)
        monkeypatch.setitem(sys.modules, "apify_client", fake_module)

        new_count = scrape_apify_linkedin(db)

        # 最多 7 条进入消耗(虽然 mock 每个 query 返回 100 条),所以最多 7 条入库
        assert new_count <= 7
        # 至少要插入一些(如果 = 0 说明逻辑跑空了)
        assert new_count > 0
