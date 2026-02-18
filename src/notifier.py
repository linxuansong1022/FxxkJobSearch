"""
Telegram 通知模块

职责：
- 发送纯文本或 Markdown 消息到 Telegram
- 生成每日求职简报 (Daily Report)
"""

import logging
import requests
import json
from datetime import datetime, timedelta
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


def send_daily_report(db: JobDatabase):
    """
    生成并发送每日简报。
    统计过去 24 小时内分析完成且匹配度较高的职位。
    """
    # 1. 获取今日统计
    # 这里简单起见，我们只查 status='analyzed' 且 match_score > 0.6 的职位
    # 实际生产环境可能需要更复杂的 SQL 查 created_at
    
    # 获取所有已分析的职位
    cursor = db.conn.execute(
        """SELECT id, title, company, url, analysis, created_at 
           FROM jobs 
           WHERE status='analyzed' 
           ORDER BY id DESC LIMIT 50"""
    )
    jobs = []
    for row in cursor.fetchall():
        try:
            analysis = json.loads(row[4]) if row[4] else {}
            # 兼容嵌套结构
            if "match_evaluation" in analysis:
                score = analysis["match_evaluation"].get("score", 0)
            else:
                score = analysis.get("match_score", 0)
                
            # 确保 score 是 float
            if isinstance(score, str):
                try:
                    score = float(score)
                except:
                    score = 0.0
            
            # 只关注高分职位 (> 0.6)
            if score >= 0.6:
                jobs.append({
                    "title": row[1],
                    "company": row[2],
                    "url": row[3],
                    "score": score,
                    "id": row[0]
                })
        except Exception as e:
            logger.warning(f"解析分析结果失败 (ID: {row[0]}): {e}")
            continue

    # 按分数降序
    jobs.sort(key=lambda x: x["score"], reverse=True)
    top_jobs = jobs[:10]  # 修改为前10个

    if not top_jobs:
        logger.info("今日无高分职位，跳过通知")
        return

    # 2. 构造消息
    date_str = datetime.now().strftime("%Y-%m-%d")
    msg = f"📅 *求职日报 ({date_str})*\n\n"
    msg += f"🎯 发现 *{len(jobs)}* 个高匹配职位 (Score >= 0.6)\n\n"

    for i, job in enumerate(top_jobs, 1):
        score_icon = "🔥" if job["score"] >= 0.8 else "✨"
        msg += f"{i}. {score_icon} *{job['score']:.2f}* | [{job['title']}]({job['url']})\n"
        msg += f"   🏢 {job['company']}\n\n"

    msg += "💪 加油！点击链接直接申请。"

    # 3. 发送
    logger.info(f"发送 Telegram 通知: 包含 {len(top_jobs)} 个职位")
    _send_message(msg)
