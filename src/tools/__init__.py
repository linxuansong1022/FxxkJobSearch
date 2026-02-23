"""
Tools Layer — 将所有功能模块封装为 ToolSpec

每个 Tool 包含:
1. handler: 实际执行函数 (db, **kwargs) -> dict
2. ToolSpec: 名称 + 描述 + JSON Schema 参数
"""

from __future__ import annotations

import json
import logging

from src.agents.base_agent import ToolSpec

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tool Handlers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def handle_scrape_linkedin(db, keywords: list[str] | None = None) -> dict:
    """LinkedIn / Indeed / Glassdoor 抓取 (Tavily API, fallback to JobSpy)"""
    try:
        from src.scraper_tavily import scrape_tavily
        count = scrape_tavily(db)
        return {"status": "success", "new_jobs": count, "platform": "tavily (linkedin/indeed/glassdoor)"}
    except Exception as e:
        logger.warning(f"Tavily failed, fallback to JobSpy: {e}")
        from src.scraper import scrape_jobspy
        count = scrape_jobspy(db, keywords=keywords) if keywords else scrape_jobspy(db)
        return {"status": "success", "new_jobs": count, "platform": "jobspy-fallback"}


def handle_scrape_thehub(db, **kwargs) -> dict:
    """The Hub 抓取"""
    from src.scraper import scrape_thehub
    count = scrape_thehub(db)
    return {"status": "success", "new_jobs": count, "platform": "thehub"}


def handle_scrape_jobindex(db, **kwargs) -> dict:
    """Jobindex 抓取"""
    try:
        from src.scraper_jobindex import scrape_jobindex
        count = scrape_jobindex(db)
        return {"status": "success", "new_jobs": count, "platform": "jobindex"}
    except Exception as e:
        return {"status": "error", "message": f"Jobindex error: {e}"}


def handle_scrape_company_careers(db, max_companies: int = 5, **kwargs) -> dict:
    """公司官网 Career 页面抓取 (Playwright + LLM)"""
    try:
        from src.scraper_careers import scrape_company_careers
        # 默认只抓前5家作为单次tool调用的测试/演示，避免超时。
        # Orchestrator 如果想要全量抓，应该让 Python 脚本在后台长期跑，而不是在单次 tool 中
        count = scrape_company_careers(db, max_companies=max_companies)
        return {"status": "success", "new_jobs": count, "platform": "company_careers"}
    except Exception as e:
        return {"status": "error", "message": f"Scrape careers error: {e}"}


def handle_get_db_status(db, **kwargs) -> dict:
    """获取数据库当前状态"""
    status_counts = db.get_status_counts()
    relevance_counts = db.get_relevance_counts()
    unscored = db.get_unscored_jobs()
    all_new = db.get_jobs_by_status("new")
    pending_analyze = [j for j in all_new if j.get("relevance") == "relevant"]

    return {
        "status_counts": status_counts,
        "relevance_counts": relevance_counts,
        "pending_filter": len(unscored),
        "pending_analyze": len(pending_analyze),
        "total_jobs_in_db": sum(status_counts.values()),
    }


async def handle_filter_jobs(db, **kwargs) -> dict:
    """过滤不相关职位 (Async)"""
    from src.filter import filter_jobs
    counts = await filter_jobs(db)
    return {"status": "success", **counts}


async def handle_analyze_jobs(db, **kwargs) -> dict:
    """深度分析 JD (Async)"""
    from src.analyzer import analyze_pending_jobs
    count = await analyze_pending_jobs(db)
    return {"status": "success", "analyzed": count}


