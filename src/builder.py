"""
简历生成模块 (Jinja2 + Tectonic + google-genai)

职责：
1. 从数据库读取 status='analyzed' 的职位
2. 调用 matcher 选出最相关的经历
3. （可选）调用 LLM 对 bullet points 做微调重写
4. 用 Jinja2 渲染 LaTeX 模板
5. 用 Tectonic 编译为 PDF
"""

import json
import logging
import subprocess
import tempfile
import shutil
import os
from pathlib import Path

import jinja2
from google import genai
from google.genai import types

import config
from src.database import JobDatabase
from src.matcher import load_profile_bullets, load_profile, match_bullets_to_jd
from src.utils import escape_latex

logger = logging.getLogger(__name__)


# ============================================================
# Jinja2 LaTeX 环境
# ============================================================

def _create_latex_env() -> jinja2.Environment:
    return jinja2.Environment(
        block_start_string="\\BLOCK{",
        block_end_string="}",
        variable_start_string="\\VAR{",
        variable_end_string="}",
        comment_start_string="\\#{",
        comment_end_string="}",
        line_statement_prefix="%%",
        line_comment_prefix="%#",
        trim_blocks=True,
        autoescape=False,
        loader=jinja2.FileSystemLoader(str(config.RESUME_DIR)),
    )


# ============================================================
# LLM 重写（可选步骤）
# ============================================================

_REWRITE_PROMPT = """你是一个专业的简历写作专家。请根据目标职位的要求，微调以下简历bullet point，
使其更好地匹配职位需求。

要求：
1. 保持原文的核心事实不变，严禁编造不存在的技能或经历
2. 仅调整措辞和侧重点，使其更贴合JD需求
3. 保持专业的英文简历风格
4. 以动词开头(Developed, Engineered, Designed等)
5. 保留具体的数字和指标

目标职位技能需求: {skills}
原文: {original}

只输出修改后的bullet point，不要任何其他解释文字。"""


def rewrite_bullet(client: genai.Client, original: str, skills: list[str]) -> str:
    """
    用 Gemini 微调单条 bullet point。
    """
    prompt = _REWRITE_PROMPT.format(
        skills=", ".join(skills),
        original=original,
    )
    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(temperature=0.3),
        )
        rewritten = response.text.strip().strip('"').strip("'")
        if 20 < len(rewritten) < len(original) * 3:
            return rewritten
        return original
    except Exception as e:
        logger.warning(f"LLM 重写失败，使用原文: {e}")
        return original


# ============================================================
# Tectonic 编译
# ============================================================

def compile_latex(tex_content: str, output_path: Path) -> bool:
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = Path(tmpdir) / "resume.tex"
        tex_path.write_text(tex_content, encoding="utf-8")

        try:
            result = subprocess.run(
                [config.TECTONIC_CMD, str(tex_path)],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=tmpdir,
            )

            if result.returncode != 0:
                logger.error(f"Tectonic 编译失败:\n{result.stderr}")
                return False

            pdf_path = tex_path.with_suffix(".pdf")
            if pdf_path.exists():
                shutil.move(str(pdf_path), str(output_path))
                logger.info(f"PDF 生成成功: {output_path}")
                return True
            else:
                logger.error("Tectonic 运行成功但未生成 PDF")
                return False

        except subprocess.TimeoutExpired:
            logger.error("Tectonic 编译超时 (60s)")
            return False
        except FileNotFoundError:
            logger.error("Tectonic 未安装。请安装: brew install tectonic")
            return False


# ============================================================
# 简历生成主流程
# ============================================================

def generate_single_resume(
    job: dict,
    profile: dict,
    bullets: list[dict],
    latex_env: jinja2.Environment,
    client: genai.Client | None = None,
) -> str | None:
    raw_analysis = json.loads(job.get("analysis", "{}"))
    if isinstance(raw_analysis, list) and raw_analysis:
        analysis = raw_analysis[0] if isinstance(raw_analysis[0], dict) else {}
    elif isinstance(raw_analysis, dict):
        analysis = raw_analysis
    else:
        analysis = {}
        
    hard_skills = (
        analysis.get("hard_skills")
        or analysis.get("required_skills")
        or analysis.get("skills")
        or []
    )

    matched = match_bullets_to_jd(bullets, analysis)

    final_bullets = []
    for b in matched:
        if client and hard_skills:
            rewritten = rewrite_bullet(client, b["text"], hard_skills)
        else:
            rewritten = b["text"]
        final_bullets.append({
            **b,
            "display_text": escape_latex(rewritten),
        })

    render_experiences = []
    for exp in profile.get("experiences", []):
        matched = [b["display_text"] for b in final_bullets
                   if b["source"] == exp.get("company") and b["category"] == "experience"]
        if not matched:
            matched = [escape_latex(b) for b in exp.get("bullets", [])]
        render_experiences.append({
            "company": escape_latex(exp.get("company", "")),
            "role": escape_latex(exp.get("role", "")),
            "dates": exp.get("dates", ""),
            "location": escape_latex(exp.get("location", "")),
            "bullets": matched,
        })

    render_projects = []
    for proj in profile.get("projects", []):
        matched = [b["display_text"] for b in final_bullets
                   if b["source"] == proj.get("name") and b["category"] == "project"]
        if not matched:
            matched = [escape_latex(b) for b in proj.get("bullets", [])]
        render_projects.append({
            "name": escape_latex(proj.get("name", "")),
            "role": escape_latex(proj.get("role", "")),
            "type": escape_latex(proj.get("type", "")),
            "dates": proj.get("dates", ""),
            "bullets": matched,
        })

    try:
        template = latex_env.get_template("template.tex")
        tex_content = template.render(
            personal=profile["personal"],
            education=profile["education"],
            skills=profile.get("skills", {}),
            render_experiences=render_experiences,
            render_projects=render_projects,
            target_job=job,
            target_analysis=analysis,
        )
    except jinja2.TemplateError as e:
        logger.error(f"Jinja2 渲染失败: {e}")
        return None

    safe_name = f"{job['id']}_{job['company'][:20]}_{job['title'][:30]}".replace(" ", "_").replace("/", "-")
    output_path = config.OUTPUT_DIR / f"{safe_name}.pdf"

    if compile_latex(tex_content, output_path):
        return str(output_path)
    return None


def generate_resumes(db: JobDatabase) -> int:
    analyzed_jobs = db.get_jobs_by_status("analyzed")
    if not analyzed_jobs:
        logger.info("没有待生成简历的职位")
        return 0

    logger.info(f"待生成简历: {len(analyzed_jobs)} 条")

    profile = load_profile()
    bullets = load_profile_bullets()
    latex_env = _create_latex_env()

    try:
        api_key = config.GOOGLE_CLOUD_API_KEY or os.environ.get("GOOGLE_CLOUD_API_KEY")
        client = genai.Client(
            vertexai=True,
            api_key=api_key,
            project=config.GCP_PROJECT_ID,
            location=config.GCP_LOCATION
        )
    except Exception:
        logger.warning("Gemini Client 初始化失败，将跳过 bullet 重写步骤")
        client = None

    generated_count = 0
    for job in analyzed_jobs:
        logger.info(f"生成简历: {job['title']} @ {job['company']}")
        pdf_path = generate_single_resume(job, profile, bullets, latex_env, client)

        if pdf_path:
            db.update_job_resume(job["id"], pdf_path)
            generated_count += 1
        else:
            logger.warning(f"  简历生成失败")

    return generated_count
