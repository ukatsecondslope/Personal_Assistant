"""Background job runner for the memory harness.

Ollama runs one request at a time here (OLLAMA_NUM_PARALLEL=1), so jobs run one by one and
always yield to live chats: a job waits until no reply is streaming before it starts.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Dict, Optional

from . import config, ollama_client, prompts
from .db import store
from .memory import long_term, short_term

log = logging.getLogger("jobs")


class JobRunner:
    def __init__(self) -> None:
        self._queue: "asyncio.Queue[tuple]" = asyncio.Queue()
        self._queued: set = set()
        self._timers: Dict[str, asyncio.Task] = {}
        self._active_chats = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._worker: Optional[asyncio.Task] = None
        self.status: Dict[str, str] = {}

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self._worker = asyncio.create_task(self._run())
        if config.MEMORY_ENABLED:
            # Turns that were never mined (e.g. server stopped during the idle wait).
            for tid in store.threads_with_unmined_messages():
                self.schedule_memory(tid, delay=5)

    async def stop(self) -> None:
        for t in self._timers.values():
            t.cancel()
        if self._worker:
            self._worker.cancel()

    # ---- chat activity ---------------------------------------------------
    def chat_started(self, thread_id: str) -> None:
        self._active_chats += 1
        self._idle.clear()
        # The user is still talking; push memory extraction back.
        timer = self._timers.pop(thread_id, None)
        if timer:
            timer.cancel()

    def chat_finished(self, thread_id: str, first_turn: bool) -> None:
        self._active_chats = max(0, self._active_chats - 1)
        if self._active_chats == 0:
            self._idle.set()
        if first_turn:
            self.enqueue("title", thread_id)
        self.enqueue("summarize", thread_id)
        if config.MEMORY_ENABLED:
            self.schedule_memory(thread_id)

    # ---- scheduling ------------------------------------------------------
    def enqueue(self, kind: str, thread_id: Optional[str] = None) -> None:
        key = (kind, thread_id)
        if key not in self._queued:
            self._queued.add(key)
            self._queue.put_nowait(key)

    def schedule_memory(self, thread_id: str, delay: float = None) -> None:
        delay = config.MEMORY_IDLE_SECONDS if delay is None else delay
        old = self._timers.pop(thread_id, None)
        if old:
            old.cancel()

        async def later() -> None:
            await asyncio.sleep(delay)
            self._timers.pop(thread_id, None)
            self.enqueue("memory", thread_id)

        self._timers[thread_id] = asyncio.create_task(later())

    def flush_memory(self) -> None:
        """Run pending memory extraction now instead of waiting for the idle timer."""
        for tid in list(self._timers):
            self._timers.pop(tid).cancel()
        for tid in store.threads_with_unmined_messages():
            self.enqueue("memory", tid)

    def pending(self) -> Dict[str, object]:
        return {
            "queued": [f"{k}:{(t or '')[:8]}" for k, t in self._queued],
            "memory_timers": [t[:8] for t in self._timers],
            "status": self.status,
        }

    # ---- worker ----------------------------------------------------------
    async def _run(self) -> None:
        while True:
            kind, tid = await self._queue.get()
            await self._idle.wait()
            self._queued.discard((kind, tid))
            self.status = {"running": f"{kind}:{(tid or '')[:8]}"}
            try:
                if kind == "title":
                    await self._title(tid)
                elif kind == "summarize":
                    if await short_term.maybe_summarize(tid):
                        log.info("thread %s: summary updated", tid[:8])
                elif kind == "memory":
                    await long_term.extract(tid)
                elif kind == "compact":
                    await long_term.compact()
                self.status = {"last": f"{kind}:{(tid or '')[:8]} ok"}
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("job %s failed", kind)
                self.status = {"last": f"{kind}:{(tid or '')[:8]} failed: {e}"}

    async def _title(self, tid: str) -> None:
        thread = store.get_thread(tid)
        msgs = store.messages(tid)
        if not thread or thread["title"] != "New chat" or len(msgs) < 2:
            return
        title = await ollama_client.complete(
            [{"role": "user", "content": prompts.TITLE_PROMPT.format(
                user=msgs[0]["content"][:800], assistant=msgs[1]["content"][:800])}],
            temperature=0.3,
            num_predict=24,
        )
        title = re.sub(r'^["\'#*\s]+|["\'.*\s]+$', "", title.splitlines()[0] if title else "")[:60]
        if title:
            store.update_thread(tid, title=title)


runner = JobRunner()
