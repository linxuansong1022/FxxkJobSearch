"""
全局配置模块

所有可配置项集中管理：API密钥、搜索参数、文件路径等。
敏感信息（API Key）通过环境变量读取，不硬编码。
"""

import os
from pathlib import Path

# ============================================================
# 路径配置
# ============================================================
BASE_DIR = Path(__file__).parent
RESUME_DIR = BASE_DIR / "resume"
OUTPUT_DIR = BASE_DIR / "output"
TEMPLATE_PATH = RESUME_DIR / "template.tex"
PROFILE_PATH = BASE_DIR / "profile.yaml"
DB_PATH = BASE_DIR / "jobs.db"

# ============================================================
# Google Cloud / Vertex AI 配置
# ============================================================
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")
GEMINI_MODEL = "gemini-2.0-flash"  # 或 gemini-1.5-pro
EMBEDDING_MODEL = "text-embedding-004"
EMBEDDING_DIMENSION = 768

# ============================================================
# 采集配置
# ============================================================

# JobSpy 搜索关键词列表（轮询执行，降低单次频率）
SEARCH_QUERIES = [
    "Python Intern",
    "AI Engineer Intern",
    "Machine Learning Intern",
    "Data Science Student",
    "LLM Engineer Intern",
    "RAG Developer",
]

# JobSpy 通用参数
JOBSPY_CONFIG = {
    "location": "Denmark",
    "results_wanted": 20,       # 每次少量采集
    "hours_old": 24,            # 仅过去24小时
    "country_indeed": "dk",
}

# The Hub API 配置
THEHUB_CONFIG = {
    "base_url": "https://thehub.io/api/jobs",
    "params": {
        "countryCode": "DK",
        "positionType": "internship",
        "orderBy": "published",
        "status": "active",
    },
    "keywords": ["python", "data science", "ai", "machine learning"],
}

# ============================================================
# 简历生成配置
# ============================================================
# 向量匹配时选取的 Top-N 条经历 bullet points
TOP_N_BULLETS = 6

# Tectonic 编译命令
TECTONIC_CMD = "tectonic"

# ============================================================
# 日志配置
# ============================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
