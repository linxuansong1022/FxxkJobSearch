# FxxkJobSearch → Agent 改造实现计划

**版本**: v2.0 Agent Architecture  
**基于**: 现有 v0.5.0 代码库  
**目标**: 将线性 Pipeline 改造为 LLM 驱动的自主 Agent，同时提升抓取覆盖率

---

## 1. 核心思路：现在 vs 改造后

### 现在（线性 Pipeline）
```
main.py 硬编码顺序：
scrape → filter → backfill → analyze → notify
每一步都是你预先写死的，LLM 只负责分析，不负责决策
```

### 改造后（Agent Loop）
```
Agent 自主决定：
- 今天要抓哪些平台？
- 这个 JD 太短，要不要去官网补全？
- 这个职位值不值得深度分析？
- 今天有没有足够质量的职位发通知？
LLM 负责决策，Tools 负责执行
```

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────┐
│                  agent.py (Agent Loop)              │
│         Gemini function calling 驱动决策             │
├─────────────────────────────────────────────────────┤
│                    Tools Layer                      │
│  scrape_linkedin │ scrape_thehub │ scrape_jobindex  │
│  scrape_company_careers │ fetch_job_detail          │
│  filter_jobs │ analyze_job │ send_notification      │
│  get_db_status │ search_company_career_url          │
├─────────────────────────────────────────────────────┤
│              database.py (SQLite - 不变)             │
├─────────────────────────────────────────────────────┤
│         config.py + profile.yaml (不变)              │
└─────────────────────────────────────────────────────┘
```

### 新增文件结构
```
FxxkJobSearch/
├── agent.py                    # ← 新增：Agent 主入口
├── src/
│   ├── tools.py                # ← 新增：所有 Tool 定义和注册
│   ├── scraper_jobindex.py     # ← 新增：Jobindex 抓取
│   ├── scraper_careers.py      # ← 新增：公司官网抓取
│   ├── scraper.py              # 保留，改造为 Tool
│   ├── filter.py               # 保留，改造为 Tool
│   ├── analyzer.py             # 保留，改造为 Tool
│   ├── notifier.py             # 保留，改造为 Tool
│   └── ...                     # 其他保持不变
├── company_list.py             # ← 新增：读取 Google Sheets
└── main.py                     # 保留，加入 agent 命令
```

---

## 3. 实现步骤（按优先级排序）

---

### Step 1：新增数据源（最高优先级，直接解决覆盖率问题）

#### 3.1 Jobindex 抓取（`src/scraper_jobindex.py`）

Jobindex 有公开的搜索接口，无需登录，无反爬：

```python
import httpx
from bs4 import BeautifulSoup
from src.utils import compute_job_hash, clean_html
import config

JOBINDEX_BASE = "https://www.jobindex.dk/jobsoegning"

JOBINDEX_QUERIES = [
    {"q": "python intern", "area": "storkoebenhavn"},
    {"q": "data science studiejob", "area": "storkoebenhavn"},
    {"q": "machine learning praktikant"},
    {"q": "software developer studentermedhjælper"},
    {"q": "AI engineer intern denmark"},
]

