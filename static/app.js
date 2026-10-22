// Personal Assistant – static chat client (no build step).
const $ = (s) => document.querySelector(s);
const els = {
  sidebar: $("#sidebar"), threads: $("#threads"), messages: $("#messages"), empty: $("#empty"),
  input: $("#input"), composer: $("#composer"), send: $("#send"), stop: $("#stop"), think: $("#think"),
  title: $("#threadTitle"), status: $("#status"), statusText: $("#statusText"), modelName: $("#modelName"),
};

const state = { threads: [], currentId: null, controller: null, streaming: false };

// ---------- helpers ----------
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderMarkdown(text) {
  if (window.marked && window.DOMPurify) {
    return DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: false }));
  }
  return `<p>${escapeHtml(text).replace(/\n/g, "<br>")}</p>`; // CDN unavailable: plain text
}

function addCopyButtons(root) {
  root.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".copy")) return;
    const b = document.createElement("button");
    b.className = "copy";
    b.textContent = "Copy";
    b.onclick = () => {
      navigator.clipboard.writeText(pre.querySelector("code")?.innerText ?? pre.innerText);
      b.textContent = "Copied";
      setTimeout(() => (b.textContent = "Copy"), 1200);
    };
    pre.appendChild(b);
  });
}

function scrollToBottom(force = false) {
  const m = els.messages;
  if (force || m.scrollHeight - m.scrollTop - m.clientHeight < 120) m.scrollTop = m.scrollHeight;
}

function autoresize() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 220) + "px";
}

function dayGroup(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const days = Math.floor((new Date(now.toDateString()) - new Date(d.toDateString())) / 864e5);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  if (days < 30) return "Previous 30 days";
  return d.toLocaleString(undefined, { month: "long", year: "numeric" });
}

// ---------- threads ----------
async function loadThreads() {
  state.threads = await api("/api/threads");
  renderThreads();
}

function renderThreads() {
  els.threads.innerHTML = "";
  let group = null;
  for (const t of state.threads) {
    const g = dayGroup(t.updated_at);
    if (g !== group) {
      group = g;
      const l = document.createElement("div");
      l.className = "group-label";
      l.textContent = g;
      els.threads.appendChild(l);
    }
    const row = document.createElement("div");
    row.className = "thread" + (t.id === state.currentId ? " active" : "");
    row.innerHTML = `<span class="name"></span><span class="acts">
      <button title="Rename" data-act="rename">✎</button><button title="Delete" data-act="delete">🗑</button></span>`;
    row.querySelector(".name").textContent = t.title;
    row.onclick = (e) => {
      const act = e.target.dataset.act;
      if (act === "rename") return renameThread(t, row);
      if (act === "delete") return deleteThread(t);
      openThread(t.id);
    };
    els.threads.appendChild(row);
  }
  const cur = state.threads.find((t) => t.id === state.currentId);
  els.title.textContent = cur ? cur.title : "Personal Assistant";
}

function renameThread(t, row) {
  const name = row.querySelector(".name");
  const input = document.createElement("input");
  input.value = t.title;
  name.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const commit = async (save) => {
    if (done) return;
    done = true;
    const v = input.value.trim();
    if (save && v && v !== t.title) await api(`/api/threads/${t.id}`, { method: "PATCH", body: { title: v } });
    loadThreads();
  };
  input.onclick = (e) => e.stopPropagation();
  input.onkeydown = (e) => { if (e.key === "Enter") commit(true); if (e.key === "Escape") commit(false); };
  input.onblur = () => commit(true);
}

async function deleteThread(t) {
  if (!confirm(`Delete “${t.title}”? Long-term memories learned from it are kept.`)) return;
  await api(`/api/threads/${t.id}`, { method: "DELETE" });
  if (t.id === state.currentId) newChat();
  loadThreads();
}

function newChat() {
  if (state.streaming) stopStreaming();
  state.currentId = null;
  history.replaceState(null, "", "/");
  els.messages.querySelectorAll(".msg").forEach((n) => n.remove());
  els.empty.classList.remove("hidden");
  renderThreads();
  els.input.focus();
}

