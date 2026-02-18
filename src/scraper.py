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
# 3. LinkedIn JD 补全
# ============================================================

def backfill_linkedin_jd(db: JobDatabase) -> int:
    """
    对 JD 过短的 LinkedIn 职位，通过访问职位页面补全 JD。

    JobSpy 的 LinkedIn 模式经常只返回标题，不含完整 JD。
    这里用 httpx 访问 LinkedIn 公开职位页，提取 JD 文本。
    """
    # 找出 JD 过短的职位（< 100 字符）
    cursor = db.conn.execute(
        """SELECT id, url, title FROM jobs
           WHERE platform = 'linkedin'
             AND (jd_text IS NULL OR length(jd_text) < 100)
             AND status != 'filtered'"""
    )
    short_jd_jobs = [dict(row) for row in cursor.fetchall()]

    if not short_jd_jobs:
        logger.info("没有需要补全 JD 的 LinkedIn 职位")
        return 0

    logger.info(f"需要补全 JD 的 LinkedIn 职位: {len(short_jd_jobs)} 条")
    filled_count = 0

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for job in short_jd_jobs:
        url = job.get("url", "")
        if not url or "linkedin.com" not in url:
            continue

        logger.info(f"  补全: {job['title']} ({url[:60]}...)")
        try:
            resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
            if resp.status_code != 200:
                logger.debug(f"    HTTP {resp.status_code}")
                continue

            html = resp.text
            # LinkedIn 公开页面的 JD 通常在 <div class="show-more-less-html__markup"> 中
            # 或者在 <div class="description__text"> 中
            jd_text = _extract_linkedin_jd(html)

            if jd_text and len(jd_text) > 100:
                db.update_job_jd(job["id"], jd_text)
                filled_count += 1
                logger.info(f"    补全成功: {len(jd_text)} 字")
            else:
                logger.debug(f"    未提取到有效 JD")

        except Exception as e:
            logger.debug(f"    请求失败: {e}")

        # 友好延迟，避免被封
        time.sleep(random.uniform(2, 4))

    return filled_count


def _extract_linkedin_jd(html: str) -> str:
    """从 LinkedIn 职位页面 HTML 中提取 JD 文本"""
    from src.utils import clean_html
    import re

    # 方法1：找 show-more-less-html__markup 块
    pattern1 = r'<div class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>'
    match = re.search(pattern1, html, re.DOTALL)
    if match:
        return clean_html(match.group(1))

    # 方法2：找 description__text 块
    pattern2 = r'<div class="description__text[^"]*"[^>]*>(.*?)</div>'
    match = re.search(pattern2, html, re.DOTALL)
    if match:
        return clean_html(match.group(1))

    # 方法3：找 JSON-LD 中的 description
    pattern3 = r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"'
    match = re.search(pattern3, html)
    if match:
        desc = match.group(1).encode().decode('unicode_escape', errors='ignore')
        return clean_html(desc)

    return ""


# ============================================================
# 统一入口
# ============================================================

def scrape_all_platforms(db: JobDatabase) -> int:
    """运行所有采集器，返回总新增数"""
    total = 0
    total += scrape_jobspy(db)
    total += scrape_thehub(db)
    return total