def scrape_jobindex(db) -> int:
    new_count = 0
    headers = {"User-Agent": "Mozilla/5.0 ..."}
    
    for query_params in JOBINDEX_QUERIES:
        params = {**query_params, "maxdate": 7}  # 7天内
        resp = httpx.get(JOBINDEX_BASE, params=params, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Jobindex 职位列表在 .PaidJob 和 .jix-toolbar__link 中
        for item in soup.select(".jobsearch-result"):
            title_el = item.select_one("h4 a")
            company_el = item.select_one(".jix-toolbar-top__company")
            if not title_el or not company_el:
                continue
            
            title = title_el.get_text(strip=True)
            company = company_el.get_text(strip=True)
            url = "https://www.jobindex.dk" + title_el.get("href", "")
            posted_at = item.select_one("time")
            
            job_data = {
                "platform": "jobindex",
                "platform_id": url.split("/")[-1],
                "title": title,
                "company": company,
                "url": url,
                "content_hash": compute_job_hash(company, title),
                "jd_text": "",  # 需要进入详情页才有，backfill 处理
                "posted_at": posted_at.get("datetime") if posted_at else None,
            }
            if db.insert_job(job_data):
                new_count += 1
    
    return new_count
```

#### 3.2 公司官网抓取（`src/scraper_careers.py`）

分两阶段：发现 career URL → 抓取岗位

```python
import httpx
from bs4 import BeautifulSoup
from google import genai
import config

# 阶段一：从官网发现 career 页面 URL
CAREER_KEYWORDS = [
    "careers", "jobs", "job", "work with us", "join us",
    "join our team", "we're hiring", "karriere", "ledige stillinger",
    "studiejob", "praktik"
]

def discover_career_url(company_website: str) -> str | None:
    """
    从公司官网首页找 career 页面链接。
    策略：找 <a> 标签中含有 career 关键词的链接。
    """
    try:
        resp = httpx.get(company_website, timeout=10, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")
        
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            href = a["href"].lower()
            if any(kw in text or kw in href for kw in CAREER_KEYWORDS):
                # 补全相对 URL
                if href.startswith("http"):
                    return a["href"]
                else:
                    from urllib.parse import urljoin
                    return urljoin(company_website, a["href"])
    except Exception:
        return None
    return None


# 阶段二：抓取 career 页面，用 Gemini 提取岗位列表
def extract_jobs_from_career_page(
    client: genai.Client,
    career_url: str,
    company: str
) -> list[dict]:
    """
    用 Playwright 获取页面内容，用 Gemini 提取职位列表。
    """
    try:
        # 动态页面用 playwright
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(career_url, timeout=15000)
            page.wait_for_timeout(2000)  # 等待 JS 渲染
            html = page.content()
            browser.close()
        
        soup = BeautifulSoup(html, "html.parser")
        # 去掉 script/style，减少 token
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)[:8000]
        
    except Exception as e:
        return []
    
    # 让 Gemini 提取职位
    prompt = f"""
    以下是 {company} 公司 career 页面的文本内容。
    请提取所有招聘职位，返回 JSON 数组：
    [{{"title": "职位名", "url": "申请链接或空字符串", "is_intern": true/false}}]
    只返回 JSON，不要其他文字。
    
    页面内容：
    {text}
    """
    
    try:
        response = client.models.generate_content(
            model=config.GEMINI_FLASH_MODEL,
            contents=[prompt],
            config={"response_mime_type": "application/json"}
        )
        jobs = json.loads(response.text)
        return jobs if isinstance(jobs, list) else []
    except Exception:
        return []
```

#### 3.3 Google Sheets 读取（`company_list.py`）

```python
import httpx
import csv
import io

def load_companies_from_sheets(sheet_url: str) -> list[dict]:
    """
    读取 Google Sheets（需要设置为"任何人可查看"）。
    Sheet URL 格式：https://docs.google.com/spreadsheets/d/{ID}/export?format=csv
    """
    # 将普通 URL 转换为 CSV 导出 URL
    if "/edit" in sheet_url:
        sheet_id = sheet_url.split("/d/")[1].split("/")[0]
        csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    else:
        csv_url = sheet_url
    
    resp = httpx.get(csv_url, timeout=15)
    reader = csv.DictReader(io.StringIO(resp.text))
    
    companies = []
    for row in reader:
        companies.append({
            "name": row.get("Company Name", ""),
            "website": row.get("Website", ""),
            "career_url": row.get("Career URL", ""),  # 可能为空
        })
    return companies
```

---

### Step 2：将所有功能封装为 Tools（`src/tools.py`）

这是 Agent 化的核心。每个 Tool 需要：
1. 实际执行函数
2. Gemini function calling 的 schema 描述

```python
from google.genai import types

# ============================================================
# Tool 函数定义
# ============================================================

def tool_scrape_linkedin(db, keywords: list[str] = None) -> dict:
    """执行 LinkedIn + Indeed 抓取"""
    from src.scraper import scrape_jobspy
    count = scrape_jobspy(db, keywords=keywords)
    return {"status": "success", "new_jobs": count, "platform": "linkedin/indeed"}

def tool_scrape_thehub(db, keywords: list[str] = None) -> dict:
    from src.scraper import scrape_thehub
    count = scrape_thehub(db, keywords=keywords)
    return {"status": "success", "new_jobs": count, "platform": "thehub"}

def tool_scrape_jobindex(db, queries: list[str] = None) -> dict:
    from src.scraper_jobindex import scrape_jobindex
    count = scrape_jobindex(db, queries=queries)
    return {"status": "success", "new_jobs": count, "platform": "jobindex"}

def tool_scrape_company_careers(db, company_name: str, career_url: str) -> dict:
    """抓取单个公司官网的职位"""
    from src.scraper_careers import extract_jobs_from_career_page
    jobs = extract_jobs_from_career_page(career_url, company_name)
    inserted = 0
    for job in jobs:
        if db.insert_job({...}):
            inserted += 1
    return {"status": "success", "new_jobs": inserted, "company": company_name}

def tool_get_db_status(db) -> dict:
    """让 Agent 了解当前数据库状态，决定下一步"""
    return {
        "status_counts": db.get_status_counts(),
        "relevance_counts": db.get_relevance_counts(),
        "pending_filter": len(db.get_unscored_jobs()),
        "pending_analyze": len([j for j in db.get_jobs_by_status("new") 
                                if j.get("relevance") == "relevant"]),
    }

def tool_filter_jobs(db) -> dict:
    from src.filter import filter_jobs
    counts = filter_jobs(db)
    return {"status": "success", **counts}

def tool_analyze_jobs(db) -> dict:
    from src.analyzer import analyze_pending_jobs
    count = analyze_pending_jobs(db)
    return {"status": "success", "analyzed": count}

def tool_fetch_job_detail(db, job_id: int, url: str) -> dict:
    """补全单个职位的 JD（Agent 判断 JD 太短时调用）"""
    import httpx
    from src.utils import clean_html
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        text = clean_html(resp.text)[:5000]
        db.update_job_jd(job_id, text)
        return {"status": "success", "jd_length": len(text)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def tool_send_notification(db) -> dict:
    from src.notifier import send_daily_report
    send_daily_report(db)
    return {"status": "success"}


# ============================================================
# Gemini Function Calling Schema
# ============================================================

TOOL_SCHEMAS = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="scrape_linkedin",
            description="从 LinkedIn 和 Indeed 抓取职位。当需要获取新职位时调用。",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "keywords": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING),
                        description="搜索关键词列表，不传则使用默认配置"
                    )
                }
            )
        ),
        types.FunctionDeclaration(
            name="scrape_thehub",
            description="从 The Hub 抓取丹麦创业公司职位。",
            parameters=types.Schema(type=types.Type.OBJECT, properties={})
        ),
        types.FunctionDeclaration(
            name="scrape_jobindex",
            description="从 Jobindex 抓取丹麦本地职位，覆盖更多本地公司。",
            parameters=types.Schema(type=types.Type.OBJECT, properties={})
        ),
        types.FunctionDeclaration(
            name="get_db_status",
            description="获取数据库当前状态，包括各状态职位数量。用于决定下一步操作。",
            parameters=types.Schema(type=types.Type.OBJECT, properties={})
        ),
        types.FunctionDeclaration(
            name="filter_jobs",
            description="对未评分的职位做相关性过滤，分为 relevant/irrelevant。",
            parameters=types.Schema(type=types.Type.OBJECT, properties={})
        ),
        types.FunctionDeclaration(
            name="analyze_jobs",
            description="用 Gemini 深度分析相关职位的 JD，计算匹配度评分。",
            parameters=types.Schema(type=types.Type.OBJECT, properties={})
        ),
        types.FunctionDeclaration(
            name="fetch_job_detail",
            description="当某个职位的 JD 内容太短或缺失时，访问原始链接补全 JD。",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "job_id": types.Schema(type=types.Type.INTEGER, description="职位 ID"),
                    "url": types.Schema(type=types.Type.STRING, description="职位原始链接")
                },
                required=["job_id", "url"]
            )
        ),
        types.FunctionDeclaration(
            name="send_notification",
            description="发送 Telegram 日报通知，推送高匹配度职位。",
            parameters=types.Schema(type=types.Type.OBJECT, properties={})
        ),
    ])
]


