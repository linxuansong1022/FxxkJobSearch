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


def _is_too_old(posted_at: str | None) -> bool:
    """检查职位是否超过最大年龄"""
    if not posted_at:
        return False  # 没有发布时间的保留

    try:
        # 兼容多种时间格式
        for fmt in [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ]:
            try:
                # 处理带有时区的情况
                posted = datetime.strptime(posted_at[:26], fmt)
                if posted.tzinfo is None:
                    posted = posted.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        else:
            return False  # 解析失败，保留

        cutoff = datetime.now(timezone.utc) - timedelta(days=config.MAX_JOB_AGE_DAYS)
        return posted < cutoff
    except Exception:
        return False


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
            project=config.GCP_PROJECT_ID,
            location=config.GCP_LOCATION
        )
    else:
        logger.info(f"Using ADC for GenAI Filter Client")
        return genai.Client(
            vertexai=True,
            project=config.GCP_PROJECT_ID,
            location=config.GCP_LOCATION
        )


def _check_relevance_with_llm(client: genai.Client, title: str, company: str) -> tuple[bool, str]:
    """
    使用 Gemini Flash 判断职位是否相关。
    """
    prompt = f"""
    Role: You are a strict recruitment filter for a Computer Science Master student.
    
    Candidate Profile:
    - Education: Master in CS at DTU.
    - Skills: Python, Backend, AI, LLM, RAG, Agents, Data Engineering.
    - Looking for: Internship, Student Job, Unpaid Project.
    
    Negative Filters (Must Reject):
    - Pure Marketing, Sales, HR, Finance, Supply Chain, Design roles.
    - Senior/Lead roles requiring 5+ years experience.
    
    Task: Evaluate if the job below is a potential match.
    
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

    try:
        response = client.models.generate_content(
            model=config.GEMINI_FLASH_MODEL,
            contents=[prompt],
            config=generation_config,
        )
        
        result = json.loads(response.text)
        return result.get("is_relevant", False), result.get("reason", "No reason provided")
    except Exception as e:
        logger.warning(f"LLM Filter Error: {e}")
        # 如果 LLM 失败，回退到保守策略
        if "intern" in title.lower() or "student" in title.lower():
            return True, "LLM failed, fallback to keyword"
        return False, "LLM failed"


def filter_jobs(db: JobDatabase) -> dict[str, int]:
    """
    执行过滤流程。
    """
    unscored = db.get_unscored_jobs()
    if not unscored:
        logger.info("没有待过滤的职位")
        return {"relevant": 0, "irrelevant": 0, "too_old": 0}

    logger.info(f"待过滤职位: {len(unscored)} 条")
    
    # 初始化 client (ADC)
    try:
        client = _init_client()
    except Exception as e:
        logger.error(f"Filter client init failed: {e}")
        return {"relevant": 0, "irrelevant": 0, "too_old": 0}

    counts = {"relevant": 0, "irrelevant": 0, "too_old": 0}

    for job in unscored:
        title = job["title"]
        
        # 1. 时间过滤 (Rule)
        if _is_too_old(job.get("posted_at")):
            db.update_job_relevance(job["id"], "irrelevant", status="filtered")
            counts["too_old"] += 1
            logger.debug(f"  [Time] 过期: {title}")
            continue

        # 2. 显性排除 (Rule)
        if _is_obvious_irrelevant(title):
            db.update_job_relevance(job["id"], "irrelevant", status="filtered")
            counts["irrelevant"] += 1
            logger.info(f"  [Rule] 排除: {title}")
            continue

        # 3. 智能判断 (LLM)
        time.sleep(0.2) 
        
        is_relevant, reason = _check_relevance_with_llm(client, title, job["company"])
        
        if is_relevant:
            db.update_job_relevance(job["id"], "relevant")
            counts["relevant"] += 1
            logger.info(f"  [LLM] 保留: {title} ({reason})")
        else:
            db.update_job_relevance(job["id"], "irrelevant", status="filtered")
            counts["irrelevant"] += 1
            logger.info(f"  [LLM] 排除: {title} ({reason})")

    return counts
