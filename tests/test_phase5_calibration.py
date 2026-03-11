"""
Phase 5 Tests: Score Calibration

验证:
- analyzer prompt 包含评分锚定段落
- rank_jobs 阈值从 10 改为 15
- JD 信息不足提示语在 prompt 中
"""

import re


class TestPromptCalibration:
    """Test that the analysis prompt includes score anchoring."""

    def _get_prompt(self):
        from src.analyzer import _ANALYSIS_PROMPT
        return _ANALYSIS_PROMPT

    def test_prompt_has_score_anchoring(self):
        prompt = self._get_prompt()
        assert "0.90-1.00" in prompt
        assert "0.75-0.89" in prompt
        assert "0.60-0.74" in prompt
        assert "0.40-0.59" in prompt
        assert "0.00-0.39" in prompt

    def test_prompt_has_perfect_match_description(self):
        prompt = self._get_prompt()
        assert "完美匹配" in prompt

    def test_prompt_warns_about_short_jd(self):
        prompt = self._get_prompt()
        assert "JD信息不足" in prompt
        assert "0.75" in prompt

    def test_prompt_penalizes_fulltime(self):
        prompt = self._get_prompt()
        assert "全职" in prompt
        assert "0.50" in prompt or "0.30" in prompt


class TestRankJobsThreshold:
    """Test that rank_jobs uses top-15 instead of top-10."""

    def test_source_uses_15_threshold(self):
        """Check source code for the threshold value."""
        from pathlib import Path
        src = Path("src/analyzer.py").read_text()

        # Should have 'if len(jobs) <= 15:'
        assert re.search(r"if\s+len\(jobs\)\s*<=\s*15", src)

        # Should have 'ranked[:15]'
        assert "ranked[:15]" in src

    def test_no_old_threshold_10(self):
        """Ensure old threshold of 10 is no longer in rank_jobs."""
        from pathlib import Path
        src = Path("src/analyzer.py").read_text()

        # Extract rank_jobs function body
        match = re.search(r"async def rank_jobs\(.*?\n(.*?)(?=\nasync def |\nclass |\Z)",
                          src, re.DOTALL)
        assert match
        rank_body = match.group(1)

        # Should NOT have old 'ranked[:10]' or '<= 10'
        assert "ranked[:10]" not in rank_body
        assert "<= 10" not in rank_body