def handle_fetch_job_detail(db, job_id: int = 0, url: str = "") -> dict:
    """补全单个职位的 JD"""
    import httpx
    from src.utils import clean_html

    if not url:
        return {"status": "error", "message": "URL is required"}
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        text = clean_html(resp.text)[:5000]
        db.update_job_jd(job_id, text)
        return {"status": "success", "jd_length": len(text)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def handle_send_notification(db, **kwargs) -> dict:
    """发送 Telegram 通知"""
    from src.notifier import send_daily_report
    try:
        send_daily_report(db)
        return {"status": "success", "message": "Notification sent"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tool Specifications
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# Scout Agent Tools
SCRAPE_LINKEDIN = ToolSpec(
    name="scrape_linkedin",
    description="通过 Tavily Search API 从 LinkedIn/Indeed/Glassdoor 搜索最新职位。正规 API，无反爬风险。",
    parameters={
        "type": "object",
        "properties": {
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "搜索关键词列表，不传则使用默认配置",
            }
        },
    },
    handler=handle_scrape_linkedin,
)

SCRAPE_THEHUB = ToolSpec(
    name="scrape_thehub",
    description="从 The Hub 抓取丹麦创业公司职位。覆盖本地创业公司生态。",
    parameters={"type": "object", "properties": {}},
    handler=handle_scrape_thehub,
)

SCRAPE_JOBINDEX = ToolSpec(
    name="scrape_jobindex",
    description="从 Jobindex 抓取丹麦本地职位，覆盖更多本地公司。",
    parameters={"type": "object", "properties": {}},
    handler=handle_scrape_jobindex,
)

SCRAPE_COMPANY_CAREERS = ToolSpec(
    name="scrape_company_careers",
    description="使用 Playwright 和大模型，从 company_list.py 配置的丹麦科技公司官网直接抓取最新职位。用于捕获尚未发布到招聘平台的隐蔽职位。",
    parameters={
        "type": "object",
        "properties": {
            "max_companies": {
                "type": "integer",
                "description": "单次抓取最多处理的公司数量，默认为 5，避免单次调用耗时过长",
            }
        },
    },
    handler=handle_scrape_company_careers,
)

# DB Status Tool
GET_DB_STATUS = ToolSpec(
    name="get_db_status",
    description="获取数据库当前状态，包括各状态职位数量、待过滤数、待分析数。用于 Agent 决策下一步操作。",
    parameters={"type": "object", "properties": {}},
    handler=handle_get_db_status,
)

# Filter Tools
FILTER_JOBS = ToolSpec(
    name="filter_jobs",
    description="对未评分的职位做相关性过滤（Rule + LLM 两层），分为 relevant/irrelevant。",
    parameters={"type": "object", "properties": {}},
    handler=handle_filter_jobs,
)

# Analyze Tools
ANALYZE_JOBS = ToolSpec(
    name="analyze_jobs",
    description="用 Gemini 深度分析相关职位的 JD，计算匹配度评分。仅处理 relevance=relevant 且 status=new 的职位。",
    parameters={"type": "object", "properties": {}},
    handler=handle_analyze_jobs,
)

FETCH_JOB_DETAIL = ToolSpec(
    name="fetch_job_detail",
    description="当某个职位的 JD 内容太短（<100字）或缺失时，访问原始链接获取完整 JD 文本。",
    parameters={
        "type": "object",
        "properties": {
            "job_id": {
                "type": "integer",
                "description": "职位数据库 ID",
            },
            "url": {
                "type": "string",
                "description": "职位原始链接",
            },
        },
        "required": ["job_id", "url"],
    },
    handler=handle_fetch_job_detail,
)

# Notification Tools
SEND_NOTIFICATION = ToolSpec(
    name="send_notification",
    description="发送 Telegram 日报通知，推送高匹配度职位。仅在有高分(>0.6)职位时调用。",
    parameters={"type": "object", "properties": {}},
    handler=handle_send_notification,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tool Groups (for different agents)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCOUT_TOOLS = [SCRAPE_LINKEDIN, SCRAPE_THEHUB, SCRAPE_JOBINDEX, SCRAPE_COMPANY_CAREERS, FETCH_JOB_DETAIL]
FILTER_TOOLS = [FILTER_JOBS, GET_DB_STATUS]
ANALYST_TOOLS = [ANALYZE_JOBS, FETCH_JOB_DETAIL, GET_DB_STATUS]
NOTIFIER_TOOLS = [SEND_NOTIFICATION, GET_DB_STATUS]
ALL_TOOLS = [SCRAPE_LINKEDIN, SCRAPE_THEHUB, SCRAPE_JOBINDEX, SCRAPE_COMPANY_CAREERS,
             GET_DB_STATUS, FILTER_JOBS, ANALYZE_JOBS, FETCH_JOB_DETAIL, SEND_NOTIFICATION]
