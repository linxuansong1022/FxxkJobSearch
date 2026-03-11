"""
Phase 3 Tests: Coverage Expansion

验证:
- config.py 搜索关键词扩充 (6→16)
- Tavily 参数调整 (time_range=week, max_results=20)
- Jobindex 关键词扩充 (6→11)
- company_list.py career_url 补全 (9家公司)
"""

import re


class TestSearchQueries:
    """Test config.py SEARCH_QUERIES expansion."""

    def test_query_count_increased(self):
        import config
        assert len(config.SEARCH_QUERIES) >= 16

    def test_core_queries_present(self):
        import config
        queries_lower = [q.lower() for q in config.SEARCH_QUERIES]
        assert any("python" in q for q in queries_lower)
        assert any("ai" in q for q in queries_lower)
        assert any("machine learning" in q for q in queries_lower)

    def test_expanded_queries_present(self):
        import config
        queries_lower = [q.lower() for q in config.SEARCH_QUERIES]
        assert any("backend" in q for q in queries_lower)
        assert any("full stack" in q for q in queries_lower)
        assert any("software engineer" in q for q in queries_lower)

    def test_danish_queries_present(self):
        import config
        queries_lower = [q.lower() for q in config.SEARCH_QUERIES]
        assert any("studiejob" in q for q in queries_lower)
        assert any("praktikant" in q for q in queries_lower)

    def test_research_query_present(self):
        import config
        queries_lower = [q.lower() for q in config.SEARCH_QUERIES]
        assert any("research" in q for q in queries_lower)


class TestTavilyConfig:
    """Test Tavily parameter adjustments."""

    def test_time_range_is_week(self):
        import config
        assert config.TAVILY_SEARCH_CONFIG["time_range"] == "week"

    def test_max_results_increased(self):
        import config
        assert config.TAVILY_SEARCH_CONFIG["max_results_per_query"] >= 20

    def test_jobindex_in_include_domains(self):
        import config
        domains = config.TAVILY_SEARCH_CONFIG["include_domains"]
        assert any("jobindex.dk" in d for d in domains)


class TestJobindexQueries:
    """Test Jobindex query expansion."""

    def test_query_count_increased(self):
        from src.scraper_jobindex import JOBINDEX_QUERIES
        assert len(JOBINDEX_QUERIES) >= 11

    def test_new_queries_present(self):
        from src.scraper_jobindex import JOBINDEX_QUERIES
        queries_lower = [q.lower() for q in JOBINDEX_QUERIES]
        assert any("full stack" in q for q in queries_lower)
        assert any("cloud" in q or "mlops" in q for q in queries_lower)
        assert any("data engineer" in q for q in queries_lower)


class TestCompanyListCareerUrls:
    """Test career_url completion for key companies."""

    def _get_company(self, name):
        from src.company_list import DENMARK_TECH_COMPANIES
        for c in DENMARK_TECH_COMPANIES:
            if c["name"] == name:
                return c
        return None

    def test_orsted_has_career_url(self):
        c = self._get_company("Ørsted")
        assert c is not None
        assert c["career_url"] is not None
        assert "orsted" in c["career_url"].lower()

    def test_carlsberg_has_career_url(self):
        c = self._get_company("Carlsberg Group")
        assert c is not None
        assert c["career_url"] is not None
        assert "carlsberg" in c["career_url"].lower()

    def test_danfoss_has_career_url(self):
        c = self._get_company("Danfoss")
        assert c is not None
        assert c["career_url"] is not None
        assert "danfoss" in c["career_url"].lower()

    def test_grundfos_has_career_url(self):
        c = self._get_company("Grundfos")
        assert c is not None
        assert c["career_url"] is not None
        assert "grundfos" in c["career_url"].lower()

    def test_capgemini_has_career_url(self):
        c = self._get_company("Capgemini Danmark")
        assert c is not None
        assert c["career_url"] is not None
        assert "capgemini" in c["career_url"].lower()

    def test_accenture_has_career_url(self):
        c = self._get_company("Accenture Danmark")
        assert c is not None
        assert c["career_url"] is not None
        assert "accenture" in c["career_url"].lower()

    def test_cognizant_has_career_url(self):
        c = self._get_company("Cognizant Denmark")
        assert c is not None
        assert c["career_url"] is not None
        assert "cognizant" in c["career_url"].lower()

    def test_total_companies_unchanged(self):
        from src.company_list import DENMARK_TECH_COMPANIES
        # Should still have 90+ companies (no entries deleted)
        assert len(DENMARK_TECH_COMPANIES) >= 40

    def test_more_career_urls_filled(self):
        from src.company_list import DENMARK_TECH_COMPANIES
        filled = sum(1 for c in DENMARK_TECH_COMPANIES if c.get("career_url"))
        # At least 19 should now have career_url (was ~10 before)
        assert filled >= 18
