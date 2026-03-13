from typing import Dict
from app.agents.base import BaseAgent

agents: Dict[str, BaseAgent] = {}

def get_agent(agent_id: str) -> BaseAgent:
    return agents.get(agent_id)

async def call_agent(agent_id: str, prompt: str) -> str:
    agent = get_agent(agent_id)
    if not agent:
        return f"Error: Agent {agent_id} not found"
    return await agent.chat(prompt)
