"""FastAPI app: chat API (SSE streaming), thread + memory management, and the static UI."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, ollama_client
from .db import store
from .jobs import runner
from .memory import long_term, short_term

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    runner.start()
    yield
    await runner.stop()
    await ollama_client.close()


app = FastAPI(title="Personal Assistant", lifespan=lifespan)


# ---- schemas ---------------------------------------------------------------
class ChatIn(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    think: bool = False


class ThreadPatch(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    category: str = "other"
    importance: int = Field(3, ge=1, le=5)


class MemoryPatch(BaseModel):
    content: Optional[str] = Field(None, min_length=1, max_length=500)
    category: Optional[str] = None
    importance: Optional[int] = Field(None, ge=1, le=5)


def _thread_or_404(tid: str) -> dict:
    thread = store.get_thread(tid)
    if not thread:
        raise HTTPException(404, "thread not found")
    return thread


def _check_category(cat: Optional[str]) -> None:
    if cat is not None and cat not in config.MEMORY_CATEGORIES:
        raise HTTPException(422, f"category must be one of {config.MEMORY_CATEGORIES}")


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ---- health ------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {**await ollama_client.health(), "jobs": runner.pending()}


# ---- threads -----------------------------------------------------------------
@app.get("/api/threads")
def list_threads():
    return store.list_threads()


@app.post("/api/threads")
def create_thread():
    return store.create_thread()


@app.patch("/api/threads/{tid}")
def rename_thread(tid: str, body: ThreadPatch):
    _thread_or_404(tid)
    store.update_thread(tid, title=body.title.strip())
    return store.get_thread(tid)


@app.delete("/api/threads/{tid}")
def delete_thread(tid: str):
    _thread_or_404(tid)
    store.delete_thread(tid)
    return {"ok": True}


@app.get("/api/threads/{tid}/messages")
def thread_messages(tid: str):
    thread = _thread_or_404(tid)
    return {"thread": thread, "messages": store.messages(tid)}


@app.get("/api/threads/{tid}/context")
def thread_context(tid: str):
    """Inspector: exactly what the model would receive for the next turn."""
    _thread_or_404(tid)
    messages, info = short_term.build_context(tid, long_term.render_block())
    return {"info": info, "messages": messages}


# ---- chat --------------------------------------------------------------------
@app.post("/api/threads/{tid}/chat")
async def chat(tid: str, body: ChatIn, request: Request):
    _thread_or_404(tid)
    first_turn = store.message_count(tid) == 0
    user_msg = store.add_message(tid, "user", body.content.strip())
    messages, info = short_term.build_context(tid, long_term.render_block())

    async def events():
        content, thinking, stats = [], [], {}
        saved = False
        runner.chat_started(tid)
        try:
            yield _sse({"type": "start", "user_message_id": user_msg["id"], "context": info})
            async for chunk in ollama_client.stream_chat(messages, think=body.think):
                msg = chunk.get("message", {})
                if msg.get("thinking"):
                    thinking.append(msg["thinking"])
                    yield _sse({"type": "thinking", "text": msg["thinking"]})
                if msg.get("content"):
                    content.append(msg["content"])
                    yield _sse({"type": "token", "text": msg["content"]})
                if chunk.get("done"):
                    ev, ed = chunk.get("eval_count", 0), chunk.get("eval_duration", 0)
                    pc, pd = chunk.get("prompt_eval_count", 0), chunk.get("prompt_eval_duration", 0)
                    stats = {
                        "prompt_tokens": pc,
                        "output_tokens": ev,
                        "tok_per_s": round(ev / ed * 1e9, 1) if ed else None,
                        "prompt_tok_per_s": round(pc / pd * 1e9, 1) if pd else None,
                        "total_s": round(chunk.get("total_duration", 0) / 1e9, 2),
                    }
            amsg = store.add_message(tid, "assistant", "".join(content).strip(),
                                     "".join(thinking).strip(), stats)
            saved = True
            yield _sse({"type": "done", "assistant_message_id": amsg["id"], "stats": stats})
        except Exception as e:  # noqa: BLE001
            log.exception("chat failed")
            yield _sse({"type": "error", "message": str(e)})
        finally:
            # Runs on normal completion, errors and client disconnect ("Stop" button):
            # keep whatever was generated so the thread stays consistent.
            if not saved and (content or thinking):
                partial = "".join(content).strip()
                if partial.count("```") % 2:
                    partial += "\n```"
                store.add_message(tid, "assistant", partial + "\n\n*[stopped]*",
                                  "".join(thinking).strip(), {"stopped": True})
            runner.chat_finished(tid, first_turn)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- long-term memory --------------------------------------------------------
@app.get("/api/memories")
def list_memories():
    return {
        "memories": store.list_memories(),
        "categories": config.MEMORY_CATEGORIES,
        "prompt_block": long_term.render_block(),
    }


@app.post("/api/memories")
def add_memory(body: MemoryIn):
    _check_category(body.category)
    mid = store.add_memory(body.category, body.content.strip(), body.importance)
    store.export_memories()
    return {"id": mid}


@app.patch("/api/memories/{mid}")
def update_memory(mid: int, body: MemoryPatch):
    _check_category(body.category)
    store.update_memory(mid, content=body.content, category=body.category, importance=body.importance)
    store.export_memories()
    return {"ok": True}


@app.delete("/api/memories/{mid}")
def delete_memory(mid: int):
    store.delete_memory(mid)
    store.export_memories()
    return {"ok": True}


@app.post("/api/memories/compact")
def compact_memories():
    runner.enqueue("compact")
    return {"queued": True}


@app.post("/api/memories/flush")
def flush_memories():
    """Extract memories from recent chats now, without waiting for the idle timer."""
    runner.flush_memory()
    return {"queued": True}


# ---- static UI ---------------------------------------------------------------
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")
