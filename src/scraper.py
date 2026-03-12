"""
多平台职位采集模块

数据源：
1. LinkedIn + Indeed — 通过 python-jobspy 库聚合采集
2. The Hub (thehub.io) — 通过 httpx 直调 REST API

采集后写入 SQLite，由 filter.py 做相关性过滤。
"""

import logging
import time
import random

import httpx
import pandas as pd
from jobspy import scrape_jobs

import config
from src.database import JobDatabase
from src.utils import compute_job_hash, clean_html

logger = logging.getLogger(__name__)


# ============================================================
# 1. JobSpy 采集（LinkedIn + Indeed）
# ============================================================

def scrape_jobspy(db: JobDatabase) -> int:
    """
    使用 JobSpy 从 LinkedIn 和 Indeed 采集职位。
    关键词之间随机延迟 2-5 秒。
    """
    new_count = 0

    for query in config.SEARCH_QUERIES:
        logger.info(f"JobSpy 采集: query='{query}'")
        try:
            jobs_df: pd.DataFrame = scrape_jobs(
                site_name=["linkedin", "indeed"],
                search_term=query,
                location=config.JOBSPY_CONFIG["location"],
                results_wanted=config.JOBSPY_CONFIG["results_wanted"],
                hours_old=config.JOBSPY_CONFIG["hours_old"],
                country_indeed=config.JOBSPY_CONFIG["country_indeed"],
            )
        except Exception as e:
            logger.warning(f"JobSpy 采集失败 (query='{query}'): {e}")
            continue

        for _, row in jobs_df.iterrows():
            title = str(row.get("title", ""))
            company = str(row.get("company_name", "") or row.get("company", ""))
            if not title or not company:
                continue

            # 提取发布时间
            posted_at = None
            date_posted = row.get("date_posted")
            if pd.notna(date_posted):
                posted_at = str(date_posted)

            job_data = {
                "platform": str(row.get("site", "unknown")),
                "platform_id": str(row.get("id", "")),
                "title": title,
                "company": company,
                "url": str(row.get("job_url", "")),
                "content_hash": compute_job_hash(company, title),
                "jd_text": clean_html(str(row.get("description", ""))),
                "posted_at": posted_at,
            }

            if db.insert_job(job_data):
                new_count += 1

        delay = random.uniform(2, 5)
        time.sleep(delay)

    return new_count


# ============================================================
# 2. The Hub API 采集
# ============================================================

def scrape_thehub(db: JobDatabase) -> int:
    """
    通过 The Hub REST API 采集丹麦地区的职位。
    The Hub 返回标准 JSON，包含 publishedAt 发布时间。
    """
    new_count = 0
    hub_config = config.THEHUB_CONFIG

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    for keyword in hub_config["keywords"]:
        logger.info(f"The Hub 采集: keyword='{keyword}'")
        params = {**hub_config["params"], "search": keyword}

        try:
            resp = httpx.get(
                hub_config["base_url"],
                params=params,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"The Hub 采集失败 (keyword='{keyword}'): {e}")
            continue

        jobs_list = data.get("docs", data.get("results", data if isinstance(data, list) else []))

        for job in jobs_list:
            title = job.get("title", "")
            company_obj = job.get("company", {})
            company = company_obj.get("name", "") if isinstance(company_obj, dict) else str(company_obj)

            if not title or not company:
                continue

            # The Hub 的发布时间字段
            posted_at = job.get("publishedAt") or job.get("approvedAt") or job.get("createdAt")

            job_data = {
                "platform": "thehub",
                "platform_id": str(job.get("id", "")),
                "title": title,
                "company": company,
                "url": job.get("absoluteJobUrl", ""),
                "content_hash": compute_job_hash(company, title),
                "jd_text": clean_html(str(job.get("description", ""))),
                "posted_at": posted_at,
            }

            if db.insert_job(job_data):
                new_count += 1

        time.sleep(1)

    return new_count



# ============================================================
# 统一入口
# ============================================================

def scrape_all_platforms(db: JobDatabase) -> int:
    """运行所有采集器，返回总新增数"""
    total = 0

    # Tavily (替代 JobSpy, 覆盖 LinkedIn/Indeed/Glassdoor)
    try:
        from src.scraper_tavily import scrape_tavily
        total += scrape_tavily(db)
    except Exception as e:
        logger.warning(f"Tavily 采集失败: {e}")
        # Fallback 到 JobSpy
        logger.info("Fallback 到 JobSpy")
        total += scrape_jobspy(db)

    # TheHub (丹麦创业公司)
    total += scrape_thehub(db)

    # Jobindex (丹麦本地)
    try:
        from src.scraper_jobindex import scrape_jobindex
        total += scrape_jobindex(db)
    except Exception as e:
        logger.warning(f"Jobindex 采集失败: {e}")

    # 公司官网直抓 (基于公司列表) — 需要 Playwright
    if config.LIGHTWEIGHT_MODE:
        logger.info("轻量模式: 跳过公司官网采集 (Playwright)")
    else:
        try:
            from src.scraper_careers import scrape_company_careers
            total += scrape_company_careers(db)
        except Exception as e:
            logger.warning(f"公司官网采集失败: {e}")

    return total
