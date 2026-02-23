"""
公司官网直抓采集模块 — Playwright + LLM 提取

专门用于抓取 `company_list.py` 中预定义的那些无法通过常规平台获取的公司官网。
原理：
  1. 使用 Playwright 打开 `career_url`，等待 JS 加载完成。
  2. 提取网页主体纯文本（去掉无关 HTML）。
  3. 将文本喂给 Gemini Flash，要求提取所有相关的有效职位（名称、链接、地点）。
  4. 存入数据库。
"""

import json
import logging
import re
import time
from typing import Optional

from google import genai
from google.genai import types
from playwright.sync_api import sync_playwright

import config
from src.database import JobDatabase
from src.utils import compute_job_hash, clean_html
from src.company_list import DENMARK_TECH_COMPANIES

logger = logging.getLogger(__name__)

# 定义给 LLM 输出限制的 Schema
JOB_LIST_SCHEMA = {
    "type": "ARRAY",
    "description": "A list of relevant open job positions found on the page.",
    "items": {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING", "description": "The job title."},
            "url": {"type": "STRING", "description": "The URL to apply or view details. Must be a valid URL path."},
            "location": {"type": "STRING", "description": "Job location if mentioned."}
        },
        "required": ["title", "url"],
    }
}


def _get_llm_client() -> genai.Client:
    """初始化用于提取的 LLM"""
    import os
    api_key = config.GOOGLE_CLOUD_API_KEY or os.environ.get("GOOGLE_CLOUD_API_KEY")
    if api_key:
        return genai.Client(vertexai=True, api_key=api_key)
    else:
        return genai.Client(
            vertexai=True,
            project=config.GCP_PROJECT_ID,
            location=config.GCP_LOCATION,
        )


def _extract_text_via_playwright(url: str, headless: bool = True) -> tuple[str, str]:
    """使用 Playwright 加载网页并返回纯文本和完整 URL"""
    text_content = ""
    final_url = url
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()
            
            # 访问页面
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception as e:
                logger.debug(f"      Playwright goto 报错 (可能超时)，尝试继续: {e}")
                
            # 提取所有可见的链接文本和 URL (Helper function)
            def _get_links(page_obj):
                try:
                    return page_obj.evaluate("""
                        () => {
                            const links = [];
                            document.querySelectorAll('a').forEach(a => {
                                const text = a.innerText.trim();
                                const href = a.href;
                                if (text && text.length > 4 && href && !href.includes('javascript:') && !href.includes('mailto:')) {
                                    links.push(`[${text}](${href})`);
                                }
                            });
                            return links;
                        }
                    """)
                except Exception as e:
                    logger.debug(f"      提取链接时出错 (可能页面正在跳转): {e}")
                    return []
            
            # 1. 初始尝试抓取 (作为 Fallback)
            initial_links = _get_links(page)
            
            # 2. 处理常见的 Cookie 弹窗 (尝试盲猜并关闭，避免遮挡)
            try:
                page.evaluate("""
                    () => {
                        const buttons = Array.from(document.querySelectorAll('button, a'));
                        const cookieBtn = buttons.find(b => 
                            b.innerText.toLowerCase().includes('accept') || 
                            b.innerText.toLowerCase().includes('allow all') ||
                            b.innerText.toLowerCase().includes('agree')
                        );
                        if(cookieBtn) cookieBtn.click();
                    }
                """)
                page.wait_for_timeout(1000)
            except:
                pass
            
            # 3. 尝试点击 "搜索职位" 或 "显示全部" 按钮以加载动态列表
            try:
                # 使用 Playwright 自带的 wait_for_load_state 避免 context destroyed 报错
                page.evaluate("""
                    () => {
                        const buttons = Array.from(document.querySelectorAll('button, a'));
                        const searchBtn = buttons.find(b => 
                            b.innerText.toLowerCase().includes('search job') || 
                            b.innerText.toLowerCase().includes('view all') ||
                            b.innerText.toLowerCase().includes('show job') ||
                            b.innerText.toLowerCase().includes('all open')
                        );
                        if(searchBtn) searchBtn.click();
                    }
                """)
                # 等待网络空闲，但也设置超时捕获异常
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception as e:
                logger.debug(f"      点击交互后等待超时或强制跳转: {e}")
                
            final_url = page.url
            
            # 移除无用的元素，减少噪点
            try:
                page.evaluate("""
                    () => {
                        const selectors = ['nav', 'footer', 'script', 'style', 'noscript', 'svg', 'img', 'video', 'header', '.cookie-banner', '#cookie-banner'];
                        selectors.forEach(selector => {
                            document.querySelectorAll(selector).forEach(e => e.remove());
                        });
                    }
                """)
            except:
                pass
            
            # 4. 再次提取链接
            final_links = _get_links(page)
            
            # 合并链接并去重
            all_links = list(set(initial_links + final_links))
            
            # 提取正文文本作为补充
            raw_text = ""
            try:
                body_element = page.locator("body")
                if body_element.count() > 0:
                    raw_text = body_element.inner_text()
            except:
                pass
            
            # 将链接列表和原始文本组合，重点突出链接
            text_content = "EXTRACTED LINKS:\n" + "\n".join(all_links) + "\n\nRAW TEXT SUPPLEMENT:\n" + raw_text[:20000]
            
            browser.close()
    except Exception as e:
        logger.error(f"      Playwright 抓取失败: {e}")
        
    return text_content, final_url


