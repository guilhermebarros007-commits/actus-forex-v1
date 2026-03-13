import logging
import os
import json
from typing import AsyncIterator
import google.generativeai as genai
from pathlib import Path
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.memory import db as memory_db
from app.ws_manager import ws_manager

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path(__file__).parent.parent.parent / "workspaces"
MEMORY_MAX_CHARS = 4000

# ── Per-agent model selection ─────────────────────────────────────────────────
# Lux (orchestrator) → Gemini 2.0 Flash: strong reasoning
# Traders → Gemini 2.0 Flash Lite: fast and efficient
AGENT_MODELS: dict[str, str] = {
    "lux":        os.getenv("GEMINI_MODEL_LUX", "gemini-2.0-flash"),
    "hype_beast": os.getenv("GEMINI_MODEL_TRADERS", "gemini-2.0-flash-lite"),
    "oracle":     os.getenv("GEMINI_MODEL_TRADERS", "gemini-2.0-flash-lite"),
    "vitalik":    os.getenv("GEMINI_MODEL_TRADERS", "gemini-2.0-flash-lite"),
}

AGENT_MAX_TOKENS: dict[str, int] = {
    "lux":        2048, # Increased to handle large trader analysis aggregates
    "hype_beast": 1024,
    "oracle":     1024,
    "vitalik":    1024,
}

_DEFAULT_MODEL = os.getenv("GEMINI_MODEL_LUX", "gemini-2.0-flash")


