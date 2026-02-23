"""
FxxkJobSearch Agent v2.0 — Multi-Agent 入口

使用方式:
    python agent.py                 # 运行完整 Multi-Agent 流程
    python agent.py --agent scout   # 仅运行 ScoutAgent
    python agent.py --dry-run       # 不执行实际 API 调用

架构:
    OrchestratorAgent (Plan-and-Solve)
    ├── ScoutAgent (数据采集)
    ├── FilterAgent (智能筛选)
    ├── AnalystAgent (JD深度分析)
    └── NotifierAgent (通知推送)
"""

import argparse
import json
import logging
import os

from google import genai

import config
from src.database import JobDatabase
from src.memory import MemorySystem
from src.agents.orchestrator import OrchestratorAgent
from src.agents.scout_agent import ScoutAgent
from src.agents.filter_agent import FilterAgent
from src.agents.analyst_agent import AnalystAgent
from src.agents.notifier_agent import NotifierAgent

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("agent")


def init_llm_client() -> genai.Client:
    """初始化 Gemini Client"""
    api_key = config.GOOGLE_CLOUD_API_KEY or os.environ.get("GOOGLE_CLOUD_API_KEY")

    if api_key:
        logger.info("Using API Key for GenAI Client")
        return genai.Client(vertexai=True, api_key=api_key)
    else:
        logger.info(
            f"Using ADC for GenAI Client "
            f"(Project: {config.GCP_PROJECT_ID}, Location: {config.GCP_LOCATION})"
        )
        return genai.Client(
            vertexai=True,
            project=config.GCP_PROJECT_ID,
            location=config.GCP_LOCATION,
        )


import asyncio

async def run_orchestrator(db: JobDatabase, memory: MemorySystem, task: str = ""):
    """运行完整的 Multi-Agent 编排"""
    client = init_llm_client()
    model = config.GEMINI_FLASH_MODEL  # 编排器用 Flash 模型, 子 Agent 各自配置

    orchestrator = OrchestratorAgent.create(
        llm_client=client,
        model=model,
        memory=memory,
    )

    result = await orchestrator.run(task=task, db=db)

    logger.info(f"\n{'='*60}")
    logger.info(f"  Multi-Agent 执行完成")
    logger.info(f"  成功: {result.success}")
    logger.info(f"  指标: {json.dumps(result.metrics, ensure_ascii=False, indent=2)}")
    logger.info(f"  摘要: {result.summary}")
    logger.info(f"{'='*60}\n")

    return result


async def run_single_agent(agent_name: str, db: JobDatabase, memory: MemorySystem, task: str = ""):
    """运行单个 Sub-Agent"""
    client = init_llm_client()
    model = config.GEMINI_FLASH_MODEL

    agent_map = {
        "scout": ScoutAgent,
        "filter": FilterAgent,
        "analyst": AnalystAgent,
        "notifier": NotifierAgent,
    }

    agent_cls = agent_map.get(agent_name)
    if not agent_cls:
        logger.error(f"Unknown agent: {agent_name}. Available: {list(agent_map.keys())}")
        return

    agent = agent_cls.create(llm_client=client, model=model, memory=memory)

    if not task:
        default_tasks = {
            "scout": "从所有可用平台抓取最新职位",
            "filter": "对所有未评分的职位进行智能过滤",
            "analyst": "深度分析所有相关职位的 JD",
            "notifier": "检查是否有高匹配度职位需要发送通知",
        }
        task = default_tasks[agent_name]

    result = await agent.run(task=task, db=db)

    logger.info(f"\n[{agent_name}] 执行完成:")
    logger.info(f"  指标: {result.metrics}")
    logger.info(f"  摘要: {result.summary}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="FxxkJobSearch Multi-Agent System v2.0"
    )
    parser.add_argument(
        "--agent",
        choices=["orchestrator", "scout", "filter", "analyst", "notifier"],
        default="orchestrator",
        help="运行哪个 Agent (默认: orchestrator, 即完整流程)",
    )
    parser.add_argument(
        "--task",
        type=str,
        default="",
        help="自定义任务描述 (不传则使用默认任务)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印计划, 不执行实际 API 调用",
    )
    args = parser.parse_args()

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    db = JobDatabase(config.DB_PATH)
    memory = MemorySystem(config.DB_PATH)

    try:
        if args.agent == "orchestrator":
            asyncio.run(run_orchestrator(db, memory, task=args.task))
        else:
            asyncio.run(run_single_agent(args.agent, db, memory, task=args.task))
    finally:
        # Consolidate memory before exit
        memory.consolidate()
        memory.close()
        db.close()


if __name__ == "__main__":
    main()