# Tool 路由表
TOOL_REGISTRY = {
    "scrape_linkedin": tool_scrape_linkedin,
    "scrape_thehub": tool_scrape_thehub,
    "scrape_jobindex": tool_scrape_jobindex,
    "get_db_status": tool_get_db_status,
    "filter_jobs": tool_filter_jobs,
    "analyze_jobs": tool_analyze_jobs,
    "fetch_job_detail": tool_fetch_job_detail,
    "send_notification": tool_send_notification,
}
```

---

### Step 3：Agent 主循环（`agent.py`）

这是整个改造的核心文件：

```python
"""
FxxkJobSearch Agent v2.0

使用 Gemini function calling 驱动自主决策。
Agent 根据当前状态决定调用哪些 Tools，完成完整的求职自动化流程。
"""

import json
import logging
from google import genai
from google.genai import types

import config
from src.database import JobDatabase
from src.tools import TOOL_SCHEMAS, TOOL_REGISTRY

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个专业的求职自动化 Agent，负责帮助一位 DTU 计算机科学硕士生在丹麦寻找 Python/AI/Backend 相关的实习和学生工作。

你的工作流程：
1. 首先调用 get_db_status 了解当前数据库状态
2. 根据状态决定是否需要抓取新职位（调用各平台 scraper）
3. 对新抓取的职位进行过滤（filter_jobs）
4. 如果发现某些职位 JD 太短，主动补全（fetch_job_detail）
5. 对相关职位进行深度分析（analyze_jobs）
6. 最后发送 Telegram 通知（send_notification）