class BaseAgent:
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.workspace = WORKSPACE_ROOT / agent_id
        # Client will be initialized in specific provider classes
        self._model = AGENT_MODELS.get(agent_id, _DEFAULT_MODEL)
        self._max_tokens = AGENT_MAX_TOKENS.get(agent_id, 1024)
        logger.info(f"[{agent_id}] model={self._model} max_tokens={self._max_tokens}")

    def _read_file(self, path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _build_system_string(self) -> str:
        """Builds plain-text system prompt for Gemini/Anthropic providers."""
        parts = []
        soul = self._read_file(self.workspace / "SOUL.md")
        if soul:
            parts.append(soul)
            
        skills_dir = self.workspace / "skills"
        if skills_dir.exists():
            for skill_file in sorted(skills_dir.glob("*.md")):
                content = self._read_file(skill_file)
                if content:
                    parts.append(f"\n---\n# Skill: {skill_file.stem}\n{content}")
                    
        memory = self._read_file(self.workspace / "MEMORY.md")
        if memory and len(memory.strip()) > 20:
            if len(memory) > MEMORY_MAX_CHARS:
                # Keep latest memory
                memory = memory[-MEMORY_MAX_CHARS:]
            parts.append(f"\n---\n# Memória Acumulada\n{memory}")
            
        return "\n".join(parts)

    async def chat(
        self, user_message: str, extra_context: str = "", history_limit: int = 10
    ) -> str:
        system_str = self._build_system_string()
        past = await memory_db.get_chat_history(self.agent_id, limit=history_limit)
        messages = [{"role": m["role"], "content": m["content"]} for m in past]

        full_message = f"{extra_context}\n\n{user_message}" if extra_context else user_message
        messages.append({"role": "user", "content": full_message})

        # Forward to sub-class implementation
        return await self._execute_chat(system_str, messages, full_message)

    async def stream_chat(
        self, user_message: str, extra_context: str = "", history_limit: int = 10
    ) -> AsyncIterator[str]:
        # Implementation in sub-classes
        pass

    async def append_memory(self, entry: str):
        memory_path = self.workspace / "MEMORY.md"
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        block = f"\n## [{timestamp}]\n{entry}\n"

        if memory_path.exists():
            existing = memory_path.read_text(encoding="utf-8")
            lines = existing.split("\n", 4)
            header = "\n".join(lines[:4]) if len(lines) >= 4 else existing
            rest = "\n".join(lines[4:]) if len(lines) >= 4 else ""
            new_content = header + block + rest
        else:
            new_content = f"# MEMORY.md — {self.agent_id}\n\n---\n{block}"

        memory_path.write_text(new_content, encoding="utf-8")

    async def log_event(self, message: str, level: str = "info"):
        """Broadcast a reasoning log to all connected WebSocket clients."""
        payload = {
            "type": "agent_log",
            "agent_id": self.agent_id,
            "level": level,
            "message": message,
            "timestamp": datetime.utcnow().isoformat()
        }
        logger.info(f"[{self.agent_id}] {message}")
        await ws_manager.broadcast(payload)


class GeminiBaseAgent(BaseAgent):
    """
    Base agent using Google Gemini 2.0.
    Supports individual API keys per agent.
    """
    def __init__(self, agent_id: str):
        super().__init__(agent_id)
        
        # Determine the specific key for this agent
        key_map = {
            "lux":        "GOOGLE_API_KEY_LUX",
            "hype_beast": "GOOGLE_API_KEY_HYPE",
            "oracle":     "GOOGLE_API_KEY_ORACLE",
            "vitalik":    "GOOGLE_API_KEY_VITALIK"
        }
        env_var = key_map.get(agent_id, "GOOGLE_API_KEY_LUX")
        api_key = os.getenv(env_var)
        
        if not api_key:
            logger.warning(f"[{agent_id}] No specific API key found for {env_var}, falling back to default.")
            api_key = os.getenv("GOOGLE_API_KEY_LUX")

        genai.configure(api_key=api_key)
        self._client = genai.GenerativeModel(model_name=self._model)
        logger.info(f"[{agent_id}] model={self._model} (Gemini) max_tokens={self._max_tokens}")

    async def _execute_chat(self, system_str: str, history_blocks: list, full_message: str) -> str:
        """Internal execution for Gemini."""
        history = []
        for m in history_blocks[:-1]: # exclude latest user message
            role = "user" if m["role"] == "user" else "model"
            history.append({"role": role, "parts": [m["content"]]})
        
        model_with_sys = genai.GenerativeModel(
            model_name=self._model,
            system_instruction=system_str
        )
        chat_session = model_with_sys.start_chat(history=history)
        
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            reraise=True
        )
        async def _send():
            return await chat_session.send_message_async(
                full_message,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=self._max_tokens
                )
            )

        response = await _send()
        reply = response.text
        
        await memory_db.save_message(self.agent_id, "user", full_message)
        await memory_db.save_message(self.agent_id, "assistant", reply)
        return reply

    def _extract_json(self, text: str) -> dict:
        """Robustly extracts JSON from potentially noisy LLM output."""
        import re
        import json
        if not text:
            return {}
        
        # 1. Direct parse attempt after trimming
        trimmed = text.strip()
        try:
            return json.loads(trimmed)
        except:
            pass
            
        # 2. Markdown block removal (```json ... ```)
        blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        for block in blocks:
            try:
                return json.loads(block)
            except:
                continue

        # 3. Brute force: find first '{' and last '}'
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            content = text[start:end+1]
            try:
                content = re.sub(r"//.*", "", content) # remove single line comments
                content = re.sub(r",\s*}", "}", content)
                return json.loads(content)
            except Exception as e:
                logger.debug(f"[{self.agent_id}] JSON brute force failed: {e}")

        logger.warning(f"[{self.agent_id}] Could not extract valid JSON from text (len={len(text)})")
        return {}

    async def stream_chat(
        self, user_message: str, extra_context: str = "", history_limit: int = 10
    ) -> AsyncIterator[str]:
        """Streaming via Gemini."""
        system_str = self._build_system_string()
        past = await memory_db.get_chat_history(self.agent_id, limit=history_limit)

        history = []
        for m in past:
            role = "user" if m["role"] == "user" else "model"
            history.append({"role": role, "parts": [m["content"]]})

        full_message = f"{extra_context}\n\n{user_message}" if extra_context else user_message
        
        model_with_sys = genai.GenerativeModel(
            model_name=self._model,
            system_instruction=system_str
        )
        chat_session = model_with_sys.start_chat(history=history)
        
        response = await chat_session.send_message_async(
            full_message,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=self._max_tokens
            ),
            stream=True
        )
        
        full_reply = []
        async for chunk in response:
            text = chunk.text
            full_reply.append(text)
            yield text

        reply = "".join(full_reply)
        await memory_db.save_message(self.agent_id, "user", full_message)
        await memory_db.save_message(self.agent_id, "assistant", reply)