async function openThread(id) {
  if (state.streaming) stopStreaming();
  let data;
  try {
    data = await api(`/api/threads/${id}/messages`);
  } catch {
    return newChat();
  }
  state.currentId = id;
  history.replaceState(null, "", `#${id}`);
  els.messages.querySelectorAll(".msg").forEach((n) => n.remove());
  els.empty.classList.toggle("hidden", data.messages.length > 0);
  for (const m of data.messages) {
    if (m.role === "user") appendUser(m.content);
    else appendAssistant(m);
  }
  renderThreads();
  scrollToBottom(true);
  if (window.innerWidth < 760) els.sidebar.classList.add("collapsed");
}

// ---------- messages ----------
function appendUser(text) {
  els.empty.classList.add("hidden");
  const div = document.createElement("div");
  div.className = "msg user";
  div.innerHTML = `<div class="bubble"></div>`;
  div.querySelector(".bubble").textContent = text;
  els.messages.appendChild(div);
  return div;
}

function appendAssistant(m = {}) {
  const div = document.createElement("div");
  div.className = "msg assistant";
  div.innerHTML = `<details class="thinking hidden"><summary>Thought</summary><div class="think-text"></div></details>
    <div class="body"></div><div class="meta"></div>`;
  els.messages.appendChild(div);
  const view = {
    el: div,
    body: div.querySelector(".body"),
    thinkWrap: div.querySelector(".thinking"),
    thinkText: div.querySelector(".think-text"),
    meta: div.querySelector(".meta"),
    content: m.content || "",
    thinking: m.thinking || "",
  };
  if (view.thinking) {
    view.thinkWrap.classList.remove("hidden");
    view.thinkText.textContent = view.thinking;
  }
  if (m.content !== undefined) {
    view.body.innerHTML = renderMarkdown(view.content);
    renderMath(view.body);
    addCopyButtons(view.body);
    setMeta(view, m.stats);
  }
  return view;
}

function setMeta(view, stats) {
  view.meta.innerHTML = "";
  const copy = document.createElement("button");
  copy.textContent = "Copy";
  copy.onclick = () => { navigator.clipboard.writeText(view.content); copy.textContent = "Copied"; };
  view.meta.appendChild(copy);
  if (stats && stats.tok_per_s) {
    const s = document.createElement("span");
    s.textContent = `${stats.output_tokens} tokens · ${stats.tok_per_s} tok/s · ${stats.total_s}s`;
    s.title = `Prompt: ${stats.prompt_tokens} tokens @ ${stats.prompt_tok_per_s} tok/s`;
    view.meta.appendChild(s);
  }
}

