"""
简历导入模块 — 用 Gemini 解析简历 PDF 生成 profile.yaml

用法:
    python main.py import-resume path/to/resume.pdf
"""

import json
import logging
import os
from pathlib import Path

import yaml
from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

_EXTRACT_PROMPT = """你是一个专业的简历解析专家。请从以下简历中提取结构化信息，输出 YAML 格式。

要求：
1. 严格按照以下结构输出，不要添加额外字段
2. bullets 要保留原文（英文），不要翻译或改写
3. 如果某个字段在简历中找不到，用空字符串或空列表
4. dates 格式统一用 "Mon. Year -- Mon. Year" 形式

输出格式（纯 YAML，不要包含 ```yaml 标记）：

personal:
  name: "姓名"
  phone: "电话"
  email: "邮箱"
  linkedin: "LinkedIn URL"
  github: "GitHub URL"

education:
  - school: "学校名"
    degree: "学位"
    dates: "Sep. 2024 -- Jun. 2026"
    location: "城市, 国家"
    bullets:
      - "GPA: X.X"

experiences:
  - company: "公司名"
    role: "职位"
    dates: "Mon. Year -- Mon. Year"
    location: "城市, 国家"
    bullets:
      - "做了什么，用了什么技术，达到了什么效果"

projects:
  - name: "项目名"
    role: "角色"
    type: "Personal Project / Course Project / Competition"
    dates: "Mon. Year -- Mon. Year"
    bullets:
      - "项目描述"

skills:
  languages: "Python, Java, C++"
  frameworks: "FastAPI, PyTorch, LangChain"
  tools: "Git, Docker, Neo4j"
  spoken_languages: "English (IELTS 7), Chinese (Native)"
"""


def _init_client():
    """初始化 Gemini Client"""
    api_key = config.GOOGLE_CLOUD_API_KEY or os.environ.get("GOOGLE_CLOUD_API_KEY")
    if api_key:
        return genai.Client(vertexai=True, api_key=api_key)
    else:
        return genai.Client(
            vertexai=True,
            project=config.GCP_PROJECT_ID,
            location=config.GCP_LOCATION,
        )


def import_resume(pdf_path: str) -> Path:
    """
    解析简历 PDF，用 Gemini 提取结构化信息，写入 profile.yaml。

    Args:
        pdf_path: 简历 PDF 文件路径

    Returns:
        profile.yaml 的路径
    """
    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        raise FileNotFoundError(f"找不到文件: {pdf_path}")
    if not pdf_file.suffix.lower() == ".pdf":
        raise ValueError(f"只支持 PDF 文件，收到: {pdf_file.suffix}")

    logger.info(f"正在用 Gemini 解析简历: {pdf_file.name}")

    client = _init_client()

    # 读取 PDF 二进制
    pdf_bytes = pdf_file.read_bytes()

    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                types.Part.from_text(text=_EXTRACT_PROMPT),
            ],
        )
    ]

    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=8192,
        ),
    )

    raw_text = response.text.strip()

    # 清理可能的 markdown 包裹
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        # 去掉首行 ```yaml 和末行 ```
        lines = [l for l in lines if not l.strip().startswith("```")]
        raw_text = "\n".join(lines)

    # 验证是合法 YAML
    try:
        profile_data = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        logger.error(f"Gemini 输出的 YAML 解析失败: {e}")
        logger.error(f"原始输出:\n{raw_text[:500]}")
        raise ValueError("简历解析失败，Gemini 返回了无效的 YAML") from e

    # 验证基本结构
    required_keys = {"personal", "education", "experiences", "skills"}
    missing = required_keys - set(profile_data.keys())
    if missing:
        logger.warning(f"解析结果缺少字段: {missing}")

    # 加上注释头
    yaml_header = (
        "# ============================================================\n"
        "# 用户主档案 (Master Profile)\n"
        "#\n"
        "# 由 'python main.py import-resume' 自动从简历 PDF 生成。\n"
        "# 你可以手动编辑此文件来修正或补充信息。\n"
        "# analyzer.py 读取此文件来评估职位匹配度。\n"
        "# ============================================================\n\n"
    )

    output_path = config.PROFILE_PATH
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(yaml_header)
        yaml.dump(
            profile_data,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    logger.info(f"Profile 已保存到: {output_path}")

    # 打印摘要
    personal = profile_data.get("personal", {})
    edu_count = len(profile_data.get("education", []))
    exp_count = len(profile_data.get("experiences", []))
    proj_count = len(profile_data.get("projects", []))
    print(f"\n✅ 简历解析完成")
    print(f"   姓名: {personal.get('name', 'N/A')}")
    print(f"   教育: {edu_count} 段")
    print(f"   经历: {exp_count} 段")
    print(f"   项目: {proj_count} 个")
    print(f"   保存: {output_path}")
    print(f"\n💡 你可以手动编辑 {output_path} 来修正或补充信息。\n")

    return output_path
