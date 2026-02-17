# LinkedIn Agent — 个人求职自动化工具

## 1. 项目定位

个人使用的CLI工具，自动完成：**职位采集 → JD分析 → 技能匹配 → 定制简历PDF生成**。

- **目标岗位**：Python后端、AI/LLM工程、Agent/RAG开发、Data Science
- **雇佣形式**：Internship、Student Job、Unpaid
- **地理范围**：丹麦 (Denmark) 或 Remote
- **使用频率**：每日运行一次

> 不做自动投递。系统只负责"搜集+生成"，投递由用户手动完成。这已解决80%痛点。

---

## 2. 系统架构

```
┌─────────────────────────────────────────────────┐
│                    main.py (CLI)                │
│         scrape / analyze / generate / run       │
├──────────┬──────────┬───────────┬───────────────┤
│ scraper  │ analyzer │  matcher  │   builder     │
│ (采集层) │ (分析层)  │ (匹配层)  │  (生成层)      │
├──────────┴──────────┴───────────┴───────────────┤
│              database.py (SQLite)               │
├─────────────────────────────────────────────────┤
│     config.py + profile.yaml (配置 & 用户数据)   │
└─────────────────────────────────────────────────┘
```

### 目录结构

```
linkedin-agent/
├── src/
│   ├── __init__.py
│   ├── scraper.py        # 多平台职位采集 (JobSpy + The Hub)
│   ├── analyzer.py       # Gemini API 解析JD
│   ├── matcher.py        # 向量相似度匹配经历
│   ├── builder.py        # Jinja2渲染 + Tectonic编译PDF
│   ├── database.py       # SQLite 存储与去重
│   └── utils.py          # LaTeX转义等工具函数
├── resume/
│   ├── resume.tex        # 原始简历 (参考)
│   └── template.tex      # Jinja2模板 (动态生成用)
├── output/               # 生成的PDF输出目录
├── config.py             # 全局配置
├── profile.yaml          # 用户主档案 (经历、技能、个人信息)
├── main.py               # CLI入口
└── requirements.txt
```

---

## 3. 核心模块设计

### 3.1 采集层 (scraper.py)

两个数据源，混合使用：

| 平台 | 方案 | 说明 |
|------|------|------|
| **LinkedIn + Indeed** | `python-jobspy` 库 | 聚合采集，自带反爬处理 |
| **The Hub** | `httpx` 直调API | 北欧创业平台，返回JSON，无反爬 |

**关键参数**：
- LinkedIn: `search_term="Python AI Intern"`, `location="Denmark"`, `results_wanted=20`
- Indeed: `sc=0kf:jt(internship);`, `fromage=1` (仅过去24h)
- The Hub: `countryCode=DK`, `positionType=internship`

**策略**：少量多次（每次20条），浅层搜索代替深层翻页。

### 3.2 分析层 (analyzer.py)

使用 **Google Vertex AI (Gemini)** 解析JD，提取结构化信息：

```json
{
  "hard_skills": ["Python", "PyTorch", "Docker"],
  "soft_skills": ["team collaboration"],
  "experience_years": 0,
  "job_type": "internship",
  "is_remote": true,
  "company_domain": "AI/ML",
  "special_instructions": null
}
```

- 使用 `response_mime_type="application/json"` 强制JSON输出
- `temperature=0.1` 确保稳定性

### 3.3 匹配层 (matcher.py)

将用户经历与JD需求做语义匹配：

1. 用户所有经历bullet points存储在 `profile.yaml` 中
2. 使用 **Vertex AI Embeddings (text-embedding-004)** 生成768维向量
3. `numpy` 计算余弦相似度，选出 Top-N 最相关的经历
4. 无需向量数据库，内存计算即可（数据量小）

### 3.4 生成层 (builder.py)

1. **Jinja2渲染**：读取 `template.tex`，注入匹配到的经历和LLM改写后的bullet points
2. **Tectonic编译**：`subprocess` 调用 `tectonic` 生成PDF
3. **LaTeX转义**：通过 `utils.py` 中的 `escape_latex()` 处理特殊字符 (`&`, `%`, `$` 等)
4. **编译失败降级**：失败时输出错误日志，不中断主流程

Jinja2定界符配置（避免与LaTeX `{}` 冲突）：
```python
jinja2.Environment(
    block_start_string='\\BLOCK{',
    block_end_string='}',
    variable_start_string='\\VAR{',
    variable_end_string='}',
)
```

### 3.5 存储层 (database.py)

SQLite，单表 `jobs`：

```sql
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT,           -- linkedin / indeed / thehub
    platform_id TEXT,        -- 原始平台ID
    title TEXT,
    company TEXT,
    url TEXT,
    content_hash TEXT UNIQUE, -- SHA256(normalize(company+title)) 去重
    jd_text TEXT,            -- 原始JD
    analysis TEXT,           -- JSON格式的分析结果
    resume_path TEXT,        -- 生成的PDF路径
    status TEXT DEFAULT 'new', -- new / analyzed / generated / skipped
    created_at TEXT DEFAULT (datetime('now'))
);
```

---

## 4. 数据流

```
[每日运行 main.py run]
       │
       ▼
  ① scraper.py → 采集各平台职位 → 写入 jobs 表 (自动去重)
       │
       ▼
  ② analyzer.py → 读取 status='new' 的职位 → Gemini解析JD → 更新 analysis 字段
       │
       ▼
  ③ matcher.py → 读取 analysis → 与 profile.yaml 中的经历做向量匹配
       │
       ▼
  ④ builder.py → 选中的经历 + LLM改写 → Jinja2渲染 → Tectonic编译 → PDF输出到 output/
       │
       ▼
  ⑤ 更新 status='generated', resume_path 指向PDF
       │
       ▼
  [用户查看 output/ 中的PDF，手动投递]
```

---

## 5. 技术选型

| 组件 | 工具 | 理由 |
|------|------|------|
| 采集 | `python-jobspy` | 聚合多平台，维护活跃 |
| HTTP | `httpx` | The Hub API调用，async支持 |
| LLM | `google-cloud-aiplatform` (Vertex AI Gemini) | 长上下文、JSON输出、Embedding一站式 |
| 向量 | `numpy` | 数据量小，内存计算足够 |
| 模板 | `jinja2` | LaTeX动态渲染 |
| 编译 | `tectonic` (系统安装) | 轻量级LaTeX引擎，自动下载宏包 |
| 存储 | `sqlite3` (内置) | 零依赖，个人工具足够 |
| CLI | `argparse` (内置) | 简单够用 |

---

## 6. 快速上线步骤

### Week 1：核心链路跑通
1. 搭建项目结构，安装依赖
2. 实现 scraper.py（JobSpy + The Hub）
3. 实现 database.py（SQLite存储+去重）
4. 验证：运行一次，数据入库

### Week 2：智能层 + 生成层
1. 实现 analyzer.py（Gemini解析JD）
2. 实现 matcher.py（向量匹配）
3. 将 resume.tex 改造为 Jinja2 模板
4. 实现 builder.py（渲染+编译）
5. 验证：输入一个Job → 输出一份定制PDF

### Week 3：完善 + 日常使用
1. main.py CLI 整合所有模块
2. 添加日志输出
3. 设置 cron 每日自动运行
4. 开始实际使用，根据反馈迭代