// ---------- streaming ----------
async function send(text) {
  if (!text.trim() || state.streaming) return;
  if (!state.currentId) {
    const t = await api("/api/threads", { method: "POST" });
    state.currentId = t.id;
    history.replaceState(null, "", `#${t.id}`);
    await loadThreads();
  }
  appendUser(text);
  const view = appendAssistant();
  view.body.innerHTML = `<div class="typing"><span></span><span></span><span></span></div>`;
  scrollToBottom(true);
  setStreaming(true);

  const tid = state.currentId;
  state.controller = new AbortController();
  let pending = false;
  const paint = () => {
    pending = false;
    if (view.finished) return;
    view.body.innerHTML = renderMarkdown(view.content);
    view.body.lastElementChild?.classList.add("cursor");
    scrollToBottom();
  };

  try {
    const res = await fetch(`/api/threads/${tid}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text, think: els.think.checked }),
      signal: state.controller.signal,
    });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 2);
        if (!line.startsWith("data:")) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.type === "thinking") {
          view.thinking += ev.text;
          view.thinkWrap.classList.remove("hidden");
          view.thinkWrap.open = true;
          view.thinkWrap.querySelector("summary").textContent = "Thinking…";
          view.thinkText.textContent = view.thinking;
          view.thinkText.scrollTop = view.thinkText.scrollHeight;
          if (!view.content) view.body.innerHTML = "";
        } else if (ev.type === "token") {
          if (!view.content && view.thinking) {
            view.thinkWrap.open = false;
            view.thinkWrap.querySelector("summary").textContent = "Thought";
          }
          view.content += ev.text;
          if (!pending) { pending = true; requestAnimationFrame(paint); }
        } else if (ev.type === "done") {
          finish(view, ev.stats);
        } else if (ev.type === "error") {
          throw new Error(ev.message);
        }
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      view.content = withStoppedMarker(view.content);
      finish(view);
    } else {
      view.body.innerHTML = `<div class="error">⚠ ${escapeHtml(err.message)}</div>`;
    }
  } finally {
    setStreaming(false);
    loadThreads();
    // The title is generated in the background after the first reply.
    setTimeout(loadThreads, 4000);
    setTimeout(loadThreads, 12000);
  }
}

function withStoppedMarker(text) {
  // Close an unterminated code fence so the marker isn't swallowed by it.
  const fences = (text.match(/^\s*```/gm) || []).length;
  return text + (fences % 2 ? "\n```" : "") + (text ? "\n\n*[stopped]*" : "*[stopped]*");
}

function renderMath(el) {
  if (!window.renderMathInElement) return;
  try {
    renderMathInElement(el, {
      delimiters: [
        { left: "$$", right: "$$", display: true }, { left: "\\[", right: "\\]", display: true },
        { left: "$", right: "$", display: false }, { left: "\\(", right: "\\)", display: false },
      ],
      ignoredTags: ["pre", "code", "script", "style", "textarea"],
      throwOnError: false,
    });
  } catch {}
}

function finish(view, stats) {
  view.finished = true;
  view.thinkWrap.querySelector("summary").textContent = "Thought";
  view.body.innerHTML = renderMarkdown(view.content || "*(empty response)*");
  renderMath(view.body);
  addCopyButtons(view.body);
  setMeta(view, stats);
  scrollToBottom();
}

function setStreaming(on) {
  state.streaming = on;
  els.send.classList.toggle("hidden", on);
  els.stop.classList.toggle("hidden", !on);
  els.send.disabled = !els.input.value.trim();
}

function stopStreaming() {
  state.controller?.abort();
}

// ---------- memory dialog ----------
const memDialog = $("#memoryDialog");

async function loadMemories() {
  const [data, health] = await Promise.all([api("/api/memories"), api("/api/health")]);
  const sel = $("#memCat");
  if (!sel.options.length) {
    for (const c of data.categories) sel.add(new Option(c, c));
  }
  const jobs = health.jobs || {};
  const busy = (jobs.queued || []).length + (jobs.memory_timers || []).length;
  $("#memJobs").textContent = jobs.status?.running ? `Running ${jobs.status.running}…`
    : busy ? `${busy} pending job(s)` : jobs.status?.last ? `Last: ${jobs.status.last}` : "";
  const list = $("#memList");
  list.innerHTML = data.memories.length ? "" :
    `<p class="muted">No memories yet. Chat a bit — after a chat is idle for a short while, durable facts about you are extracted here.</p>`;
  for (const m of data.memories) {
    const row = document.createElement("div");
    row.className = "mem-row";
    row.innerHTML = `<span class="tag"></span><span class="text" contenteditable="true"></span>
      <span class="imp" title="Importance (1–5)"></span><button title="Delete">✕</button>`;
    row.querySelector(".tag").textContent = m.category;
    row.querySelector(".imp").textContent = "★".repeat(m.importance);
    const text = row.querySelector(".text");
    text.textContent = m.content;
    text.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); text.blur(); } };
    text.onblur = async () => {
      const v = text.textContent.trim();
      if (v && v !== m.content) await api(`/api/memories/${m.id}`, { method: "PATCH", body: { content: v } });
    };
    row.querySelector("button").onclick = async () => {
      await api(`/api/memories/${m.id}`, { method: "DELETE" });
      loadMemories();
    };
    list.appendChild(row);
  }
}

