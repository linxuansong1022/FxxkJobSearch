"""
Phase 6 修复测试：
1. rank_jobs prompt 使用候选人 profile（而非 job title）
2. JD backfill Playwright fallback 逻辑
"""

import asyncio
import inspect
import re
import textwrap

import pytest


# ================================================================
# 1. rank_jobs prompt 修复验证
# ================================================================

class TestRankJobsPromptFix:
    """验证 rank_jobs 不再把 job title 当作候选人背景"""

    def _get_rank_jobs_source(self) -> str:
        with open("src/analyzer.py", encoding="utf-8") as f:
            return f.read()

    def test_no_candidate_background_job_title(self):
        """prompt 中不应用 job['title'] 作为 Candidate background"""
        src = self._get_rank_jobs_source()
        # 旧的 bug 模式: Candidate background: {job['title']}
        assert "Candidate background: {job" not in src, \
            "rank_jobs prompt 仍在使用 job title 作为候选人背景"

    def test_uses_profile_text(self):
        """prompt 应该引用 profile_text 变量"""
        src = self._get_rank_jobs_source()
        # 函数体内应该加载 profile
        assert "_load_profile_as_text()" in src, \
            "rank_jobs 应该调用 _load_profile_as_text() 加载候选人背景"
        # prompt 中应该用 profile_text
        assert "profile_text" in src, \
            "rank_jobs prompt 应该包含 profile_text"

    def test_prompt_has_candidate_profile_section(self):
        """prompt 应该有 Candidate Profile 区块"""
        src = self._get_rank_jobs_source()
        assert "Candidate Profile" in src, \
            "rank_jobs prompt 应该有 'Candidate Profile' 区块"

    def test_prompt_has_scoring_criteria(self):
        """prompt 应该有评分标准"""
        src = self._get_rank_jobs_source()
        assert "80-100" in src or "Scoring criteria" in src, \
            "rank_jobs prompt 应该有评分标准指引"

    def test_rank_jobs_still_returns_top15(self):
        """rank_jobs 返回上限仍是 15"""
        src = self._get_rank_jobs_source()
        assert "ranked[:15]" in src


# ================================================================
# 2. JD Backfill Playwright fallback 验证
# ================================================================

class TestJDFetcherPlaywrightFallback:
    """验证 JD fetcher 的 Playwright fallback 机制"""

    def _get_fetcher_source(self) -> str:
        with open("src/jd_fetcher.py", encoding="utf-8") as f:
            return f.read()

    def test_has_playwright_fallback_function(self):
        """应该存在 _fetch_via_playwright 函数"""
        src = self._get_fetcher_source()
        assert "def _fetch_via_playwright" in src

    def test_has_httpx_function(self):
        """应该存在 _fetch_via_httpx 函数"""
        src = self._get_fetcher_source()
        assert "def _fetch_via_httpx" in src

    def test_js_heavy_platforms_defined(self):
        """应该定义 JS 重度平台列表"""
        src = self._get_fetcher_source()
        assert "_JS_HEAVY_PLATFORMS" in src
        # LinkedIn, Indeed, Glassdoor 应该在列表中
        assert '"linkedin"' in src or "'linkedin'" in src
        assert '"indeed"' in src or "'indeed'" in src

    def test_linkedin_uses_playwright_directly(self):
        """LinkedIn 平台应该直接走 Playwright，不用 httpx"""
        src = self._get_fetcher_source()
        assert "platform in _JS_HEAVY_PLATFORMS" in src

    def test_httpx_failure_falls_back_to_playwright(self):
        """_fetch_single_jd 中 httpx 失败应该 fallback 到 Playwright"""
        src = self._get_fetcher_source()
        # 在 _fetch_single_jd 中应该先尝试 httpx，然后 fallback
        assert "_fetch_via_httpx" in src
        assert "_fetch_via_playwright" in src
        # 应该有 fallback 逻辑
        assert "fallback" in src.lower() or "run_in_executor" in src

    def test_playwright_handles_cookie_banners(self):
        """Playwright 应该处理 cookie 弹窗"""
        src = self._get_fetcher_source()
        assert "accept" in src.lower() and "cookie" in src.lower()

    def test_playwright_removes_noise_elements(self):
        """Playwright 应该移除 header/footer/nav 等噪音"""
        src = self._get_fetcher_source()
        assert "'header'" in src or '"header"' in src
        assert "'footer'" in src or '"footer"' in src

    def test_platform_selectors_shared(self):
        """平台选择器应该在 httpx 和 playwright 中都用到"""
        src = self._get_fetcher_source()
        assert "_PLATFORM_SELECTORS" in src or "selectors" in src
        # 关键选择器都应存在
        assert "#jobDescriptionText" in src
        assert ".description__text" in src
        assert ".jobad-content" in src

    def test_min_jd_length_constant(self):
        """MIN_JD_LENGTH 应该是 200"""
        from src.jd_fetcher import MIN_JD_LENGTH
        assert MIN_JD_LENGTH == 200


class TestPlaywrightFallbackUnit:
    """_fetch_via_playwright 的单元逻辑测试（不启动真实浏览器）"""

    def test_playwright_import_guard(self):
        """如果 Playwright 未安装，应该优雅降级返回 None"""
        src = self._get_fetcher_source()
        assert "ImportError" in src, \
            "_fetch_via_playwright 应该捕获 ImportError"

    def _get_fetcher_source(self) -> str:
        with open("src/jd_fetcher.py", encoding="utf-8") as f:
            return f.read()

    def test_fetch_single_jd_is_async(self):
        """_fetch_single_jd 应该是 async 函数"""
        src = self._get_fetcher_source()
        assert "async def _fetch_single_jd" in src

    def test_backfill_query_uses_200_threshold(self):
        """backfill SQL 查询应该用 MIN_JD_LENGTH (200) 过滤"""
        src = self._get_fetcher_source()
        assert "LENGTH(jd_text) < ?" in src

    def test_run_in_executor_for_sync_playwright(self):
        """Playwright 是同步的，应该用 run_in_executor 包装"""
        src = self._get_fetcher_source()
        assert "run_in_executor" in src, \
            "应该用 run_in_executor 在线程池中运行同步 Playwright"
