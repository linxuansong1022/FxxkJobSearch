"""
Telegram 通知模块

职责：
- 发送纯文本或 Markdown 消息到 Telegram
- 生成每日求职简报 (Daily Report)
- 增量通知：已通知的不再推送
- 展示分析详情：match_reason, hard_skills, role_type
"""

import logging
import re
import requests
import json
from datetime import datetime
from src.database import JobDatabase
import config

logger = logging.getLogger(__name__)


def _send_message(text: str):
    """发送消息到底层 API"""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram 配置缺失，无法发送通知")
        return

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Telegram 发送失败: {resp.text}")
    except Exception as e:
        logger.error(f"Telegram 请求异常: {e}")


def _is_valid_job_url(url: str) -> bool:
    """快速检查 URL 是否可能是有效的单个职位页，而非聚合搜索页"""
    if not url or len(url) < 10:
        return False
    url_lower = url.lower()
    aggregate_patterns = [
        r"indeed\.com/jobs\?",
        r"indeed\.com/q-",
        r"glassdoor\.com/job/",       # /Job/ 搜索结果页 (case-insensitive)
        r"linkedin\.com/jobs/search",
    ]
    return not any(re.search(p, url_lower) for p in aggregate_patterns)


def send_daily_report(db: JobDatabase):
    """
    生成并发送每日简报。
    增量通知：只推送 notified_at IS NULL 的已分析高匹配职位。
    """
    # 查询未通知的已分析职位
    cursor = db.conn.execute(
        """SELECT id, title, company, url, analysis, created_at
           FROM jobs
           WHERE status='analyzed' AND (notified_at IS NULL)
           ORDER BY id DESC LIMIT 50"""
    )
    jobs = []
    for row in cursor.fetchall():
        try:
            analysis = json.loads(row[4]) if row[4] else {}
            if "match_evaluation" in analysis:
                score = analysis["match_evaluation"].get("score", 0)
            else:
                score = analysis.get("match_score", 0)

            if isinstance(score, str):
                try:
                    score = float(score)
                except ValueError:
                    score = 0.0

            if score >= 0.6:
                jobs.append({
                    "title": row[1],
                    "company": row[2],
                    "url": row[3],
                    "score": score,
                    "id": row[0],
                    "match_reason": analysis.get("match_reason", ""),
                    "hard_skills": analysis.get("hard_skills", []),
                    "role_type": analysis.get("role_type", ""),
                    "summary": analysis.get("summary", ""),
                })
        except Exception as e:
            logger.warning(f"解析分析结果失败 (ID: {row[0]}): {e}")
            continue

    jobs.sort(key=lambda x: x["score"], reverse=True)
    # 过滤掉聚合页 URL 和非实习/学生工岗位
    _INTERN_ROLE_KEYWORDS = ["实习", "intern", "student", "学生", "兼职", "part-time",
                              "praktik", "studiejob", "thesis", "graduate", "research assistant",
                              "unpaid", "deltid", "studentermedhjælper"]
    def _is_target_role(job):
        role = (job.get("role_type") or "").lower()
        title = (job.get("title") or "").lower()
        combined = f"{role} {title}"
        return any(kw in combined for kw in _INTERN_ROLE_KEYWORDS)

    top_jobs = [j for j in jobs if _is_valid_job_url(j["url"]) and _is_target_role(j)][:10]

    if not top_jobs:
        logger.info("今日无新的高分职位，发送简要状态报告")
        date_str = datetime.now().strftime("%Y-%m-%d")
        counts = db.get_status_counts()
        msg = f"📅 *求职日报 ({date_str})*\n\n"
        msg += f"ℹ️ 今日暂无新的匹配度 > 0.6 的高匹配职位。\n"
        msg += f"📊 当前数据库概览：已分析 {counts.get('analyzed', 0)} 个，待分析 {counts.get('new', 0)} 个。\n\n"
        msg += "☕️ 只要有合适的，我会第一时间推给你！"
        _send_message(msg)
        return

    # 构造消息
    date_str = datetime.now().strftime("%Y-%m-%d")
    msg = f"📅 *求职日报 ({date_str})*\n\n"
    msg += f"🎯 发现 *{len(top_jobs)}* 个新的高匹配职位\n\n"

    for i, job in enumerate(top_jobs, 1):
        score_icon = "🔥" if job["score"] >= 0.8 else "✨"
        msg += f"{i}. {score_icon} *{job['score']:.2f}* | {job['title']}\n"
        msg += f"   🏢 {job['company']}"
        if job.get("role_type"):
            msg += f" · {job['role_type']}"
        msg += "\n"
        if job.get("hard_skills"):
            skills_str = ", ".join(job["hard_skills"][:4])
            msg += f"   🔑 {skills_str}\n"
        if job.get("match_reason"):
            msg += f"   💡 {job['match_reason'][:80]}\n"
        msg += f"   🔗 [申请链接]({job['url']})\n\n"

    msg += "💪 加油！点击链接直接申请。"

    # 发送
    logger.info(f"发送 Telegram 通知: 包含 {len(top_jobs)} 个职位")
    _send_message(msg)

    # 标记已通知
    job_ids = [j["id"] for j in top_jobs]
    placeholders = ",".join("?" * len(job_ids))
    db.conn.execute(
        f"UPDATE jobs SET notified_at = datetime('now') WHERE id IN ({placeholders})",
        job_ids,
    )
    db.conn.commit()
    logger.info(f"已标记 {len(job_ids)} 个职位为已通知")
