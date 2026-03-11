"""
FxxkJobSearch — CLI 入口

用法:
    python main.py run         # 🚀 一键全流程: 采集→过滤→分析→通知
    python main.py scrape      # [仅测试] 采集职位
    python main.py filter      # [仅测试] 过滤不相关职位
    python main.py analyze     # [仅测试] 用 Gemini 分析 JD
    python main.py status      # 查看数据库统计
    python main.py list        # 列出所有相关职位
    python main.py report      # 发送 Telegram 通知
    python main.py agent       # 🤖 运行 Multi-Agent 系统 (v2.0 主干)
    python main.py mcp-server  # 启动 MCP Tool Server
    python main.py evaluate    # 运行 Agent 评估
"""

import argparse
import logging
import sys
import asyncio

import config
from src.database import JobDatabase
from src.scraper import scrape_all_platforms
from src.filter import filter_jobs
from src.analyzer import analyze_pending_jobs
from src.notifier import send_daily_report

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


async def cmd_run(db: JobDatabase):
    """一键全流程: 采集→过滤→分析→通知"""
    logger.info("=== 开始全流程 ===")
    cmd_scrape(db)
    await cmd_filter(db)
    await cmd_analyze(db)
    cmd_report(db)
    logger.info("=== 全流程完成 ===")


async def cmd_filter(db: JobDatabase):
    """过滤不相关职位 (Async)"""
    logger.info("开始过滤...")
    counts = await filter_jobs(db)
    logger.info(
        f"过滤完成: {counts['relevant']} 条相关, "
        f"{counts['irrelevant']} 条不相关, "
        f"{counts['too_old']} 条过期"
    )



async def cmd_analyze(db: JobDatabase):
    """分析 JD (Async)"""
    # Phase 2: 先补全缺失的 JD
    logger.info("开始补全缺失 JD...")
    from src.jd_fetcher import backfill_missing_jds
    backfilled = await backfill_missing_jds(db)
    logger.info(f"JD 补全完成: {backfilled} 条")

    logger.info("开始分析 JD...")
    analyzed_count = await analyze_pending_jobs(db)
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

async def cmd_agent(db: JobDatabase):
    """运行 Multi-Agent 系统 (v2.0 Async)"""
    from agent import run_orchestrator
    from src.memory import MemorySystem
    memory = MemorySystem(config.DB_PATH)
    try:
        await run_orchestrator(db, memory)
    finally:
        memory.consolidate()
        memory.close()


def cmd_mcp_server(db: JobDatabase):
    """启动 MCP Tool Server (stdio 模式)"""
    from src.mcp.mcp_server import run_mcp_stdio_server
    run_mcp_stdio_server(db)


def cmd_evaluate(db: JobDatabase):
    """运行 Agent 评估系统"""
    from src.evaluation.evaluator import run_evaluation
    run_evaluation()


def main():
    parser = argparse.ArgumentParser(
        description="FxxkJobSearch — 求职自动化 Multi-Agent 系统"
    )
    parser.add_argument(
        "command",
        choices=[
            "run", "scrape", "filter", "analyze",
            "status", "list", "report",
            "agent", "mcp-server", "evaluate",
        ],
        help="要执行的命令",
    )
    args = parser.parse_args()

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    db = JobDatabase(config.DB_PATH)

    commands = {
        "run": cmd_run,
        "scrape": cmd_scrape,
        "filter": cmd_filter,
        "analyze": cmd_analyze,
        "status": cmd_status,
        "list": cmd_list,
        "report": cmd_report,
        "agent": cmd_agent,
        "mcp-server": cmd_mcp_server,
        "evaluate": cmd_evaluate,
    }
    cmd_fn = commands[args.command]
    if asyncio.iscoroutinefunction(cmd_fn):
        asyncio.run(cmd_fn(db))
    else:
        cmd_fn(db)


if __name__ == "__main__":
    main()
