import { qs, val, splitCsv, escapeHtml } from "./helpers.js";

const state = {
  text: "",
  lines: [],
  chunks: [],
  selected: null,
  defaultKind: "chapter_text",
  defaultTags: [],
  collection: window.collectionName || "",
  docId: "",
};

const viewport = () => qs("chunkViewport");
const linesEl = () => qs("chunkLines");

function setStatus(msg) {
  const el = qs("chunkStatus");
  if (el) el.textContent = msg;
}

function switchChunkTab(name) {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.style.display = panel.dataset.panel === name ? "block" : "none";
  });
}

window.switchChunkTab = switchChunkTab;

function hydrateDefaults() {
  state.collection = val("chunkCollection").trim() || state.collection;
  state.docId = val("chunkDocId").trim();
  state.defaultKind = val("defaultChunkKind") || "chapter_text";
  state.defaultTags = splitCsv(val("defaultTags"));
}

function renderLines() {
  const el = linesEl();
  if (!el) return;
  const vp = viewport();
  const height = vp?.clientHeight || 400;
  const lineHeight = 26; // approx with padding
  const startIdx = Math.max(0, Math.floor((vp?.scrollTop || 0) / lineHeight) - 8);
  const endIdx = Math.min(state.lines.length, startIdx + Math.ceil(height / lineHeight) + 16);

  const visible = [];
  for (let i = startIdx; i < endIdx; i++) {
    const line = state.lines[i];
    const chunk = state.chunks.find((c) => i >= c.start_line && i <= c.end_line);
    const isStart = chunk && i === chunk.start_line;
    const isEnd = chunk && i === chunk.end_line;
    const isActive = state.selected && chunk && chunk.chunk_id === state.selected.chunk_id;
    const labels = chunk
      ? `<span class="chunk-label">${escapeHtml(chunk.chunk_id || `chunk-${i}`)}</span>`
      : "";
    const handles = chunk
      ? `<div class="markers">
          <button class="chunk-handle" data-handle="start" data-line="${i}" data-chunk="${chunk.chunk_id}">Start</button>
          <button class="chunk-handle end" data-handle="end" data-line="${i}" data-chunk="${chunk.chunk_id}">End</button>
        </div>`
      : "";
    visible.push(`
      <div class="chunk-line ${isStart ? "chunk-start" : ""} ${isEnd ? "chunk-end" : ""} ${isActive ? "chunk-active" : ""}" data-line="${i}" data-chunk="${chunk?.chunk_id || ""}">
        <div class="line-no">${i + 1}</div>
        <div class="line-text">${escapeHtml(line || "")}
          ${labels}
          ${handles}
        </div>
      </div>
    `);
  }

  el.innerHTML = visible.join("");
}

function onScroll() { renderLines(); }

function setChunks(chunks) {
  state.chunks = (chunks || []).map((c) => ({ ...c }));
  if (!state.chunks.length) state.selected = null;
  renderLines();
  updateSelectedMeta();
}

function setText(text) {
  state.text = text || "";
  state.lines = state.text.split("\n");
  renderLines();
}

function updateSelectedMeta() {
  const el = qs("selectedMeta");
  if (!el) return;
  if (!state.selected) {
    el.textContent = "No chunk selected.";
    return;
  }
  const c = state.selected;
  el.textContent = `${c.chunk_id || "chunk"} · lines ${c.start_line + 1}-${c.end_line + 1} (${c.chunk_kind || state.defaultKind})`;
}

function handleLineClick(evt) {
  const lineEl = evt.target.closest(".chunk-line");
  if (!lineEl) return;
  const lineNum = Number(lineEl.dataset.line);
  const chunk = state.chunks.find((c) => lineNum >= c.start_line && lineNum <= c.end_line);
  if (chunk) {
    state.selected = chunk;
    updateSelectedMeta();
    renderLines();
  }
}

function handleHandleClick(evt) {
  const btn = evt.target.closest(".chunk-handle");
  if (!btn) return;
  const line = Number(btn.dataset.line);
  const chunkId = btn.dataset.chunk;
  const chunk = state.chunks.find((c) => c.chunk_id === chunkId);
  if (!chunk) return;
  if (btn.dataset.handle === "start") {
    chunk.start_line = Math.max(0, Math.min(line, chunk.end_line));
  } else {
    chunk.end_line = Math.max(chunk.start_line, line);
  }
  state.selected = chunk;
  renderLines();
  updateSelectedMeta();
}

