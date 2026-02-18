"""
JD 分析模块 (使用 google-genai 库 + ADC 认证)

职责：
- 读取 status='new' 的职位
- 调用 Gemini (通过 google-genai V2 SDK) 解析 JD
- 优先使用 Application Default Credentials (ADC) 进行认证
- 将分析结果写回数据库，状态更新为 'analyzed'
"""

import json
import logging
import os
import yaml

from google import genai
from google.genai import types

import config
from src.database import JobDatabase

logger = logging.getLogger(__name__)

def _load_profile_as_text() -> str:
    """读取 profile.yaml 并转换为文本描述"""
    try:
        with open(config.PROFILE_PATH, "r", encoding="utf-8") as f:
            profile = yaml.safe_load(f)
        
        # 构造文本
        text = f"Candidate Name: {profile.get('personal', {}).get('name')}\n"
        text += f"Education:\n"
        for edu in profile.get('education', []):
            text += f"- {edu.get('degree')} at {edu.get('school')} ({edu.get('dates')})\n"
            
        text += f"\nExperience:\n"
        for exp in profile.get('experiences', []):
            text += f"- {exp.get('role')} at {exp.get('company')}: {exp.get('bullets', [''])[0]}\n"
            
        text += f"\nProjects:\n"
        for proj in profile.get('projects', []):
            text += f"- {proj.get('name')} ({proj.get('role')}): {proj.get('bullets', [''])[0]}\n"
            
        text += f"\nSkills:\n"
        skills = profile.get('skills', {})
        text += f"- Languages: {skills.get('languages')}\n"
        text += f"- Tools: {skills.get('tools')}\n"
        
        return text
    except Exception as e:
        logger.warning(f"无法读取 Profile: {e}, 使用默认简略背景")
        return "Candidate: DTU Master Student in CS. Skills: Python, AI, LLM."

# JD 分析 Prompt
_ANALYSIS_PROMPT = """你是一个专业的IT技术招聘分析专家。请分析以下职位描述(JD)，并提取关键信息。

候选人背景 (Candidate Profile)：
{candidate_profile}

请基于以上候选人背景和以下JD提取信息，并评估与候选人的匹配度(0-1)。
重点关注：候选人的项目/实习经历是否支撑JD的核心需求。

请严格按照以下JSON格式输出，不要包含任何Markdown格式或额外文本：
{{
    "match_score": 0.0-1.0,  // 浮点数，匹配度分数
    "match_reason": "简短的匹配/不匹配理由，需具体引用候选人经历",
    "hard_skills": ["技能1", "技能2", ...], // 提取JD中的硬技能要求
    "soft_skills": ["技能1", "技能2", ...],
    "company_domain": "公司行业领域",
    "role_type": "职位类型 (实习/全职/兼职)",
    "summary": "职位摘要"
}}

JD内容：
{jd_text}
"""


def _init_client():
    """
    初始化 google-genai Client，优先使用 ADC。
    """
    # 尝试从环境变量获取 API Key (作为备选)
    api_key = config.GOOGLE_CLOUD_API_KEY or os.environ.get("GOOGLE_CLOUD_API_KEY")
    
    if api_key:
        logger.info("Using API Key for GenAI Client")
        # 当使用 API Key 时，不需要 project 和 location，
        # 或者某些版本的 SDK 是互斥的。根据报错信息 "mutually exclusive"，
        # 我们这里只传 api_key。
        return genai.Client(
            vertexai=True,
            api_key=api_key,
        )
    else:
        logger.info(f"Using ADC for GenAI Client (Project: {config.GCP_PROJECT_ID}, Location: {config.GCP_LOCATION})")
        # 显式传递 project 和 location 以确保 ADC 正确路由到 Vertex AI
        return genai.Client(
            vertexai=True,
            project=config.GCP_PROJECT_ID,
            location=config.GCP_LOCATION
        )


def analyze_single_jd(client: genai.Client, jd_text: str) -> dict | None:
    """
    用 Gemini 分析单条 JD。
    """
    if not jd_text or len(jd_text.strip()) < 50:
        logger.warning("JD 文本过短，跳过分析")
        return None

    # 动态加载 Profile
    profile_text = _load_profile_as_text()

    # 构造内容
    prompt_text = _ANALYSIS_PROMPT.format(
        candidate_profile=profile_text,
        jd_text=jd_text[:30000]
    )
    
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=prompt_text)
            ]
        )
    ]

    # 配置生成参数 (参考用户提供的 snippet)
    generate_content_config = types.GenerateContentConfig(
        temperature=0.7,  # 使用较高的温度以激发 Thinking 能力
        top_p=0.95,
        max_output_tokens=65535,
        response_mime_type="application/json",
        safety_settings=[
            types.SafetySetting(
                category="HARM_CATEGORY_HATE_SPEECH",
                threshold="OFF"
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT",
                threshold="OFF"
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                threshold="OFF"
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_HARASSMENT",
                threshold="OFF"
            )
        ],
        thinking_config=types.ThinkingConfig(
            thinking_level="HIGH",
        ),
    )

    try:
        # 使用 unary 调用 (非流式)，因为我们需要完整的 JSON
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=contents,
            config=generate_content_config,
        )
        
        # 解析响应
        if not response.text:
            logger.warning("Gemini 返回空文本")
            return None

        text = response.text
        # 尝试提取 JSON 块 (以防 Thinking 内容混入或 markdown 格式)
        if "```json" in text:
            import re
            match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)
        elif "{" in text:
            # 简单的查找第一个 { 和最后一个 }
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                text = text[start : end + 1]
            
        result = json.loads(text)
        
        # 兼容性处理
        if isinstance(result, list) and result:
            result = result[0]
        if not isinstance(result, dict):
            logger.warning(f"Gemini 返回非 dict 类型: {type(result)}")
            return None
            
        return result

    except Exception as e:
        logger.error(f"Gemini API 调用失败: {e}")
        return None


def analyze_pending_jobs(db: JobDatabase) -> int:
    """
    批量分析所有 status='new' 的职位。
    """
    all_new = db.get_jobs_by_status("new")
    pending_jobs = [j for j in all_new if j.get("relevance") == "relevant"]
    
    if not pending_jobs:
        logger.info("没有待分析的相关职位")
        return 0

    logger.info(f"待分析职位: {len(pending_jobs)} 条")
    
    try:
        client = _init_client()
    except Exception as e:
        logger.error(f"Failed to initialize GenAI client: {e}")
        return 0
        
    analyzed_count = 0

    for job in pending_jobs:
        logger.info(f"分析: {job['title']} @ {job['company']}")
        analysis = analyze_single_jd(client, job.get("jd_text", ""))

        if analysis:
            db.update_job_analysis(job["id"], analysis)
            analyzed_count += 1
            logger.info(f"  匹配度: {analysis.get('match_score', 'N/A')}")
        else:
            logger.warning(f"  分析失败，保留 new 状态待重试")

    return analyzed_count
