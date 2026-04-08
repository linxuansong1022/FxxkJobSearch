"""
Apify LinkedIn Jobs Scraper

主力 LinkedIn 数据源,替代 Tavily 在 LinkedIn 上的覆盖。

Actor: bebity/linkedin-jobs-scraper
计费: ~$5 / 1000 results,通过 APIFY_CONFIG.max_items_total 卡死预算

Apify Python client docs:
  https://docs.apify.com/api/client/python/docs/quick-start

NOTE: Apify actor 的输入字段可能随版本变化。如果某次 run 报 schema 错误,
打开 https://apify.com/bebity/linkedin-jobs-scraper/input-schema 对照修正
_build_run_input() 里的字段名。
"""

from __future__ import annotations

import logging
from typing import Any

import config
from src.database import JobDatabase
from src.utils import clean_html, compute_job_hash

logger = logging.getLogger(__name__)


def _build_run_input(query: str, max_items: int) -> dict[str, Any]:
    """构造 bebity/linkedin-jobs-scraper 的 run_input。

    使用 actor 文档中标注的核心字段;如果 schema 更新,只需改这里。
    """
    return {
        "title": query,
        "location": config.APIFY_CONFIG["location"],
        "rows": max_items,
        "publishedAt": config.APIFY_CONFIG["time_range"],
        "proxy": {"useApifyProxy": True},
    }


def _extract_field(item: dict, *keys: str, default: str = "") -> str:
    """从 actor 返回的 item 里按优先级取第一个非空字段(防御 schema 漂移)。"""
    for key in keys:
        value = item.get(key)
        if value:
            return str(value).strip()
    return default


def _map_item_to_job(item: dict) -> dict | None:
    """把 Apify item 映射到 db.insert_job 期望的 job_data 字典。

    返回 None 表示该条 item 缺少关键字段(title 或 company),应跳过。
    """
    title = _extract_field(item, "title", "jobTitle", "position")
    company = _extract_field(item, "companyName", "company", "company_name")
    url = _extract_field(item, "link", "url", "jobUrl", "applyUrl")

    if not title or not company:
        return None

    description = _extract_field(item, "descriptionText", "description", "jobDescription")
    posted_at = _extract_field(
        item, "postedAt", "postedTime", "postedDate", "publishedAt", default="",
    ) or None

    platform_id = _extract_field(item, "id", "jobId", "linkedinJobId") or url

    return {
        "platform": "apify_linkedin",
        "platform_id": platform_id,
        "title": title,
        "company": company,
        "url": url,
        "content_hash": compute_job_hash(company, title),
        "jd_text": clean_html(description) if description else None,
        "posted_at": posted_at,
    }


def scrape_apify_linkedin(db: JobDatabase) -> int:
    """通过 Apify bebity/linkedin-jobs-scraper 采集丹麦 LinkedIn 岗位。

    Returns:
        新增入库的职位数量
    """
    token = config.APIFY_API_TOKEN
    if not token:
        logger.warning("APIFY_API_TOKEN 未配置,跳过 Apify LinkedIn 采集")
        return 0

    try:
        from apify_client import ApifyClient
    except ImportError:
        logger.warning("apify-client 未安装,跳过 Apify LinkedIn 采集 (pip install apify-client)")
        return 0

    apify_cfg = config.APIFY_CONFIG
    client = ApifyClient(token)
    actor_id = apify_cfg["linkedin_actor"]
    max_per_query = apify_cfg["max_items_per_query"]
    max_total = apify_cfg["max_items_total"]
    timeout = apify_cfg.get("run_timeout_secs", 300)

    new_count = 0
    items_consumed = 0  # 已消耗的 result 数(用于预算硬阀)

    for query in config.SEARCH_QUERIES:
        if items_consumed >= max_total:
            logger.info(
                f"Apify LinkedIn 已达预算上限 {max_total},停止后续 query"
            )
            break

        # 本次 query 的实际上限 = min(单次上限, 剩余预算)
        remaining = max_total - items_consumed
        this_max = min(max_per_query, remaining)

        logger.info(
            f"Apify LinkedIn 采集: query='{query}' max={this_max} (已消耗 {items_consumed}/{max_total})"
        )

        run_input = _build_run_input(query, this_max)

        try:
            run = client.actor(actor_id).call(
                run_input=run_input,
                timeout_secs=timeout,
            )
        except Exception as e:
            logger.warning(f"Apify LinkedIn actor 调用失败 (query='{query}'): {e}")
            continue

        if not run or "defaultDatasetId" not in run:
            logger.warning(f"Apify LinkedIn 返回空 run (query='{query}')")
            continue

        try:
            items_iter = client.dataset(run["defaultDatasetId"]).iterate_items()
        except Exception as e:
            logger.warning(f"Apify LinkedIn dataset 读取失败 (query='{query}'): {e}")
            continue

        for item in items_iter:
            items_consumed += 1
            job_data = _map_item_to_job(item)
            if not job_data:
                continue
            if db.insert_job(job_data):
                new_count += 1
                logger.info(
                    f"  [NEW] [apify_linkedin] {job_data['title']} @ {job_data['company']}"
                )
            if items_consumed >= max_total:
                break

    estimated_cost = items_consumed * 0.005
    logger.info(
        f"Apify LinkedIn 总新增: {new_count} 条 | "
        f"消耗 {items_consumed} results (预估 ${estimated_cost:.2f})"
    )
    return new_count
