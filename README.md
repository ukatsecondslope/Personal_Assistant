# Personal Assistant

A local, ChatGPT-style chat app for the Ollama `qwen3-14b-16k` model: a FastAPI backend, a static
web UI, and a memory harness with short-term (per-thread) and long-term (per-user) memory.
Everything – chats and memory – is stored on this machine in `data/`.

## Run

```bash
./run.sh            # first run creates .venv and installs deps
```

Open http://127.0.0.1:8000. Ollama must be running with the model `qwen3-14b-16k`
(see `PA_CHAT_MODEL` below to use another one).

## Layout

```
app/
  main.py            FastAPI routes: chat (SSE streaming), threads, memories, static UI
  config.py          all settings (env-overridable)
  db.py              SQLite store: threads, messages, memories
  ollama_client.py   async Ollama /api/chat client (streaming + JSON-schema calls)
  prompts.py         system, memory-extraction, compaction, summary and title prompts
  jobs.py            background worker (titles, summaries, memory extraction)
  memory/
    short_term.py    context building + rolling thread summary
    long_term.py     memory extraction, compaction, prompt rendering
static/              index.html, styles.css, app.js (no build step)
data/                created at runtime (git-ignored)
  assistant.db         threads, messages, summaries, memories
  long_term_memory.json  readable mirror of long-term memory
```

## How the memory harness works

**Short-term (thread continuity).** Each request is built as
`system prompt + long-term memory + thread summary + recent messages`. Recent messages are taken
newest-first up to `PA_HISTORY_TOKEN_BUDGET`. When the unsummarised part of a thread passes
`PA_SUMMARIZE_TRIGGER_TOKENS`, older messages (all but the last `PA_KEEP_RECENT_MESSAGES`) are
folded into a rolling summary stored on the thread, so long chats keep their context instead of
silently dropping it. Previous turns' reasoning is never sent back to the model.

**Long-term (user understanding).** After a thread has been idle for `PA_MEMORY_IDLE_SECONDS`, all
new turns are sent to the model with the existing memories. It returns `add` / `update` / `delete`
operations (constrained by a JSON schema) covering identity, preferences, goals, projects, skills,
relationships and usage behaviour. Above `PA_MEMORY_COMPACT_THRESHOLD` memories, the set is merged
into a compact one. The memory block is grouped by category, ranked by importance, trimmed to
`PA_MEMORY_PROMPT_CHAR_BUDGET` and injected into every chat. Manage it from **Memory** in the
sidebar (view, edit inline, add, delete, "Update from recent chats", "Compact").

The **`</>` button** in the top bar shows exactly what will be sent to the model for the next turn.

### Why jobs are batched and idle-gated

Ollama serves one request at a time here, and its prompt cache holds one conversation. Every
background call (title, summary, extraction) waits until no reply is streaming, and extraction is
batched per idle thread, so the chat stays responsive and follow-up messages reuse the prompt cache.
All calls use the same model and `num_ctx`; a different value would make Ollama reload the model.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PA_CHAT_MODEL` | `qwen3-14b-16k` | Ollama model for chat and background jobs |
| `PA_NUM_CTX` | `16384` | Context window sent with every request (match the model) |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama server |
| `PA_DATA_DIR` | `./data` | Where the database lives |
| `PA_HISTORY_TOKEN_BUDGET` | `9000` | Raw history tokens per request |
| `PA_SUMMARIZE_TRIGGER_TOKENS` | `6000` | Summarise older turns past this |
| `PA_KEEP_RECENT_MESSAGES` | `6` | Messages never summarised |
| `PA_MEMORY_ENABLED` | `1` | Turn long-term memory extraction on/off |
| `PA_MEMORY_IDLE_SECONDS` | `45` | Idle time before extracting memories |
| `PA_MEMORY_PROMPT_CHAR_BUDGET` | `3000` | Size of memory block in the prompt |
| `PA_MEMORY_COMPACT_THRESHOLD` | `40` | Compact above this many memories |
| `PORT` | `8000` | Port used by `run.sh` |

## Notes

- **Thinking:** the "Think" toggle enables Qwen3's reasoning (shown collapsible above the answer).
  The Hugging Face GGUF's template ignores Ollama's `think: false`, so the app appends Qwen3's
  `/think` / `/no_think` switch to the outgoing message instead (not stored in history).
- **Stop** keeps the partial answer in the thread, marked `[stopped]`.
- Markdown, math (KaTeX) and DOMPurify load from jsDelivr; without internet the UI falls back to
  plain text.

## API

| Method | Path | |
|---|---|---|
| GET | `/api/health` | Ollama/model status and background job queue |
| GET/POST | `/api/threads` | List / create threads |
| PATCH/DELETE | `/api/threads/{id}` | Rename / delete (memories are kept) |
| GET | `/api/threads/{id}/messages` | Thread + messages |
| GET | `/api/threads/{id}/context` | Context inspector |
| POST | `/api/threads/{id}/chat` | `{content, think}` → SSE: `start`, `thinking`, `token`, `done`, `error` |
| GET/POST | `/api/memories` | List (with rendered prompt block) / add |
| PATCH/DELETE | `/api/memories/{id}` | Edit / delete |
| POST | `/api/memories/flush` | Extract memories now |
| POST | `/api/memories/compact` | Compact memories now |
