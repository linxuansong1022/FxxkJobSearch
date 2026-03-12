"""
全局配置模块

所有可配置项集中管理：API密钥、搜索参数、文件路径等。
敏感信息（API Key）通过环境变量读取，不硬编码。
"""

import os
from pathlib import Path

# 读取本地 .env 文件
from dotenv import load_dotenv
load_dotenv()

# ============================================================
# 路径配置
# ============================================================
BASE_DIR = Path(__file__).parent
RESUME_DIR = BASE_DIR / "resume"
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(BASE_DIR / "output")))
TEMPLATE_PATH = RESUME_DIR / "template.tex"
PROFILE_PATH = BASE_DIR / "profile.yaml"
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "jobs.db")))

# ============================================================
# Google Cloud / Vertex AI 配置
# ============================================================
# 使用新的 google-genai 库所需的配置
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "cph-beer-map-dev")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")
GOOGLE_CLOUD_API_KEY = os.getenv("GOOGLE_CLOUD_API_KEY", "")

# 主分析模型
GEMINI_MODEL = "gemini-2.5-pro"

# 快速模型 (编排/过滤/Agent 日常使用)
GEMINI_FLASH_MODEL = "gemini-2.5-flash"

EMBEDDING_MODEL = "text-embedding-004"
EMBEDDING_DIMENSION = 768

# ============================================================
# Tavily Search API (替代 JobSpy)
# ============================================================
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TAVILY_SEARCH_CONFIG = {
    "time_range": "week",          # "day" / "week" / "month" — week覆盖更广
    "max_results_per_query": 20,
    "include_domains": [
        "linkedin.com/jobs",
        "indeed.com",
        "glassdoor.com",
        "wellfound.com",
        "jobindex.dk",
    ],
}
# ============================================================

# JobSpy 搜索关键词列表（轮询执行，降低单次频率）
SEARCH_QUERIES = [
    # === 核心匹配 (Python / AI) ===
    "Python Intern",
    "AI Engineer Intern",
    "Machine Learning Intern",
    "Data Science Student",
    "LLM Engineer Intern",
    "RAG Developer",
    # === 扩展覆盖 (Backend / Fullstack) ===
    "Backend Developer Intern",
    "Software Engineer Intern",
    "Full Stack Developer Intern",
    "Data Engineer Student",
    "Cloud Engineer Intern",
    "DevOps Intern",
    # === 丹麦语变体 ===
    "studiejob software",
    "praktikant data science",
    "studentermedhjælper IT",
    # === Research ===
    "Research Assistant Computer Science",
]

# JobSpy 通用参数
JOBSPY_CONFIG = {
    "location": "Denmark",
    "results_wanted": 20,       # 每次少量采集
    "hours_old": 24,            # 仅过去24小时
    "country_indeed": "denmark",
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
# 过滤配置
# ============================================================

# 标题中包含这些词的直接排除（不区分大小写）
TITLE_EXCLUDE_KEYWORDS = [
    "senior", "staff", "lead", "principal", "head of",
    "manager", "director", "vp ", "vice president",
    "hr ", "human resource", "marketing", "sales",
    "finance", "accounting", "legal", "counsel",
    "customer service", "customer success",
    "ux design", "ui design", "graphic design",
    "content", "social media", "influencer",
    "phd", "postdoc",
]

# 标题中包含这些词的优先保留（不区分大小写）
TITLE_INCLUDE_KEYWORDS = [
    "intern", "internship", "student", "junior",
    "graduate", "entry", "trainee", "apprentice",
    "studiejob", "praktik",  # 丹麦语：学生工、实习
]

# 领域相关关键词 — 标题或JD中应包含至少一个
DOMAIN_KEYWORDS = [
    "python", "ai", "artificial intelligence",
    "machine learning", "ml", "deep learning",
    "data scien", "data engineer", "data analyst",
    "llm", "nlp", "rag", "agent",
    "backend", "back-end", "software engineer",
    "software developer", "full-stack", "fullstack",
]

# 职位最大年龄（天），超过则过滤
MAX_JOB_AGE_DAYS = 7

# ============================================================
# 简历生成配置
# ============================================================
# 向量匹配时选取的 Top-N 条经历 bullet points
TOP_N_BULLETS = 6

# Tectonic 编译命令
TECTONIC_CMD = "tectonic"

# ============================================================
# Telegram 通知配置
# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# 轻量模式（低内存服务器: ≤ 512MB RAM）
# ============================================================
# 跳过 Playwright / Chromium，只用 Tavily + Jobindex + TheHub
# JD backfill 仅用 httpx，不启动浏览器
LIGHTWEIGHT_MODE = os.getenv("LIGHTWEIGHT_MODE", "false").lower() in ("true", "1", "yes")

# ============================================================
# 日志配置
# ============================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
