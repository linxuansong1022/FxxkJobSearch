"""
多平台职位采集模块

数据源：
1. LinkedIn + Indeed — 通过 python-jobspy 库聚合采集
2. The Hub (thehub.io) — 通过 httpx 直调 REST API

采集策略：
- 少量多次（每次20条），浅层搜索代替深层翻页
- 仅采集过去24小时内发布的职位
- 采集后立即计算 content_hash 去重，写入 SQLite
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

    遍历 config.SEARCH_QUERIES 中的关键词，
    每个关键词采集 results_wanted 条结果。
    关键词之间随机延迟 2-5 秒，避免触发频率限制。

    Returns:
        新插入的职位数量
    """
    new_count = 0

    for query in config.SEARCH_QUERIES:
        logger.info(f"JobSpy 采集: query='{query}'")
        try:
            # TODO: 如果需要代理，在此添加 proxy 参数
            # proxy="http://user:pass@host:port"
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

        # 将 DataFrame 逐行写入数据库
        for _, row in jobs_df.iterrows():
            title = str(row.get("title", ""))
            company = str(row.get("company_name", "") or row.get("company", ""))
            if not title or not company:
                continue

            job_data = {
                "platform": str(row.get("site", "unknown")),
                "platform_id": str(row.get("id", "")),
                "title": title,
                "company": company,
                "url": str(row.get("job_url", "")),
                "content_hash": compute_job_hash(company, title),
                "jd_text": clean_html(str(row.get("description", ""))),
            }

            if db.insert_job(job_data):
                new_count += 1

        # 关键词之间随机延迟
        delay = random.uniform(2, 5)
        logger.debug(f"等待 {delay:.1f}s 后继续...")
        time.sleep(delay)

    return new_count


# ============================================================
# 2. The Hub API 采集
# ============================================================

def scrape_thehub(db: JobDatabase) -> int:
    """
    通过 The Hub REST API 采集丹麦地区的实习/学生职位。

    The Hub 返回标准 JSON，无反爬限制，是最稳定的数据源。

    Returns:
        新插入的职位数量
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

        # TODO: 根据 The Hub 实际返回的 JSON 结构调整字段名
        # 常见结构: data["docs"] 或 data["results"] 或直接是列表
        jobs_list = data if isinstance(data, list) else data.get("docs", data.get("results", []))

        for job in jobs_list:
            title = job.get("title", "")
            company = job.get("company", {}).get("name", "") if isinstance(job.get("company"), dict) else str(job.get("company", ""))
            if not title or not company:
                continue

            job_data = {
                "platform": "thehub",
                "platform_id": str(job.get("id", job.get("_id", ""))),
                "title": title,
                "company": company,
                "url": job.get("url", job.get("applyUrl", "")),
                "content_hash": compute_job_hash(company, title),
                "jd_text": clean_html(str(job.get("description", ""))),
            }

            if db.insert_job(job_data):
                new_count += 1

        time.sleep(1)  # The Hub 友好，短延迟即可

    return new_count


# ============================================================
# 统一入口
# ============================================================

def scrape_all_platforms(db: JobDatabase) -> int:
    """运行所有采集器，返回总新增数"""
    total = 0
    total += scrape_jobspy(db)
    total += scrape_thehub(db)
    return total
