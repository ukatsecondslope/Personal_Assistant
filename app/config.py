"""Runtime configuration. Every value can be overridden with an environment variable."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.getenv("PA_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "assistant.db"
MEMORY_EXPORT_PATH = DATA_DIR / "long_term_memory.json"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
CHAT_MODEL = os.getenv("PA_CHAT_MODEL", "qwen3-14b-16k")
# Background jobs use the same model on purpose: loading a second model on a
# 16 GB Mac would evict the chat model and force a reload on every turn.
MEMORY_MODEL = os.getenv("PA_MEMORY_MODEL", CHAT_MODEL)
# Must match the model's num_ctx. If requests disagree, Ollama reloads the model.
NUM_CTX = int(os.getenv("PA_NUM_CTX", "16384"))
KEEP_ALIVE = os.getenv("PA_KEEP_ALIVE", "30m")

# --- Short-term (thread) memory -------------------------------------------
# Tokens of raw history sent with each request (rest of the window is left
# for the system prompt, long-term memory and the reply).
HISTORY_TOKEN_BUDGET = int(os.getenv("PA_HISTORY_TOKEN_BUDGET", "9000"))
# When unsummarised history grows past this, older turns are folded into the
# thread's rolling summary.
SUMMARIZE_TRIGGER_TOKENS = int(os.getenv("PA_SUMMARIZE_TRIGGER_TOKENS", "6000"))
# Most recent messages that always stay verbatim (never summarised).
KEEP_RECENT_MESSAGES = int(os.getenv("PA_KEEP_RECENT_MESSAGES", "6"))

# --- Long-term (user) memory ----------------------------------------------
MEMORY_ENABLED = os.getenv("PA_MEMORY_ENABLED", "1") == "1"
# Extraction waits until a thread has been quiet this long, then processes all
# new turns at once. Each background call evicts the chat's prompt cache, so
# batching keeps follow-up messages fast.
MEMORY_IDLE_SECONDS = float(os.getenv("PA_MEMORY_IDLE_SECONDS", "45"))
# Character budget for the memory block injected into the system prompt.
MEMORY_PROMPT_CHAR_BUDGET = int(os.getenv("PA_MEMORY_PROMPT_CHAR_BUDGET", "3000"))
# Above this many memories, they are merged into a compact set.
MEMORY_COMPACT_THRESHOLD = int(os.getenv("PA_MEMORY_COMPACT_THRESHOLD", "40"))
MEMORY_COMPACT_TARGET = int(os.getenv("PA_MEMORY_COMPACT_TARGET", "25"))

MEMORY_CATEGORIES = [
    "identity",
    "preferences",
    "goals",
    "projects",
    "skills",
    "relationships",
    "behavior",
    "other",
]