def _extract_jobs_with_llm(llm: genai.Client, text: str, company_name: str, base_url: str) -> list[dict]:
    """使用 LLM 从纯文本中提取职位列表"""
    if not text or len(text) < 50:
        return []

    # 如果文本太长，截断以防超出 token 限制，通常职位列表都在前面的主体部分
    # 这里放宽到 40000 字符，Gemini Flash 妥妥够用
    max_chars = 40000
    if len(text) > max_chars:
        text = text[:max_chars]

    prompt = f"""
You are an expert data extractor. I have scraped the careers page of the company '{company_name}'.
The page URL is: {base_url}

Your task is to extract all the RELEVANT open job positions listed in the text below.
The text contains a list of extracted markdown links `[Link Text](URL)` followed by some raw supplementary text.

We are primarily looking for:
- Software Engineering / Development / Backend / Frontend
- Data Science / Machine Learning / AI
- Internships / Student Jobs suitable for tech students

RULES:
1. Extract the exact job title from the link text or raw text.
2. Extract the associated URL. The URL MUST come from the provided `[Link Text](URL)` format if available.
3. Only return jobs roughly related to Tech, Data, AI, or Internships. Ignore pure Sales, HR, Legal, or Maintenance jobs if they are clearly irrelevant.
4. Output strictly as the requested JSON ARRAY schema.

---
Data Content:
{text}
"""
    
    try:
        response = llm.models.generate_content(
            model=config.GEMINI_FLASH_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=JOB_LIST_SCHEMA,
                temperature=0.1,
            ),
        )
        
        jobs_data = json.loads(response.text)
        if isinstance(jobs_data, list):
            return jobs_data
        elif isinstance(jobs_data, dict) and "items" in jobs_data:
            return jobs_data.get("items", [])
            
    except Exception as e:
        logger.error(f"      LLM 提取失败: {e}")
        
    return []


def scrape_company_careers(db: JobDatabase, max_companies: Optional[int] = None) -> int:
    """
    抓取公司官网职位。
    
    Args:
        db: 数据库实例
        max_companies: 限制测试的公司数量 (用于 debug)
    """
    targets = [c for c in DENMARK_TECH_COMPANIES if c.get("career_url")]
    if max_companies:
        # Debug 时可以随机散列或直接取前 N 个
        targets = targets[:max_companies]

    if not targets:
        logger.info("没有找到配置了 career_url 的公司")
        return 0

    logger.info(f"开始抓取公司官网 (共 {len(targets)} 家)")
    
    try:
        llm = _get_llm_client()
    except Exception as e:
        logger.error(f"无法初始化 LLM，挂起采集: {e}")
        return 0

    new_count = 0
    
    for idx, company in enumerate(targets, 1):
        name = company["name"]
        url = company["career_url"]
        logger.info(f"[{idx}/{len(targets)}] 抓取 {name} ({url})")
        
        # 1. Playwright 获取纯文本
        text_content, final_url = _extract_text_via_playwright(url)
        
        if not text_content:
            logger.warning(f"      未能获取到页面文本内容")
            continue
            
        logger.debug(f"      获取文本长度: {len(text_content)}")
        
        # [DEBUG] Save text output to file
        with open(f"debug_{name.replace(' ', '_')}.txt", "w", encoding="utf-8") as f:
            f.write(text_content)
        
        # 2. LLM 提取职位
        extracted_jobs = _extract_jobs_with_llm(llm, text_content, name, final_url)
        logger.info(f"      LLM 提取到 {len(extracted_jobs)} 条技术类职位")
        
        # 3. 入库
        for job_info in extracted_jobs:
            title = job_info.get("title", "")
            job_url = job_info.get("url", url)
            
            if not title:
                continue
                
            job_data = {
                "platform": "company_website",
                "platform_id": compute_job_hash(name, title)[:16],
                "title": title[:200],
                "company": name,
                "url": job_url,
                "content_hash": compute_job_hash(name, title),
                "jd_text": None,  # 列表页通常只有标题，留空让 fetch_job_detail 去抓
                "posted_at": None,
            }
            
            if db.insert_job(job_data):
                new_count += 1
                logger.info(f"        [NEW] {title}")
                
        # 加点延迟，避免对本地或远程目标压力太大
        time.sleep(2)
        
    logger.info(f"公司官网直抓完成，总新增: {new_count} 条")
    return new_count
