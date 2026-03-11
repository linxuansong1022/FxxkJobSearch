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
import asyncio

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

**候选人求职目标**：
- 丹麦境内的 实习(Intern/Praktikant)、学生工(Studiejob/Studentermedhjælper)、兼职(Part-time)、论文合作(Thesis)
- 也接受 Graduate/New Grad 入职项目
- 不接受需要 3+ 年全职经验的职位

**评分规则 (MUST follow)**：
1. **职位类型权重最高**：
   - 实习/学生工/兼职/论文合作/Research Assistant → 正常评分
   - Graduate/New Grad/Junior (0-1年经验) → 正常评分
   - 全职且要求 2+ 年经验 → 最高不超过 0.50
   - 全职且要求 5+ 年经验 → 最高不超过 0.30
2. **地理位置**：
   - 丹麦境内 → 正常评分
   - 非丹麦（瑞典马尔默/隆德、远程等北欧） → 扣 0.15
   - 其他国家（美国、英国等） → 最高不超过 0.20
3. **技能匹配**：候选人极擅长 Python (PyTorch), AI, GraphRAG。即便 JD 要其他语言，只要领域对口不应大幅扣分

评分锚定:
- 0.90-1.00: 完美匹配 — 丹麦实习/学生工，技能高度吻合
- 0.75-0.89: 强相关 — 丹麦入门级/graduate，技能大部分匹配
- 0.60-0.74: 一般相关 — 领域对口但有差异（如非丹麦的北欧实习）
- 0.40-0.59: 弱相关 — 全职初级岗位或技能差距明显
- 0.00-0.39: 不相关 — 全职高年资/非丹麦/非技术

重要：如果 JD 信息不完整（少于 200 字），请在 match_reason 中注明 "JD信息不足，评分可能不准确"，且 match_score 不应超过 0.75。

请严格按照以下JSON格式输出，不要包含任何Markdown格式或额外文本：
{{
    "match_score": 0.0-1.0,  // 浮点数，匹配度分数
    "match_reason": "简短的匹配/不匹配理由，需具体引用候选人经历",
    "hard_skills": ["技能1", "技能2", ...], // 提取JD中的硬技能要求
    "soft_skills": ["技能1", "技能2", ...],
    "company_domain": "公司行业领域",
    "role_type": "职位类型 (实习/学生工/兼职/全职/Graduate)",
    "location": "职位地点",
    "experience_required": "要求的经验年限",
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


async def analyze_single_jd(client: genai.Client, jd_text: str, semaphore: asyncio.Semaphore) -> dict | None:
    """
    用 Gemini 分析单条 JD (Async)。
    """
    jd_len = len(jd_text.strip()) if jd_text else 0
    if not jd_text or jd_len < 200:
        logger.warning(f"JD 文本过短 ({jd_len} chars)，跳过分析")
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
            parts=[types.Part.from_text(text=prompt_text)]
        )
    ]

    generate_content_config = types.GenerateContentConfig(
        temperature=0.7,
        top_p=0.95,
        max_output_tokens=65535,
        response_mime_type="application/json",
        safety_settings=[
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
        ]
    )

    async with semaphore:
        try:
            response = await client.aio.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=contents,
                config=generate_content_config,
            )
            
            if not response.text:
                return None

            text = response.text
            # 兼容处理 Thinking 或 Markdown
            if "```json" in text:
                import re
                match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
                if match: text = match.group(1)
            elif "{" in text:
                start, end = text.find("{"), text.rfind("}")
                if start != -1 and end != -1: text = text[start : end + 1]
                
            result = json.loads(text)
            if isinstance(result, list) and result: result = result[0]
            return result
        except Exception as e:
            logger.error(f"Gemini API (Pro) 分析失败: {e}")
            return None


async def rank_jobs(client: genai.Client, jobs: list[dict], semaphore: asyncio.Semaphore) -> list[dict]:
    """
    使用廉价的 Flash 模型对所有 Job 进行预排名，返回 Top 15。
    """
    if len(jobs) <= 15:
        return jobs

    logger.info(f"开启分级过滤：正在对 {len(jobs)} 个职位进行快速排名...")

    profile_text = _load_profile_as_text()

    async def get_rank(job, sem):
        prompt = f"""
        Role: Quick Job Assessor.
        Task: Score the match between the candidate and the job below (0-100).
        
        Candidate Profile:
        {profile_text}
        
        Job Title: {job['title']}
        Company: {job['company']}
        Snippet: {(job.get('jd_text') or '')[:500]}
        
        Scoring criteria:
        - 80-100: Strong match (Python/AI/Data/Backend/Full-stack, junior/intern/student level)
        - 50-79: Partial match (related tech but different stack or seniority mismatch)
        - 0-49: Weak match (non-technical, very senior, or unrelated domain)
        
        Output JSON: {{"score": 85}}
        """
        async with sem:
            try:
                resp = await client.aio.models.generate_content(
                    model=config.GEMINI_FLASH_MODEL,
                    contents=[prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                return json.loads(resp.text).get("score", 0)
            except:
                return 0

    scores = await asyncio.gather(*[get_rank(job, semaphore) for job in jobs])
    
    for job, score in zip(jobs, scores):
        job["rank_score"] = score
        
    # 按分数排序并取前 15
    ranked = sorted(jobs, key=lambda x: x["rank_score"], reverse=True)
    return ranked[:15]


async def analyze_pending_jobs(db: JobDatabase) -> int:
    """
    批量分析职位 (Async + 并发 + 分级)。
    """
    import asyncio
    
    all_new = db.get_jobs_by_status("new")
    relevant_jobs = [j for j in all_new if j.get("relevance") == "relevant"]
    
    if not relevant_jobs:
        logger.info("没有待分析的相关职位")
        return 0

    try:
        client = _init_client()
    except Exception as e:
        logger.error(f"Failed to initialize GenAI client: {e}")
        return 0

    # 1. 分级排名 (Flash)
    rank_semaphore = asyncio.Semaphore(15)
    top_jobs = await rank_jobs(client, relevant_jobs, rank_semaphore)
    
    logger.info(f"预排名完成，选取前 {len(top_jobs)} 个职位进行深度分析 (Gemini Pro)")

    # 2. 深度分析 (Pro)
    analyze_semaphore = asyncio.Semaphore(5)  # Pro 模型并发控制更严一点
    
    async def process_and_update(job):
        logger.info(f"深度分析: {job['title']} @ {job['company']}")
        analysis = await analyze_single_jd(client, job.get("jd_text", ""), analyze_semaphore)
        if analysis:
            db.update_job_analysis(job["id"], analysis)
            logger.info(f"  √ 匹配度: {analysis.get('match_score', 'N/A')}")
            return True
        return False

    results = await asyncio.gather(*[process_and_update(job) for job in top_jobs])
    return sum(results)