$("#openMemory").onclick = () => { memDialog.showModal(); loadMemories(); };
$("#memAdd").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("#memText").value.trim();
  if (!text) return;
  await api("/api/memories", { method: "POST", body: { content: text, category: $("#memCat").value } });
  $("#memText").value = "";
  loadMemories();
};
async function runJob(path, label) {
  await api(path, { method: "POST" });
  $("#memJobs").textContent = `${label} queued…`;
  // Poll while the job runs so the list refreshes when it lands.
  for (let i = 0; i < 40 && memDialog.open; i++) {
    await new Promise((r) => setTimeout(r, 1500));
    await loadMemories();
    const h = await api("/api/health");
    if (!h.jobs.status?.running && !(h.jobs.queued || []).length) break;
  }
}
$("#flushMemory").onclick = () => runJob("/api/memories/flush", "Extraction");
$("#compactMemory").onclick = () => runJob("/api/memories/compact", "Compaction");

// ---------- context inspector ----------
const ctxDialog = $("#contextDialog");
$("#openContext").onclick = async () => {
  const info = $("#contextInfo"), body = $("#contextBody");
  if (!state.currentId) {
    info.innerHTML = "";
    body.innerHTML = `<p class="muted">Start a chat to inspect the context sent to the model.</p>`;
    return ctxDialog.showModal();
  }
  const data = await api(`/api/threads/${state.currentId}/context`);
  const i = data.info;
  info.innerHTML = [
    `system ≈ ${i.system_tokens} tok`, `history ≈ ${i.history_tokens} tok`,
    `${i.messages_in_context} msgs in context`, `${i.messages_summarized} msgs summarized`,
    `${i.messages_dropped} dropped`, i.has_summary ? "rolling summary: yes" : "rolling summary: no",
  ].map((s) => `<span>${s}</span>`).join("");
  body.innerHTML = "";
  for (const m of data.messages) {
    const d = document.createElement("div");
    d.className = "ctx-msg";
    d.innerHTML = `<div class="role"></div><pre></pre>`;
    d.querySelector(".role").textContent = m.role;
    d.querySelector("pre").textContent = m.content;
    body.appendChild(d);
  }
  ctxDialog.showModal();
};

document.querySelectorAll("dialog [data-close]").forEach((b) => (b.onclick = () => b.closest("dialog").close()));
document.querySelectorAll("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));

// ---------- composer ----------
els.composer.onsubmit = (e) => {
  e.preventDefault();
  const text = els.input.value;
  els.input.value = "";
  autoresize();
  send(text);
};
els.input.addEventListener("input", () => { autoresize(); els.send.disabled = !els.input.value.trim(); });
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    els.composer.requestSubmit();
  }
});
els.stop.onclick = stopStreaming;
$("#newChat").onclick = newChat;
$("#toggleSidebar").onclick = () => els.sidebar.classList.toggle("collapsed");

async function checkHealth() {
  try {
    const h = await api("/api/health");
    els.modelName.textContent = h.model;
    els.status.className = "status " + (h.ollama && h.model_available ? "ok" : "err");
    els.statusText.textContent = !h.ollama ? "Ollama not reachable" : h.model_available ? h.model : `Model ${h.model} missing`;
  } catch {
    els.status.className = "status err";
    els.statusText.textContent = "Server offline";
  }
}

// ---------- boot ----------
(async () => {
  els.send.disabled = true;
  try {
    localStorage.getItem("pa.think") === "1" && (els.think.checked = true);
  } catch {}
  els.think.onchange = () => { try { localStorage.setItem("pa.think", els.think.checked ? "1" : "0"); } catch {} };
  checkHealth();
  setInterval(checkHealth, 30000);
  await loadThreads();
  const fromHash = location.hash.slice(1);
  if (fromHash) openThread(fromHash);
})();
