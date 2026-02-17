"""
LinkedIn Agent — CLI 入口

用法:
    python main.py scrape      # 仅采集职位
    python main.py analyze     # 仅分析已采集的JD
    python main.py generate    # 仅为已分析的职位生成简历
    python main.py run         # 完整流水线：采集 → 分析 → 生成
    python main.py status      # 查看当前数据库中各状态的职位数量
"""

import argparse
import logging
import sys

import config
from src.database import JobDatabase
from src.scraper import scrape_all_platforms
from src.analyzer import analyze_pending_jobs
from src.matcher import load_profile_bullets
from src.builder import generate_resumes

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def cmd_scrape(db: JobDatabase):
    """步骤1：从各平台采集职位，写入数据库"""
    logger.info("开始采集职位...")
    new_count = scrape_all_platforms(db)
    logger.info(f"采集完成，新增 {new_count} 条职位")


def cmd_analyze(db: JobDatabase):
    """步骤2：对 status='new' 的职位用 Gemini 解析JD"""
    logger.info("开始分析JD...")
    analyzed_count = analyze_pending_jobs(db)
    logger.info(f"分析完成，处理了 {analyzed_count} 条职位")


def cmd_generate(db: JobDatabase):
    """步骤3：对已分析的职位做匹配+生成定制简历PDF"""
    logger.info("开始生成定制简历...")
    generated_count = generate_resumes(db)
    logger.info(f"生成完成，输出了 {generated_count} 份PDF到 {config.OUTPUT_DIR}")


def cmd_run(db: JobDatabase):
    """完整流水线：采集 → 分析 → 生成"""
    cmd_scrape(db)
    cmd_analyze(db)
    cmd_generate(db)
    logger.info("全流程完成")


def cmd_status(db: JobDatabase):
    """查看数据库中各状态的职位统计"""
    stats = db.get_status_counts()
    print("\n=== 职位状态统计 ===")
    for status, count in stats.items():
        print(f"  {status}: {count}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="LinkedIn Agent — 个人求职自动化工具"
    )
    parser.add_argument(
        "command",
        choices=["scrape", "analyze", "generate", "run", "status"],
        help="要执行的命令",
    )
    args = parser.parse_args()

    # 确保输出目录存在
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 初始化数据库
    db = JobDatabase(config.DB_PATH)

    # 分发命令
    commands = {
        "scrape": cmd_scrape,
        "analyze": cmd_analyze,
        "generate": cmd_generate,
        "run": cmd_run,
        "status": cmd_status,
    }
    commands[args.command](db)


if __name__ == "__main__":
    main()