决策原则：
- 如果数据库中没有新职位，优先从 Jobindex 和 The Hub 抓取（更稳定）
- LinkedIn 每次抓取后要等待，避免频繁请求
- 只有当 JD 文本少于 100 字时才调用 fetch_job_detail 补全
- 如果今日分析结果中没有匹配度 > 0.6 的职位，不发送通知（避免骚扰）
- 遇到错误不要停止，记录后继续执行其他步骤

每次 Tool 调用后，分析结果并决定下一步，直到完成完整流程。"""


def run_agent():
    """运行 Agent 主循环"""
    db = JobDatabase(config.DB_PATH)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 初始化 Gemini Client
    client = genai.Client(
        vertexai=True,
        project=config.GCP_PROJECT_ID,
        location=config.GCP_LOCATION
    )
    
    messages = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(
                "请开始今日的求职自动化流程。首先检查数据库状态，然后根据情况决定需要执行哪些操作。"
            )]
        )
    ]
    
    max_iterations = 20  # 防止无限循环
    iteration = 0
    
    logger.info("Agent 启动...")
    
    while iteration < max_iterations:
        iteration += 1
        logger.info(f"Agent 迭代 #{iteration}")
        
        # 调用 Gemini
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=messages,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                temperature=0.3,
            )
        )
        
        # 把 Assistant 的响应加入历史
        messages.append(types.Content(
            role="model",
            parts=response.candidates[0].content.parts
        ))
        
        # 检查是否有 function call
        tool_calls = [
            part for part in response.candidates[0].content.parts
            if hasattr(part, "function_call") and part.function_call
        ]
        
        if not tool_calls:
            # 没有 tool call，Agent 认为任务完成
            final_text = response.text
            logger.info(f"Agent 完成：{final_text}")
            break
        
        # 执行所有 tool calls
        tool_results = []
        for part in tool_calls:
            fc = part.function_call
            tool_name = fc.name
            tool_args = dict(fc.args) if fc.args else {}
            
            logger.info(f"  调用 Tool: {tool_name}({tool_args})")
            
            # 执行 tool
            tool_fn = TOOL_REGISTRY.get(tool_name)
            if tool_fn:
                try:
                    # 注入 db 参数
                    result = tool_fn(db, **tool_args)
                except Exception as e:
                    result = {"status": "error", "message": str(e)}
                    logger.error(f"  Tool 执行失败: {e}")
            else:
                result = {"status": "error", "message": f"Unknown tool: {tool_name}"}
            
            logger.info(f"  Tool 结果: {result}")
            
            tool_results.append(
                types.Part.from_function_response(
                    name=tool_name,
                    response=result
                )
            )
        
        # 把 tool 结果加入历史
        messages.append(types.Content(
            role="user",
            parts=tool_results
        ))
    
    db.close()
    logger.info("Agent 运行完成")
```

---

### Step 4：更新 `main.py`

在现有命令基础上加入 `agent` 命令：

```python
# 在 commands 字典中加入：
from agent import run_agent

commands = {
    ...现有命令...,
    "agent": lambda db: run_agent(),  # 新增
}

# argparse choices 加入 "agent"
```

---

## 4. 数据流对比

### 改造前
```
[固定顺序，无论数据库状态如何]
scrape → filter → backfill → analyze → notify
```

### 改造后
```
[Agent 根据状态动态决策]
get_db_status
  ↓
  如果有大量未处理职位 → 先 filter
  如果新职位不足 → scrape (选择平台)
  如果 JD 太短 → fetch_job_detail (按需)
  如果有待分析职位 → analyze
  如果有高分职位 → send_notification
  如果全部完成 → 结束
```

---

## 5. 简历上的描述建议

**项目标题**: Job Search Automation Agent

**描述**:
> Architected an autonomous multi-source job search agent leveraging Gemini's function calling capabilities to dynamically orchestrate tool execution across LinkedIn, The Hub, Jobindex, and 200+ company career pages. The agent performs real-time decision-making on data collection strategy, JD quality assessment, semantic relevance scoring via vector embeddings, and Telegram delivery — replacing a static pipeline with an adaptive workflow that self-adjusts based on database state.

**技术栈关键词**:
- LLM Agent / Function Calling / Tool Use
- Multi-source Web Scraping (Playwright, httpx)
- Vector Embeddings (Vertex AI text-embedding-004)
- SQLite / Data Pipeline
- Telegram Bot API
- Google Gemini (Vertex AI)

---

## 6. 实施顺序建议

| 周次 | 任务 | 预计时间 |
|------|------|---------|
| Week 1 | 实现 Jobindex scraper + 测试覆盖率提升 | 1-2天 |
| Week 1 | 实现 Google Sheets 读取 + career URL 发现 | 1天 |
| Week 2 | 封装所有模块为 Tools（`src/tools.py`） | 1天 |
| Week 2 | 实现 Agent 主循环（`agent.py`） | 1-2天 |
| Week 2 | 实现公司官网 Playwright 抓取 | 1-2天 |
| Week 3 | 测试 + 调优 Agent system prompt | 持续 |
| Week 3 | 部署到云服务器 + cron | 0.5天 |

**建议先做 Jobindex + Tools 封装，这两步收益最大，也是 Agent 改造的基础。**

---

## 7. 注意事项

**关于 Gemini 模型名**：
`config.py` 里的 `gemini-3-pro-preview` 目前可能不存在，建议改为 `gemini-2.0-flash-exp` 或 `gemini-1.5-pro`，运行前先确认。

**关于 Vertex AI vs API Key**：
`vertexai=True` 和 `api_key` 是互斥的。建议统一用 ADC：
```bash
gcloud auth application-default login
```
然后只传 `project` 和 `location`，不传 `api_key`。

**关于 playwright 安装**：
```bash
pip install playwright
playwright install chromium
```

**关于 Agent 稳定性**：
Agent loop 里一定要设 `max_iterations` 上限，避免 Gemini 陷入无限循环。System prompt 要写清楚"任务完成后停止"的条件。