function mergeWithNext() {
  if (!state.selected) return;
  const idx = state.chunks.findIndex((c) => c.chunk_id === state.selected.chunk_id);
  if (idx === -1 || idx === state.chunks.length - 1) return;
  const next = state.chunks[idx + 1];
  state.chunks[idx].end_line = next.end_line;
  state.chunks[idx].text = `${state.chunks[idx].text || ""}\n${next.text || ""}`;
  state.chunks.splice(idx + 1, 1);
  renderLines();
  updateSelectedMeta();
}

function splitSelection() {
  if (!state.selected) return;
  const lineCount = state.lines.length;
  const mid = Math.floor((state.selected.start_line + state.selected.end_line) / 2);
  if (mid <= state.selected.start_line || mid >= state.selected.end_line) return;

  const a = { ...state.selected, end_line: mid };
  const b = {
    ...state.selected,
    start_line: mid + 1,
    chunk_id: `${state.selected.chunk_id || "chunk"}-part2`,
  };
  state.chunks = state.chunks.flatMap((c) => (c.chunk_id === state.selected.chunk_id ? [a, b] : [c]));
  state.selected = a;
  renderLines();
  updateSelectedMeta();
}

async function detectChunks() {
  hydrateDefaults();
  setStatus("Detecting...");
  const payload = {
    text: val("chunkSource").trim() || null,
    doc_id: val("chunkDocId").trim() || null,
    collection: val("chunkCollection").trim() || null,
    chunk_kind: val("defaultChunkKind") || null,
  };

  const res = await fetch("/api/chunking/detect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(data.detail || "Detect failed");
    return;
  }

  state.collection = payload.collection || state.collection;
  state.docId = payload.doc_id || state.docId;
  state.defaultKind = payload.chunk_kind || state.defaultKind;
  setText(data.text || "");
  setChunks(data.chunks || []);
  setStatus(`Detected ${data.chunks?.length || 0} chunk(s).`);
  switchChunkTab("review");
}

async function finalizeChunks(embed) {
  hydrateDefaults();
  if (!state.collection) {
    setStatus("Collection is required to save.");
    return;
  }
  const payload = {
    text: val("chunkSource").trim() || state.text,
    doc_id: val("chunkDocId").trim() || null,
    collection: state.collection,
    chunks: state.chunks,
    embed,
    default_chunk_kind: state.defaultKind,
  };

  // Apply default tags when missing
  payload.chunks = payload.chunks.map((c) => ({
    ...c,
    tags: (c.tags && c.tags.length) ? c.tags : state.defaultTags,
  }));

  const res = await fetch("/api/chunking/finalize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(data.detail || "Save failed");
    return;
  }
  setStatus(embed ? `Saved ${data.saved} chunk(s) and embedded.` : `Draft saved (${data.saved} prepared).`);
}

function onClick(evt) {
  if (evt.target.closest(".chunk-handle")) {
    handleHandleClick(evt);
  }
}

function init() {
  const vp = viewport();
  if (vp) vp.addEventListener("scroll", onScroll);
  document.addEventListener("click", handleLineClick);
  document.addEventListener("click", onClick);

  const detectBtn = qs("detectBtn");
  if (detectBtn) detectBtn.addEventListener("click", (e) => { e.preventDefault(); detectChunks(); });
  const draftBtn = qs("saveDraftBtn");
  if (draftBtn) draftBtn.addEventListener("click", (e) => { e.preventDefault(); finalizeChunks(false); });
  const embedBtn = qs("saveEmbedBtn");
  if (embedBtn) embedBtn.addEventListener("click", (e) => { e.preventDefault(); finalizeChunks(true); });
  const mergeBtn = qs("mergeBtn");
  if (mergeBtn) mergeBtn.addEventListener("click", (e) => { e.preventDefault(); mergeWithNext(); });
  const splitBtn = qs("splitBtn");
  if (splitBtn) splitBtn.addEventListener("click", (e) => { e.preventDefault(); splitSelection(); });

  setText(val("chunkSource") || "");
  renderLines();
}

document.addEventListener("DOMContentLoaded", init);
