"""
Multi-Agent System for Job Search Automation

Architecture:
    OrchestratorAgent (Plan-and-Solve)
    ├── ScoutAgent (Data Collection)
    ├── FilterAgent (Smart Filtering)
    ├── AnalystAgent (JD Analysis + Reflection)
    └── NotifierAgent (Notification)
"""

from src.agents.base_agent import BaseAgent, AgentResult, AgentAction, AgentObservation
from src.agents.orchestrator import OrchestratorAgent
from src.agents.scout_agent import ScoutAgent
from src.agents.filter_agent import FilterAgent
from src.agents.analyst_agent import AnalystAgent
from src.agents.notifier_agent import NotifierAgent

__all__ = [
    "BaseAgent", "AgentResult", "AgentAction", "AgentObservation",
    "OrchestratorAgent",
    "ScoutAgent", "FilterAgent", "AnalystAgent", "NotifierAgent",
]
