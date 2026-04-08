"""
职位过滤模块 (LLM Enhanced - google-genai + ADC)

两层过滤机制：
1. 规则层 (Rule-Based):
   - 时间过滤 (> MAX_JOB_AGE_DAYS 天)
   - 显性排除词 (HR, Sales 等) -> 快速剔除

2. 智能层 (LLM-Based):
   - 调用 Gemini Flash (通过 google-genai V2 SDK) 判断职位是否符合 "Python/AI/Backend Intern" 的定位
   - 优先使用 ADC 认证
"""

import json
import logging
import time
import os
from datetime import datetime, timedelta, timezone

from google import genai
from google.genai import types

import config
from src.database import JobDatabase

logger = logging.getLogger(__name__)


def _is_too_old(posted_at: str | None, first_seen_at: str | None = None) -> bool:
    """检查职位是否超过最大年龄。

    优先使用源头 posted_at;如果源头没给,fall back 到 first_seen_at(入库时间)。
    两者都没有才放行(只在数据库迁移期间会出现)。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.MAX_JOB_AGE_DAYS)

    candidate = posted_at or first_seen_at
    if not candidate:
        return False  # 兜底放行

    # 兼容多种时间格式
    for fmt in [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",   # SQLite datetime('now') 格式
        "%Y-%m-%d",
    ]:
        try:
            parsed = datetime.strptime(candidate[:26], fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed < cutoff
        except ValueError:
            continue

    return False  # 全部格式都解析失败,保守放行


def _is_obvious_irrelevant(title: str) -> bool:
    """
    基于显性关键词的快速排除。
    """
    title_lower = title.lower()
    for kw in config.TITLE_EXCLUDE_KEYWORDS:
        if kw in title_lower:
            return True
    return False


def _init_client():
    """
    初始化 google-genai Client，优先使用 ADC。
    """
    api_key = config.GOOGLE_CLOUD_API_KEY or os.environ.get("GOOGLE_CLOUD_API_KEY")
    if api_key:
        logger.info("Using API Key for GenAI Filter Client")
        return genai.Client(
            vertexai=True,
            api_key=api_key,
        )
    else:
        logger.info(f"Using ADC for GenAI Filter Client")
        return genai.Client(
            vertexai=True,
            project=config.GCP_PROJECT_ID,
            location=config.GCP_LOCATION
        )


import asyncio

async def _check_relevance_with_llm(client: genai.Client, title: str, company: str, semaphore: asyncio.Semaphore) -> tuple[bool, str]:
    """
    使用 Gemini Flash 判断职位是否相关 (Async)。
    """
    prompt = f"""
    Role: You are a strict recruitment filter for a Computer Science Master student looking for internships, student jobs, and part-time positions in Denmark.
    
    Candidate Profile:
    - Current status: Master student at DTU (Technical University of Denmark)
    - Looking for: Internship, Student Worker (studiejob/studentermedhjælper), Part-time, Unpaid internship, Thesis collaboration, Research Assistant
    - Location: Denmark ONLY (reject positions in other countries)
    - Skills: Python, AI, Machine Learning, Data Engineering, Backend, Full-stack
    
    MUST REJECT (is_relevant = false):
    - Full-time permanent positions (unless explicitly labeled as graduate/new grad program)
    - Senior/Lead/Manager/Principal/Staff/Architect roles
    - Positions NOT in Denmark (other countries like Sweden, USA, UK, Germany etc.)
    - Non-technical roles (Sales, HR, Marketing, Finance, Design, Supply Chain)
    - Roles requiring 3+ years of industry experience
    
    MUST KEEP (is_relevant = true):
    - Intern, Internship, Praktikant, Praktik
    - Student Worker, Studiejob, Studentermedhjælper, Student Assistant
    - Part-time, Deltid
    - Unpaid positions, Volunteer tech roles
    - Thesis/Project collaboration
    - Research Assistant (not requiring PhD)
    - Graduate/New Grad/Entry-level programs (first job after graduation)
    - Junior positions (0-1 years experience)
    
    BORDERLINE (keep if technical and not too senior):
    - Roles with no clear seniority level — keep only if title suggests junior/entry
    - "Engineer" without "Senior/Lead" — keep
    
    Task: Evaluate the job below.
    
    Job Title: "{title}"
    Company: "{company}"
    
    Output strictly valid JSON:
    {{
        "is_relevant": true/false,
        "reason": "short explanation (max 10 words)"
    }}
    """

    generation_config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json",
    )

    async with semaphore:
        try:
            # 使用 .aio.models.generate_content 进行异步调用
            response = await client.aio.models.generate_content(
                model=config.GEMINI_FLASH_MODEL,
                contents=[prompt],
                config=generation_config,
            )
            
            result = json.loads(response.text)
            return result.get("is_relevant", False), result.get("reason", "No reason provided")
        except Exception as e:
            logger.warning(f"LLM Filter Error: {e}")
            if "intern" in title.lower() or "student" in title.lower():
                return True, "LLM failed, fallback to keyword"
            return False, "LLM failed"


async def process_job(db: JobDatabase, job: dict, client: genai.Client, semaphore: asyncio.Semaphore) -> str:
    """处理单个职位的过滤逻辑 (Async)"""
    title = job["title"]
    
    # 1. 时间过滤 (Rule) - 优先用源头 posted_at,缺失时 fall back 到 first_seen_at
    if _is_too_old(job.get("posted_at"), job.get("first_seen_at")):
        db.update_job_relevance(job["id"], "irrelevant", status="filtered")
        logger.debug(f"  [Time] 过期: {title}")
        return "too_old"

    # 2. 显性排除 (Rule)
    if _is_obvious_irrelevant(title):
        db.update_job_relevance(job["id"], "irrelevant", status="filtered")
        logger.info(f"  [Rule] 排除: {title}")
        return "irrelevant"

    # 3. 智能判断 (LLM)
    is_relevant, reason = await _check_relevance_with_llm(client, title, job["company"], semaphore)
    
    if is_relevant:
        db.update_job_relevance(job["id"], "relevant")
        logger.info(f"  [LLM] 保留: {title} ({reason})")
        return "relevant"
    else:
        db.update_job_relevance(job["id"], "irrelevant", status="filtered")
        logger.info(f"  [LLM] 排除: {title} ({reason})")
        return "irrelevant"


async def filter_jobs(db: JobDatabase) -> dict[str, int]:
    """
    执行过滤流程 (Async 并发)。
    """
    unscored = db.get_unscored_jobs()
    if not unscored:
        logger.info("没有待过滤的职位")
        return {"relevant": 0, "irrelevant": 0, "too_old": 0}

    logger.info(f"待过滤职位: {len(unscored)} 条 (并发模式)")
    
    try:
        client = _init_client()
    except Exception as e:
        logger.error(f"Filter client init failed: {e}")
        return {"relevant": 0, "irrelevant": 0, "too_old": 0}

    # 限制并发数，防止超过 API Rate Limit
    semaphore = asyncio.Semaphore(10)
    
    tasks = [process_job(db, job, client, semaphore) for job in unscored]
    results = await asyncio.gather(*tasks)

    counts = {"relevant": 0, "irrelevant": 0, "too_old": 0}
    for r in results:
        counts[r] += 1

    return counts
