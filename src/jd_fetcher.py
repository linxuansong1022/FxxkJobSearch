"""
JD 全文补全模块

对 jd_text 缺失或过短的职位，去原始 URL 抓取完整内容。
两层策略：
  1. httpx（快速，低资源） — 对非 JS 渲染页面有效
  2. Playwright fallback（慢，高成功率） — 当 httpx 失败/内容过短时启用
"""

import asyncio
import logging

import httpx
from bs4 import BeautifulSoup

import config
from src.database import JobDatabase
from src.utils import clean_html

logger = logging.getLogger(__name__)

MIN_JD_LENGTH = 200  # 低于此长度视为缺失

# 这些平台几乎都是 JS 渲染，httpx 基本无效，直接走 Playwright
_JS_HEAVY_PLATFORMS = {"linkedin", "indeed", "glassdoor"}

_PLATFORM_SELECTORS = {
    "indeed": "#jobDescriptionText",
    "linkedin": ".description__text, .show-more-less-html__markup",
    "jobindex": ".jobad-content, .PaidJob-inner",
    "thehub": ".job-description, .listing-body",
}


async def _fetch_via_httpx(
    client: httpx.AsyncClient, url: str, platform: str
) -> str | None:
    """httpx 快速抓取（对静态页面有效）"""
    try:
        resp = await client.get(url, follow_redirects=True, timeout=15)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        selector = _PLATFORM_SELECTORS.get(platform)
        el = soup.select_one(selector) if selector else None

        if not el:
            el = soup.select_one("main, article, .job-description")

        if el:
            return clean_html(el.get_text(separator="\n", strip=True))

        body = soup.find("body")
        if body:
            return clean_html(body.get_text(separator="\n", strip=True))[:5000]
    except Exception as e:
        logger.debug(f"  httpx fetch failed for {url}: {e}")
    return None


def _fetch_via_playwright(url: str) -> str | None:
    """Playwright fallback — 处理 JS 渲染页面"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright 未安装，跳过 fallback")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)  # 等待 JS 渲染
            except Exception as e:
                logger.debug(f"  Playwright goto timeout for {url}: {e}")

            # 尝试关闭 cookie 弹窗
            try:
                page.evaluate("""
                    () => {
                        const btns = Array.from(document.querySelectorAll('button, a'));
                        const cb = btns.find(b =>
                            b.innerText.toLowerCase().match(/accept|allow all|agree|got it/)
                        );
                        if (cb) cb.click();
                    }
                """)
                page.wait_for_timeout(500)
            except Exception:
                pass

            # 移除噪音元素
            try:
                page.evaluate("""
                    () => {
                        const sels = ['header', 'footer', 'nav', '[role="banner"]',
                                      '[role="navigation"]', '.cookie-banner',
                                      '#onetrust-consent-sdk'];
                        sels.forEach(s => document.querySelectorAll(s)
                            .forEach(el => el.remove()));
                    }
                """)
            except Exception:
                pass

            # 提取 JD 内容 — 按优先级尝试多种选择器
            jd_selectors = [
                "#jobDescriptionText",                          # Indeed
                ".description__text",                           # LinkedIn
                ".show-more-less-html__markup",                 # LinkedIn alt
                ".jobad-content",                               # Jobindex
                ".job-description",                             # Generic
                "[data-testid='job-description']",              # Modern sites
                "article",                                      # Semantic
                "main",                                         # Fallback
            ]

            text = ""
            for sel in jd_selectors:
                try:
                    el = page.query_selector(sel)
                    if el:
                        text = el.inner_text().strip()
                        if len(text) >= MIN_JD_LENGTH:
                            break
                except Exception:
                    continue

            if len(text) < MIN_JD_LENGTH:
                try:
                    body = page.query_selector("body")
                    if body:
                        text = body.inner_text().strip()[:5000]
                except Exception:
                    pass

            browser.close()

            if text:
                return clean_html(text)
    except Exception as e:
        logger.debug(f"  Playwright fallback failed for {url}: {e}")
    return None


async def _fetch_single_jd(
    client: httpx.AsyncClient, url: str, platform: str
) -> str | None:
    """抓取 JD 全文：httpx 优先，失败时 Playwright fallback"""
    # 轻量模式: 只用 httpx，不启动 Playwright
    if config.LIGHTWEIGHT_MODE:
        jd = await _fetch_via_httpx(client, url, platform)
        if not jd or len(jd) < MIN_JD_LENGTH:
            logger.debug(f"  轻量模式: httpx 不足，跳过 Playwright: {url}")
        return jd

    # JS 重度平台直接跳到 Playwright
    if platform in _JS_HEAVY_PLATFORMS:
        logger.debug(f"  {platform} 平台，直接使用 Playwright: {url}")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch_via_playwright, url)

    # 其他平台先尝试 httpx
    jd = await _fetch_via_httpx(client, url, platform)
    if jd and len(jd) >= MIN_JD_LENGTH:
        return jd

    # httpx 失败或内容过短，fallback 到 Playwright
    logger.debug(f"  httpx 不足 ({len(jd) if jd else 0} chars)，Playwright fallback: {url}")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_via_playwright, url)


async def backfill_missing_jds(
    db: JobDatabase, max_concurrent: int = 5
) -> int:
    """批量补全缺失的 JD"""
    cursor = db.conn.execute(
        """SELECT id, url, platform, jd_text FROM jobs
           WHERE relevance = 'relevant' AND status = 'new'
           AND (jd_text IS NULL OR LENGTH(jd_text) < ?)""",
        (MIN_JD_LENGTH,),
    )
    jobs = [dict(row) for row in cursor.fetchall()]

    if not jobs:
        logger.info("JD Backfill: 无需补全")
        return 0

    logger.info(f"JD Backfill: {len(jobs)} 条 JD 需要补全")

    filled = 0
    semaphore = asyncio.Semaphore(max_concurrent)

    async with httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0.0.0 Safari/537.36"
        },
    ) as client:

        async def process(job: dict) -> bool:
            async with semaphore:
                jd = await _fetch_single_jd(
                    client, job["url"], job["platform"]
                )
                if jd and len(jd) >= MIN_JD_LENGTH:
                    db.update_job_jd(job["id"], jd)
                    logger.info(
                        f"  ✓ JD 补全: {job['id']} ({len(jd)} chars)"
                    )
                    return True
                else:
                    logger.debug(
                        f"  ✗ JD 补全失败: {job['id']} ({job['url']})"
                    )
            return False

        results = await asyncio.gather(*[process(j) for j in jobs])
        filled = sum(results)

    logger.info(f"JD Backfill 完成: {filled}/{len(jobs)} 条成功补全")
    return filled
