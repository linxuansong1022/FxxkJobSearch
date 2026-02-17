"""
简历生成模块 (Jinja2 + Tectonic)

职责：
1. 从数据库读取 status='analyzed' 的职位
2. 调用 matcher 选出最相关的经历
3. （可选）调用 LLM 对 bullet points 做微调重写
4. 用 Jinja2 渲染 LaTeX 模板
5. 用 Tectonic 编译为 PDF
6. 输出到 output/ 目录

关键设计：
- Jinja2 使用自定义定界符，避免与 LaTeX {} 冲突
- 所有动态文本经过 escape_latex() 转义
- 编译失败时记录日志，不中断主流程
"""

import json
import logging
import subprocess
import tempfile
import shutil
from pathlib import Path

import jinja2
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

import config
from src.database import JobDatabase
from src.matcher import load_profile_bullets, load_profile, match_bullets_to_jd
from src.utils import escape_latex

logger = logging.getLogger(__name__)


# ============================================================
# Jinja2 LaTeX 环境（自定义定界符）
# ============================================================

def _create_latex_env() -> jinja2.Environment:
    """
    创建 Jinja2 环境，使用自定义定界符避免与 LaTeX 冲突。

    LaTeX 用 {}，所以 Jinja2 改用：
    - 变量: \\VAR{...}
    - 块:   \\BLOCK{...}
    - 注释: \\#{...}
    """
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
        autoescape=False,  # LaTeX 不需要 HTML 转义
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


def rewrite_bullet(model: GenerativeModel, original: str, skills: list[str]) -> str:
    """
    用 Gemini 微调单条 bullet point 使其更匹配目标 JD。

    如果 API 调用失败，返回原文（降级策略）。
    """
    prompt = _REWRITE_PROMPT.format(
        skills=", ".join(skills),
        original=original,
    )
    try:
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(temperature=0.3),
        )
        rewritten = response.text.strip().strip('"').strip("'")
        # 简单校验：重写结果不能太短或太长
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
    """
    用 Tectonic 将 LaTeX 源码编译为 PDF。

    流程：
    1. 在临时目录创建 .tex 文件
    2. 调用 tectonic 编译
    3. 将生成的 PDF 移动到目标路径

    Args:
        tex_content: 渲染后的完整 LaTeX 源码
        output_path: PDF 输出路径

    Returns:
        True 编译成功, False 失败
    """
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

            # 编译成功，移动 PDF 到目标路径
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
            logger.error(
                "Tectonic 未安装。请安装: brew install tectonic (macOS) "
                "或 cargo install tectonic"
            )
            return False


# ============================================================
# 简历生成主流程
# ============================================================

def generate_single_resume(
    job: dict,
    profile: dict,
    bullets: list[dict],
    latex_env: jinja2.Environment,
    llm_model: GenerativeModel | None = None,
) -> str | None:
    """
    为单个职位生成定制简历 PDF。

    Args:
        job: 数据库中的 job 记录
        profile: profile.yaml 的完整数据
        bullets: load_profile_bullets() 的返回值
        latex_env: Jinja2 环境
        llm_model: Gemini 模型（用于重写，可为 None 跳过重写）

    Returns:
        生成的 PDF 路径字符串，失败返回 None
    """
    analysis = json.loads(job.get("analysis", "{}"))
    hard_skills = analysis.get("hard_skills", [])

    # 步骤1：向量匹配，选出最相关的经历
    matched = match_bullets_to_jd(bullets, analysis)

    # 步骤2：（可选）LLM 重写 bullet points
    final_bullets = []
    for b in matched:
        if llm_model and hard_skills:
            rewritten = rewrite_bullet(llm_model, b["text"], hard_skills)
        else:
            rewritten = b["text"]
        final_bullets.append({
            **b,
            "display_text": escape_latex(rewritten),
        })

    # 步骤3：按来源分组（experience vs project）
    exp_bullets = [b for b in final_bullets if b["category"] == "experience"]
    proj_bullets = [b for b in final_bullets if b["category"] == "project"]

    # 步骤4：渲染 Jinja2 模板
    try:
        template = latex_env.get_template("template.tex")
        tex_content = template.render(
            personal=profile["personal"],
            education=profile["education"],
            experiences=profile.get("experiences", []),
            projects=profile.get("projects", []),
            skills=profile.get("skills", {}),
            matched_exp_bullets=exp_bullets,
            matched_proj_bullets=proj_bullets,
            target_job=job,
            target_analysis=analysis,
        )
    except jinja2.TemplateError as e:
        logger.error(f"Jinja2 渲染失败: {e}")
        return None

    # 步骤5：编译 PDF
    safe_name = f"{job['id']}_{job['company'][:20]}_{job['title'][:30]}".replace(" ", "_").replace("/", "-")
    output_path = config.OUTPUT_DIR / f"{safe_name}.pdf"

    if compile_latex(tex_content, output_path):
        return str(output_path)
    return None


def generate_resumes(db: JobDatabase) -> int:
    """
    批量为所有 status='analyzed' 的职位生成定制简历。

    Returns:
        成功生成的简历数量
    """
    analyzed_jobs = db.get_jobs_by_status("analyzed")
    if not analyzed_jobs:
        logger.info("没有待生成简历的职位")
        return 0

    logger.info(f"待生成简历: {len(analyzed_jobs)} 条")

    # 加载共享资源（只加载一次）
    profile = load_profile()
    bullets = load_profile_bullets()
    latex_env = _create_latex_env()

    # 初始化 Gemini 用于重写（可选，设为 None 则跳过重写步骤）
    try:
        vertexai.init(project=config.GCP_PROJECT_ID, location=config.GCP_LOCATION)
        llm_model = GenerativeModel(config.GEMINI_MODEL)
    except Exception:
        logger.warning("Gemini 初始化失败，将跳过 bullet 重写步骤")
        llm_model = None

    generated_count = 0
    for job in analyzed_jobs:
        logger.info(f"生成简历: {job['title']} @ {job['company']}")
        pdf_path = generate_single_resume(job, profile, bullets, latex_env, llm_model)

        if pdf_path:
            db.update_job_resume(job["id"], pdf_path)
            generated_count += 1
        else:
            logger.warning(f"  简历生成失败")

    return generated_count
