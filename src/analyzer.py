"""
JD 分析模块 (Vertex AI Gemini)

职责：
- 读取 status='new' 的职位
- 调用 Gemini 解析 JD，提取结构化信息
- 将分析结果写回数据库，状态更新为 'analyzed'

输出格式 (JSON):
{
    "hard_skills": ["Python", "PyTorch", ...],
    "soft_skills": ["team collaboration", ...],
    "experience_years": 0,
    "job_type": "internship",
    "is_remote": true,
    "company_domain": "AI/ML",
    "match_score": 0.85,        # 与用户背景的粗略匹配度 (0-1)
    "special_instructions": null # JD中的特殊要求
}
"""

import json
import logging

import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

import config
from src.database import JobDatabase

logger = logging.getLogger(__name__)

# JD 分析 Prompt
_ANALYSIS_PROMPT = """你是一个专业的IT技术招聘分析专家。请分析以下职位描述(JD)，并提取关键信息。

候选人背景：
- 丹麦技术大学 (DTU) 计算机科学硕士在读
- 技术栈：Python, PyTorch, Neo4j, Milvus, RAG, LLM, 嵌入式系统
- 寻找：实习 / 学生工 / 无薪实习

请基于以下JD提取信息，并评估与候选人的匹配度(0-1)：

JD内容：
{jd_text}

严格按照JSON格式输出，不要添加任何其他文字。
"""


def _init_gemini():
    """初始化 Vertex AI 并返回 Gemini 模型实例"""
    vertexai.init(project=config.GCP_PROJECT_ID, location=config.GCP_LOCATION)
    return GenerativeModel(config.GEMINI_MODEL)


def analyze_single_jd(model: GenerativeModel, jd_text: str) -> dict | None:
    """
    用 Gemini 分析单条 JD。

    Args:
        model: Gemini 模型实例
        jd_text: 职位描述纯文本

    Returns:
        解析后的字典，失败返回 None
    """
    if not jd_text or len(jd_text.strip()) < 50:
        logger.warning("JD 文本过短，跳过分析")
        return None

    prompt = _ANALYSIS_PROMPT.format(jd_text=jd_text[:8000])  # 截断过长的JD

    generation_config = GenerationConfig(
        temperature=0.1,
        response_mime_type="application/json",
    )

    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        result = json.loads(response.text)
        return result
    except json.JSONDecodeError as e:
        logger.error(f"Gemini 返回非法 JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"Gemini API 调用失败: {e}")
        return None


def analyze_pending_jobs(db: JobDatabase) -> int:
    """
    批量分析所有 status='new' 的职位。

    Returns:
        成功分析的职位数量
    """
    pending_jobs = db.get_jobs_by_status("new")
    if not pending_jobs:
        logger.info("没有待分析的职位")
        return 0

    logger.info(f"待分析职位: {len(pending_jobs)} 条")
    model = _init_gemini()
    analyzed_count = 0

    for job in pending_jobs:
        logger.info(f"分析: {job['title']} @ {job['company']}")
        analysis = analyze_single_jd(model, job.get("jd_text", ""))

        if analysis:
            db.update_job_analysis(job["id"], analysis)
            analyzed_count += 1
            logger.info(f"  匹配度: {analysis.get('match_score', 'N/A')}")
        else:
            # 分析失败，标记为 skipped 避免重复处理
            db.update_job_status(job["id"], "skipped")
            logger.warning(f"  分析失败，已跳过")

    return analyzed_count
