"""Thin async client for the Ollama /api/chat endpoint."""
from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from . import config

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)

_client = httpx.AsyncClient(base_url=config.OLLAMA_URL, timeout=httpx.Timeout(600, connect=5))


def _options(extra: Optional[dict] = None) -> dict:
    # num_ctx is always sent so chat and background calls share one loaded model.
    opts = {"num_ctx": config.NUM_CTX}
    opts.update(extra or {})
    return opts


def strip_think(text: str) -> str:
    text = _THINK_RE.sub("", text)
    if "</think>" in text:  # unmatched closing tag left by some templates
        text = text.split("</think>", 1)[1]
    return text.strip()


def _with_think_switch(messages: List[Dict[str, str]], think: bool) -> List[Dict[str, str]]:
    """Append Qwen3's soft switch to the last user message.

    The Hugging Face GGUF template ignores Ollama's `"think": false`, so the model reasons
    anyway and the reasoning leaks into `content`. `/no_think` / `/think` is honoured by the
    model itself. Only the outgoing copy is changed; stored history stays clean.
    """
    out = [dict(m) for m in messages]
    for m in reversed(out):
        if m["role"] == "user":
            m["content"] = f"{m['content']} {'/think' if think else '/no_think'}"
            break
    return out


async def stream_chat(
    messages: List[Dict[str, str]], think: bool = False, model: str = config.CHAT_MODEL
) -> AsyncIterator[Dict[str, Any]]:
    payload = {
        "model": model,
        "messages": _with_think_switch(messages, think),
        "stream": True,
        "keep_alive": config.KEEP_ALIVE,
        "options": _options(),
    }
    # Only send `think` when on: with `"think": false` this template leaves a stray
    # "</think>" in the content, while leaving it unset lets Ollama parse the (empty)
    # think block that `/no_think` produces.
    if think:
        payload["think"] = True
    async with _client.stream("POST", "/api/chat", json=payload) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode(errors="replace")
            raise RuntimeError(f"Ollama error {resp.status_code}: {body}")
        async for line in resp.aiter_lines():
            if line.strip():
                chunk = json.loads(line)
                if "error" in chunk:
                    raise RuntimeError(chunk["error"])
                yield chunk


async def complete(
    messages: List[Dict[str, str]],
    schema: Optional[dict] = None,
    model: str = config.MEMORY_MODEL,
    temperature: float = 0.2,
    num_predict: int = 1024,
) -> str:
    """Non-streaming call used by background jobs. Pass `schema` for JSON output."""
    payload: Dict[str, Any] = {
        "model": model,
        "messages": _with_think_switch(messages, False),
        "stream": False,
        "keep_alive": config.KEEP_ALIVE,
        "options": _options({"temperature": temperature, "num_predict": num_predict}),
    }
    if schema is not None:
        payload["format"] = schema
    resp = await _client.post("/api/chat", json=payload)
    resp.raise_for_status()
    return strip_think(resp.json()["message"]["content"])


async def complete_json(messages: List[Dict[str, str]], schema: dict, **kw: Any) -> Dict[str, Any]:
    return json.loads(await complete(messages, schema=schema, **kw))


async def health() -> Dict[str, Any]:
    try:
        tags = (await _client.get("/api/tags", timeout=5)).json()
        names = [m["name"] for m in tags.get("models", [])]
        model_ok = any(n == config.CHAT_MODEL or n.split(":")[0] == config.CHAT_MODEL for n in names)
        return {"ollama": True, "model": config.CHAT_MODEL, "model_available": model_ok}
    except Exception as e:  # noqa: BLE001
        return {"ollama": False, "model": config.CHAT_MODEL, "model_available": False, "error": str(e)}


async def close() -> None:
    await _client.aclose()
