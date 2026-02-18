"""
LinkedIn Agent — CLI 入口

用法:
    python main.py scrape      # 采集职位
    python main.py filter      # 过滤不相关职位
    python main.py backfill    # 补全 LinkedIn 缺失的 JD
    python main.py analyze     # 用 Gemini 分析 JD
    python main.py generate    # 生成定制简历 PDF
    python main.py run         # 完整流水线：采集 → 过滤 → 补全 → 分析
    python main.py status      # 查看数据库统计
    python main.py list        # 列出所有相关职位
"""

import argparse
import logging
import sys

import config
from src.database import JobDatabase
from src.scraper import scrape_all_platforms, backfill_linkedin_jd
from src.filter import filter_jobs
from src.analyzer import analyze_pending_jobs
from src.notifier import send_daily_report  # 新增通知模块

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def cmd_scrape(db: JobDatabase):
    """采集职位"""
    logger.info("开始采集职位...")
    new_count = scrape_all_platforms(db)
    logger.info(f"采集完成，新增 {new_count} 条职位")


def cmd_filter(db: JobDatabase):
    """过滤不相关职位"""
    logger.info("开始过滤...")
    counts = filter_jobs(db)
    logger.info(
        f"过滤完成: {counts['relevant']} 条相关, "
        f"{counts['irrelevant']} 条不相关, "
        f"{counts['too_old']} 条过期"
    )


def cmd_backfill(db: JobDatabase):
    """补全 LinkedIn 缺失的 JD"""
    logger.info("开始补全 LinkedIn JD...")
    count = backfill_linkedin_jd(db)
    logger.info(f"补全完成: {count} 条")


def cmd_analyze(db: JobDatabase):
    """分析 JD（仅处理 relevant 且 status='new' 的职位）"""
    logger.info("开始分析 JD...")
    analyzed_count = analyze_pending_jobs(db)
    logger.info(f"分析完成，处理了 {analyzed_count} 条职位")
    
    # 只有当确实有新分析的职位，或者你想每次都强制发通知时调用
    # 这里我们设置为：只要分析过程跑完了，就检查一下有没有高分职位发通知
    if analyzed_count > 0:
        logger.info("正在生成并发送 Telegram 通知...")
        try:
            send_daily_report(db)
        except Exception as e:
            logger.error(f"发送通知失败: {e}")
    else:
        # 如果没有新分析的，但也可以选择手动触发一次报告
        # logger.info("没有新分析的职位，跳过通知")
        pass


def cmd_run(db: JobDatabase):
    """完整流水线"""
    cmd_scrape(db)
    cmd_filter(db)
    cmd_backfill(db)
    logger.info("采集流水线完成")


def cmd_status(db: JobDatabase):
    """查看统计"""
    status_counts = db.get_status_counts()
    relevance_counts = db.get_relevance_counts()

    print("\n=== 职位状态统计 ===")
    for s, c in sorted(status_counts.items()):
        print(f"  {s}: {c}")

    print("\n=== 相关性统计 ===")
    for r, c in sorted(relevance_counts.items()):
        print(f"  {r}: {c}")
    print()


def cmd_list(db: JobDatabase):
    """列出所有相关职位"""
    jobs = db.get_relevant_jobs_summary()
    if not jobs:
        print("没有相关职位")
        return

    print(f"\n=== 相关职位 ({len(jobs)} 条) ===\n")
    for j in jobs:
        age = ""
        if j.get("posted_at"):
            age = j["posted_at"][:10]
        print(f"  [{j['id']:>3}] {j['platform']:>8} | {j['title'][:50]:50} | {j['company'][:20]:20} | {age} | {j['status']}")
    print()


def cmd_report(db: JobDatabase):
    """手动触发日报发送"""
    logger.info("正在生成并发送 Telegram 通知...")
    try:
        send_daily_report(db)
    except Exception as e:
        logger.error(f"发送通知失败: {e}")

def main():
    parser = argparse.ArgumentParser(
        description="LinkedIn Agent — 个人求职自动化工具"
    )
    parser.add_argument(
        "command",
        choices=["scrape", "filter", "backfill", "analyze", "generate", "run", "status", "list", "report"],
        help="要执行的命令",
    )
    args = parser.parse_args()

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    db = JobDatabase(config.DB_PATH)

    commands = {
        "scrape": cmd_scrape,
        "filter": cmd_filter,
        "backfill": cmd_backfill,
        "analyze": cmd_analyze,
        "run": cmd_run,
        "status": cmd_status,
        "list": cmd_list,
        "report": cmd_report,
    }
    commands[args.command](db)


if __name__ == "__main__":
    main()
