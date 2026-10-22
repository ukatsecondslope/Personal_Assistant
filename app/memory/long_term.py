"""Long-term memory: durable facts about the user, shared across all threads.

Flow (ChatGPT-style "saved memories"):
  1. extract()  – after a thread goes idle, the new turns are shown to the model together with
                  the existing memories; it returns add / update / delete operations (JSON schema).
  2. compact()  – when the store grows past MEMORY_COMPACT_THRESHOLD, the model rewrites it into
                  a smaller, de-duplicated set.
  3. render_block() – the memories are grouped by category, ranked by importance and trimmed to
                  MEMORY_PROMPT_CHAR_BUDGET, then injected into every system prompt.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List

from .. import config, ollama_client, prompts
from ..db import store
from .short_term import _transcript

log = logging.getLogger("memory.long_term")

_CATS = config.MEMORY_CATEGORIES

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["add", "update", "delete"]},
                    "id": {"type": "integer"},
                    "category": {"type": "string", "enum": _CATS},
                    "content": {"type": "string"},
                    "importance": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["op"],
            },
        }
    },
    "required": ["operations"],
}

COMPACT_SCHEMA = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": _CATS},
                    "content": {"type": "string"},
                    "importance": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["category", "content", "importance"],
            },
        }
    },
    "required": ["memories"],
}

# Max characters of conversation shown to the extractor per call.
_CHUNK_CHARS = 8000


def render_block(memories: List[Dict[str, Any]] = None) -> str:
    memories = store.list_memories() if memories is None else memories
    if not memories:
        return ""
    ranked = sorted(memories, key=lambda m: (-m["importance"], -m["updated_at"]))
    by_cat: Dict[str, List[str]] = defaultdict(list)
    used = 0
    for m in ranked:
        line = f"- {m['content']}"
        if used + len(line) > config.MEMORY_PROMPT_CHAR_BUDGET:
            break
        by_cat[m["category"]].append(line)
        used += len(line) + 1
    parts = []
    for cat in _CATS:
        if by_cat.get(cat):
            parts.append(f"{cat.capitalize()}:\n" + "\n".join(by_cat[cat]))
    return "\n".join(parts)


def _list_for_prompt(memories: List[Dict[str, Any]]) -> str:
    if not memories:
        return "(none yet)"
    return "\n".join(f"{m['id']} | {m['category']} | {m['content']}" for m in memories)


def _norm(s: str) -> str:
    return " ".join(s.lower().strip().rstrip(".").split())


def _apply(ops: List[Dict[str, Any]], thread_id: str) -> Dict[str, int]:
    existing = {m["id"]: m for m in store.list_memories()}
    seen = {_norm(m["content"]) for m in existing.values()}
    counts = {"add": 0, "update": 0, "delete": 0}
    for op in ops:
        kind = op.get("op")
        content = (op.get("content") or "").strip()
        cat = op.get("category") if op.get("category") in _CATS else None
        imp = op.get("importance")
        imp = max(1, min(5, int(imp))) if isinstance(imp, int) else None
        mid = op.get("id")
        if kind == "add" and content and _norm(content) not in seen:
            store.add_memory(cat or "other", content, imp or 3, thread_id)
            seen.add(_norm(content))
            counts["add"] += 1
        elif kind == "update" and mid in existing and (content or cat or imp):
            store.update_memory(mid, content=content or None, category=cat, importance=imp)
            counts["update"] += 1
        elif kind == "delete" and mid in existing:
            store.delete_memory(mid)
            existing.pop(mid)
            counts["delete"] += 1
    return counts


async def extract(thread_id: str) -> Dict[str, int]:
    """Mine all not-yet-processed turns of a thread for long-term memories."""
    thread = store.get_thread(thread_id)
    if not thread:
        return {}
    new = store.messages(thread_id, after_id=thread["memory_upto"])
    if not new:
        return {}

    totals = {"add": 0, "update": 0, "delete": 0}
    chunk: List[Dict[str, Any]] = []
    size = 0
    for i, m in enumerate(new):
        chunk.append(m)
        size += min(len(m["content"]), 2000)
        if size < _CHUNK_CHARS and i < len(new) - 1:
            continue
        # Skip chunks where the user said almost nothing (e.g. "ok", "thanks").
        user_text = " ".join(x["content"] for x in chunk if x["role"] == "user")
        if len(user_text.strip()) >= 12:
            system = prompts.MEMORY_EXTRACT_PROMPT.format(
                memories=_list_for_prompt(store.list_memories())
            )
            result = await ollama_client.complete_json(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": "New conversation turns:\n\n" + _transcript(chunk)},
                ],
                schema=EXTRACT_SCHEMA,
            )
            for k, v in _apply(result.get("operations", []), thread_id).items():
                totals[k] += v
        # Progress is saved per chunk, so a crash never re-processes finished turns.
        store.update_thread(thread_id, memory_upto=chunk[-1]["id"])
        chunk, size = [], 0

    if sum(totals.values()):
        log.info("thread %s: memory ops %s", thread_id[:8], totals)
        store.export_memories()
    if len(store.list_memories()) > config.MEMORY_COMPACT_THRESHOLD:
        await compact()
    return totals


async def compact() -> int:
    memories = store.list_memories()
    if len(memories) < 2:
        return len(memories)
    listing = "\n".join(f"{m['category']} | {m['importance']} | {m['content']}" for m in memories)
    result = await ollama_client.complete_json(
        [{"role": "user", "content": prompts.MEMORY_COMPACT_PROMPT.format(
            target=config.MEMORY_COMPACT_TARGET, memories=listing)}],
        schema=COMPACT_SCHEMA,
        num_predict=2048,
    )
    items = []
    for i in result.get("memories", []):
        content = (i.get("content") or "").strip()
        if content:
            items.append({
                "category": i.get("category") if i.get("category") in _CATS else "other",
                "content": content,
                "importance": max(1, min(5, int(i.get("importance") or 3))),
            })
    # Guard against a bad generation wiping memory.
    if not items or len(items) < min(5, len(memories)) // 2:
        log.warning("compaction returned %d items for %d memories; keeping originals",
                    len(items), len(memories))
        return len(memories)
    store.replace_memories(items[: config.MEMORY_COMPACT_TARGET])
    store.export_memories()
    log.info("compacted memories %d -> %d", len(memories), len(items))
    return len(items)
