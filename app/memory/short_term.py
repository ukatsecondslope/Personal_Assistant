"""Short-term memory: keeps a thread coherent within the model's context window.

Each request is built as:
    system prompt + long-term memory + rolling thread summary + most recent raw messages

Raw messages are picked newest-first until HISTORY_TOKEN_BUDGET is reached. When the
unsummarised part of a thread grows past SUMMARIZE_TRIGGER_TOKENS, the older messages are
folded into the thread's rolling summary (in the background), so nothing silently falls out.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Tuple

from .. import config, ollama_client, prompts
from ..db import store


def est_tokens(text: str) -> int:
    # ~3 chars/token is a conservative estimate for Qwen's tokenizer on mixed text/code.
    return len(text) // 3 + 4


def _transcript(msgs: List[Dict[str, Any]], max_chars_each: int = 2000) -> str:
    lines = []
    for m in msgs:
        text = m["content"]
        if len(text) > max_chars_each:
            text = text[:max_chars_each] + " …[truncated]"
        lines.append(f"{m['role'].capitalize()}: {text}")
    return "\n\n".join(lines)


def build_context(thread_id: str, memory_block: str) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """Return the Ollama `messages` list for the next reply plus debug info."""
    thread = store.get_thread(thread_id)
    system = prompts.SYSTEM_PROMPT.format(today=dt.date.today().strftime("%A, %d %B %Y"))
    if memory_block:
        system += prompts.MEMORY_BLOCK.format(memories=memory_block)
    if thread["summary"]:
        system += prompts.SUMMARY_BLOCK.format(summary=thread["summary"])

    pending = store.messages(thread_id, after_id=thread["summary_upto"])
    selected: List[Dict[str, Any]] = []
    used = 0
    for m in reversed(pending):
        t = est_tokens(m["content"])
        # Always keep at least the latest exchange, even if it is huge.
        if used + t > config.HISTORY_TOKEN_BUDGET and len(selected) >= 2:
            break
        selected.append(m)
        used += t
    selected.reverse()

    # Previous turns' reasoning is never sent back (Qwen3 guidance); only final answers.
    messages = [{"role": "system", "content": system}] + [
        {"role": m["role"], "content": m["content"]} for m in selected
    ]
    info = {
        "system_tokens": est_tokens(system),
        "history_tokens": used,
        "messages_in_context": len(selected),
        "messages_dropped": len(pending) - len(selected),
        "messages_summarized": store.message_count(thread_id) - len(pending),
        "has_summary": bool(thread["summary"]),
    }
    return messages, info


async def maybe_summarize(thread_id: str) -> bool:
    """Fold older messages into the rolling summary if the thread is getting long."""
    thread = store.get_thread(thread_id)
    if not thread:
        return False
    pending = store.messages(thread_id, after_id=thread["summary_upto"])
    total = sum(est_tokens(m["content"]) for m in pending)
    if total <= config.SUMMARIZE_TRIGGER_TOKENS or len(pending) <= config.KEEP_RECENT_MESSAGES:
        return False

    fold = pending[: -config.KEEP_RECENT_MESSAGES]
    prompt = prompts.SUMMARY_PROMPT.format(
        summary=thread["summary"] or "(none yet)", transcript=_transcript(fold)
    )
    summary = await ollama_client.complete(
        [{"role": "user", "content": prompt}], num_predict=600
    )
    store.update_thread(thread_id, summary=summary, summary_upto=fold[-1]["id"])
    return True
