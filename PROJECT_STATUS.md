# LinkedIn Agent 项目进度文档

**最后更新时间**: 2026-02-18
**当前版本**: v0.5.0 (Telegram 通知 + 动态简历分析)

---

## 1. 核心功能实现状态 (Current Features)

目前 Agent 已经具备了完整的 **采集 -> 筛选 -> 分析 -> 通知** 闭环能力。

| 模块                 | 状态           | 描述                                                                                                                             |
| :------------------- | :------------- | :------------------------------------------------------------------------------------------------------------------------------- |
| **Scraper (采集)**   | ✅ 已实现       | 支持 **LinkedIn**, **Indeed** (通过 JobSpy) 和 **The Hub** (API) 的职位采集。包含自动去重和增量更新。                            |
| **Filter (初筛)**    | ✅ 已实现       | 基于规则（关键词黑/白名单、发布时间）快速过滤不相关职位，节省 API 成本。                                                         |
| **Analyzer (分析)**  | ✅ **核心升级** | 集成 **Gemini 3 Pro Preview** 模型。支持 **Thinking Mode** (深度思考) 和 **动态简历加载** (根据 `profile.yaml` 实时分析匹配度)。 |
| **Matcher (向量)**   | ✅ 已实现       | 使用 Vertex AI Embeddings 计算简历 Bullet Points 与 JD 的余弦相似度，用于简历生成。                                              |
| **Generator (生成)** | ⚠️ **存在 Bug** | 基于 Jinja2 + LaTeX (Tectonic) 自动生成 PDF 简历。**目前存在乱码、排版错乱及字段缺失问题，需优先修复。**                         |
| **Notifier (通知)**  | ✅ **新增**     | 集成 **Telegram Bot**，每日自动推送 Top 10 高匹配职位。                                                                          |
| **Security (安全)**  | ✅ 已实现       | 敏感信息 (API Key, Token) 移至 `.env`，个人信息 (`profile.yaml`) 从 git 移除。                                                   |

---

## 2. 今日更新日志 (2026-02-18)

### 🐛 待修复 Bug (Generator 模块)
1.  **中文乱码 (Encoding Issue)**:
    - 生成的 LaTeX PDF 中，中文字符显示为乱码或方框 (可能是字体配置缺失)。
2.  **排版错乱 (Layout Misalignment)**:
    - 简历各板块间距不一致，列表项缩进异常。
3.  **字段缺失 (Missing Fields)**:
    - 部分 `profile.yaml` 中的字段 (如 Skills 的某些分类) 未能正确注入到模板中，导致生成的简历内容不完整。

### ✨ 新增功能
1.  **Telegram 通知模块**:
    - 新增 `src/notifier.py`，支持发送 Markdown 格式的日报。
    - 实现 `python main.py report` 命令，手动触发日报发送。
    - 每日日报默认推送 **Top 10** 高分职位。
2.  **动态简历分析 (Dynamic Analysis)**:
    - 重构 `src/analyzer.py`，不再使用硬编码的背景描述。
    - 现在分析时会实时读取 `profile.yaml` 中的 **Education**, **Experience**, **Projects** 详情，AI 能识别具体项目细节 (如 GraphRAG, DDPM)。
3.  **ATS 关键词优化**:
    - 更新 `profile.yaml`，大幅扩充了 `skills` 列表，覆盖 Fullstack, AI Agent, Cloud, DevOps 等热门关键词，提高通过率。

### 🔧 修复与优化
1.  **配置安全化**: 引入 `python-dotenv`，将所有 Token 移入 `.env` 文件。
2.  **Git 隐私保护**: 更新 `.gitignore`，移除 `profile.yaml`, `output/`, `*.log` 等敏感文件，并从 Git 历史中清理。
3.  **JSON 解析增强**: 修复 Gemini 返回 Markdown 代码块导致 JSON 解析失败的问题。

---

## 3. 待办事项 (TODO / Roadmap)

### 短期目标 (本周)
- [ ] **服务器部署**: 将 Agent 部署到 Azure (Denmark) 或 Hetzner (Germany) 云服务器。
- [ ] **自动化调度**: 配置 `crontab` 或 `apscheduler` 实现每日定时运行 (如每天早上 9:00)。
- [ ] **Cover Letter 生成**: 利用 Gemini 针对高分职位自动撰写求职信 (Cover Letter)。

### 中期目标
- [ ] **Web Dashboard**: 开发一个简单的 Streamlit 或 Flask 网页，用于可视化查看职位和手动干预。
- [ ] **更多数据源**: 尝试集成 Glassdoor 或 Google Jobs 接口。
- [ ] **自动投递 (Auto-Apply)**: (高风险) 研究 Selenium 自动化投递脚本 (需解决验证码问题)。

---

## 4. 已知问题 (Known Issues)
- **LinkedIn 反爬虫**: 频繁采集可能导致 IP 暂时封禁 (429/403)。建议部署时使用住宅代理 (Residential Proxy)。
- **PDF 编译速度**: Tectonic 首次编译需要下载大量包，速度较慢。

---

## 5. 常用命令速查

```bash
# 1. 运行完整流水线 (采集 -> 过滤 -> 分析 -> 通知)
python main.py run

# 2. 仅手动发送日报 (测试 Telegram)
python main.py report

# 3. 查看数据库统计
python main.py status

# 4. 重新生成简历
python main.py generate
```
