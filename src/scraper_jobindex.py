"""
Jobindex.dk 采集模块

采集策略:
  Jobindex 搜索结果页面将 job 数据嵌入在 JS 内的 escaped HTML 中。
  实际字节内容为 \\" (backslash + quote)、\\n (backslash + n) 等。

  解析步骤:
    1. httpx GET 搜索页面
    2. regex 提取 jobsearch-result 块 (每块含一个职位)
    3. js_unescape 还原 HTML
    4. BeautifulSoup 解析 company / title / url / date

  每个搜索关键词单独请求，interval 2-4s 防止 rate limit。
"""

import logging
import random
import re
import time

import httpx
from bs4 import BeautifulSoup

import config
from src.database import JobDatabase
from src.utils import compute_job_hash, clean_html

logger = logging.getLogger(__name__)

# Jobindex 搜索关键词
JOBINDEX_QUERIES = [
    "python intern",
    "AI intern",
    "machine learning intern",
    "data science studiejob",
    "software developer intern",
    "backend developer intern",
    "full stack studiejob",
    "cloud engineer praktikant",
    "MLOps intern",
    "data engineer studiejob",
    "IT praktikant København",
]

SEARCH_URL = "https://www.jobindex.dk/jobsoegning"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9,da;q=0.8",
}


def _js_unescape(s: str) -> str:
    """还原 JS 字符串中的转义字符 (实际字节层面)"""
    s = s.replace('\\"', '"')
    s = s.replace('\\/', '/')
    s = s.replace('\\n', '\n')
    s = s.replace('\\t', '\t')
    s = s.replace("\\\\", "\\")
    return s


def _parse_jobs_from_html(html: str) -> list[dict]:
    """
    从 Jobindex 搜索结果页面提取职位列表。

    使用 regex 提取 jobsearch-result 块，unescape 后用 BS4 解析。
    """
    jobs = []

    # 提取所有 jobsearch-result 块 (每块是一个职位)
    # 用非贪婪匹配提取每个 block
    blocks = re.findall(
        r'(jobsearch-result.*?)(?=jobsearch-result|$)',
        html,
        re.DOTALL,
    )

    if not blocks:
        return jobs

    for block_raw in blocks:
        block = _js_unescape(block_raw)

        # 跳过太短的无效块
        if len(block) < 100:
            continue

        try:
            soup = BeautifulSoup(block, "html.parser")

            # --- 提取公司名 ---
            company_el = soup.select_one(
                ".jix-toolbar-top__company a, .jix_robotjob__company a"
            )
            company = company_el.get_text(strip=True) if company_el else ""

            # --- 提取职位标题和 URL ---
            title_el = None

            # 方法 1: h2/h3 > a (PaidJob)
            for sel in ["h2 a", "h3 a", ".jix_robotjob_link"]:
                title_el = soup.select_one(sel)
                if title_el:
                    break

            # 方法 2: 指向 /jobannonce/ 的链接
            if not title_el:
                for link in soup.find_all("a", href=True):
                    href = link.get("href", "")
                    if "/jobannonce/" in href or "/job/" in href:
                        title_el = link
                        break

            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")

            # 拼完整 URL
            if href.startswith("/"):
                url = f"https://www.jobindex.dk{href}"
            elif href.startswith("http"):
                url = href
            else:
                continue

            if not title or len(title) < 3:
                continue

            # --- 提取发布时间 ---
            date_el = soup.select_one("time, [datetime]")
            posted_at = None
            if date_el:
                posted_at = date_el.get("datetime") or date_el.get_text(strip=True)

            # --- 提取摘要 ---
            desc_el = soup.select_one(
                ".PaidJob-inner p, .jix_robotjob__teaser, .jobsearch-result__body"
            )
            description = desc_el.get_text(strip=True) if desc_el else ""

            jobs.append({
                "title": title,
                "company": company,
                "url": url,
                "posted_at": posted_at,
                "description": description,
            })

        except Exception as e:
            logger.debug(f"解析 Jobindex block 失败: {e}")
            continue

    return jobs


def scrape_jobindex(db: JobDatabase) -> int:
    """
    从 Jobindex.dk 抓取职位。

    Returns:
        新增入库的职位数量
    """
    new_count = 0
    seen_urls: set[str] = set()

    for query in JOBINDEX_QUERIES:
        logger.info(f"Jobindex 采集: query='{query}'")

        params = {"q": query}

        try:
            resp = httpx.get(
                SEARCH_URL,
                params=params,
                headers=HEADERS,
                timeout=20,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Jobindex 请求失败 (query='{query}'): {e}")
            continue

        jobs = _parse_jobs_from_html(resp.text)
        logger.info(f"  解析到 {len(jobs)} 条职位")

        for job in jobs:
            url = job["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)

            job_data = {
                "platform": "jobindex",
                "platform_id": url.split("/")[-1] if "/" in url else url,
                "title": job["title"],
                "company": job["company"],
                "url": url,
                "content_hash": compute_job_hash(job["company"], job["title"]),
                "jd_text": job.get("description", ""),
                "posted_at": job.get("posted_at"),
            }

            if db.insert_job(job_data):
                new_count += 1

        # 友好延迟
        time.sleep(random.uniform(2, 4))

    logger.info(f"Jobindex 总新增: {new_count} 条")
    return new_count
