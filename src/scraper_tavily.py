"""
Tavily Search API 采集模块 — 替代 JobSpy

使用 Tavily Search API 搜索 LinkedIn / Indeed / Glassdoor 上的职位。
相比 JobSpy (直接爬 LinkedIn):
  - ✅ 正规 API，无反爬风险
  - ✅ 数据更新更及时 (time_range: "day")
  - ✅ 覆盖更广 (LinkedIn + Indeed + Glassdoor + Wellfound)
  - ✅ 免费 1000 次/月 (5关键词×30天 = 150次，绰绰有余)

Tavily 文档: https://docs.tavily.com/docs/tavily-api/python-sdk
"""

import logging
import re
import time

from tavily import TavilyClient

import config
from src.database import JobDatabase
from src.utils import compute_job_hash, clean_html

logger = logging.getLogger(__name__)


def _extract_company_from_content(content: str, url: str) -> str:
    """从搜索结果内容中提取公司名"""
    # LinkedIn URL 格式: linkedin.com/jobs/view/xxx-at-COMPANY-xxx
    if "linkedin.com" in url:
        match = re.search(r"-at-([a-zA-Z0-9-]+)-\d+", url)
        if match:
            return match.group(1).replace("-", " ").title()

    # 从内容中提取 "Company: xxx" 或 "at Company" 模式
    patterns = [
        r"(?:Company|Employer|Organization)[\s:]+([A-Z][A-Za-z0-9\s&.,]+?)(?:\s*[|\-·]|\n)",
        r"(?:at|@)\s+([A-Z][A-Za-z0-9\s&.,]+?)(?:\s*[|\-·]|\n|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            company = match.group(1).strip()
            if len(company) > 2 and len(company) < 80:
                return company

    return ""


def _is_job_detail_url(url: str) -> bool:
    """判断是否是具体的职位详情页，而非搜索结果页或公司主页"""
    url_lower = url.lower()
    
    # LinkedIn: 必须包含 /jobs/view/ 或 /jobs/collections/ (通常带具体ID)
    if "linkedin.com" in url_lower:
        return "/jobs/view/" in url_lower or "/jobs/collections/" in url_lower
        
    # Indeed: 必须包含 /viewjob 或 /rc/clk
    if "indeed.com" in url_lower:
        return "/viewjob" in url_lower or "/rc/clk" in url_lower
        
    # Glassdoor: 必须包含 /job-listing/ 或 /job/ (带具体ID)
    if "glassdoor.com" in url_lower:
        return "/job-listing/" in url_lower or "/job/" in url_lower
        
    # Wellfound: 通常是 /jobs/ 开头后接具体职位
    if "wellfound.com" in url_lower:
        return "/jobs/" in url_lower and not url_lower.endswith("/jobs/")
        
    return True # 其他未知平台默认允许，后续由 LLM 进一步判断

def _extract_title_from_result(title: str, url: str) -> str:
    """清理搜索结果标题，提取职位名称"""
    # 移除常见后缀
    for suffix in [
        " | LinkedIn", " - LinkedIn", " | Indeed", " - Indeed",
        " | Glassdoor", " - Glassdoor", " | Wellfound", " - Wellfound",
        " | Apply", " - Apply",
    ]:
        title = title.replace(suffix, "")

    # 移除位置信息后缀 (e.g., "... in Copenhagen, Denmark")
    title = re.sub(r"\s+in\s+[A-Z][a-zA-Z\s,]+$", "", title)

    return title.strip()


def scrape_tavily(db: JobDatabase) -> int:
    """
    使用 Tavily Search API 搜索职位。

    Returns:
        新增入库的职位数量
    """
    api_key = config.TAVILY_API_KEY
    if not api_key:
        logger.warning("TAVILY_API_KEY 未配置，跳过 Tavily 采集")
        return 0

    client = TavilyClient(api_key=api_key)
    tavily_config = config.TAVILY_SEARCH_CONFIG
    new_count = 0
    seen_urls: set[str] = set()

    for query in config.SEARCH_QUERIES:
        search_query = f"{query} Denmark"
        logger.info(f"Tavily 采集: query='{search_query}'")

        try:
            response = client.search(
                query=search_query,
                search_depth="basic",               # basic=1 credit, advanced=2
                max_results=tavily_config["max_results_per_query"],
                include_domains=tavily_config["include_domains"],
                time_range=tavily_config["time_range"],
            )
        except Exception as e:
            logger.warning(f"Tavily 搜索失败 (query='{search_query}'): {e}")
            continue

        results = response.get("results", [])
        logger.info(f"  Tavily 返回 {len(results)} 条结果")

        for result in results:
            url = result.get("url", "")
            title_raw = result.get("title", "")
            content = result.get("content", "")

            if not url or not title_raw:
                continue

            # 新增：严格过滤深层链接，确保不是搜索列表页或公司主页
            if not _is_job_detail_url(url):
                logger.debug(f"  [SKIP] 非职位详情页: {url}")
                continue

            # 去重
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # 提取职位信息
            title = _extract_title_from_result(title_raw, url)
            company = _extract_company_from_content(content, url)

            # 判断平台
            if "linkedin.com" in url:
                platform = "linkedin"
            elif "indeed.com" in url:
                platform = "indeed"
            elif "glassdoor.com" in url:
                platform = "glassdoor"
            elif "wellfound.com" in url:
                platform = "wellfound"
            else:
                platform = "tavily"

            # 跳过非职位结果 (搜索可能返回公司主页等)
            skip_patterns = [
                "/company/", "/companies/", "/salaries/",
                "/reviews/", "/about/", "/blog/",
            ]
            if any(p in url.lower() for p in skip_patterns):
                continue

            job_data = {
                "platform": platform,
                "platform_id": url.split("/")[-1] if "/" in url else url,
                "title": title,
                "company": company,
                "url": url,
                "content_hash": compute_job_hash(company, title),
                "jd_text": clean_html(content),
                "posted_at": result.get("published_date"),
            }

            if db.insert_job(job_data):
                new_count += 1
                logger.info(f"  [NEW] [{platform}] {title} @ {company}")

        # Tavily 有 rate limit，加点延迟
        time.sleep(1)

    logger.info(f"Tavily 总新增: {new_count} 条")
    return new_count
