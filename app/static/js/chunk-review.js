import { qs, val, escapeHtml, splitCsv } from "./helpers.js";

const palette = [
  "linear-gradient(90deg, rgba(242, 166, 90, 0.16), rgba(242, 166, 90, 0.04))",
  "linear-gradient(90deg, rgba(226, 122, 63, 0.16), rgba(226, 122, 63, 0.04))",
  "linear-gradient(90deg, rgba(156, 90, 99, 0.18), rgba(156, 90, 99, 0.04))",
  "linear-gradient(90deg, rgba(108, 82, 94, 0.16), rgba(108, 82, 94, 0.04))",
];

const chunkState = {
  docId: "",
  text: "",
  lines: [],
  offsets: [],
  chunks: [],
  selectedChunkId: null,
  approxLineHeight: 0,
  viewport: null,
  collection: null,
  isDirty: false,
  lastSavedAt: null,
  version: null,
  statusMessage: "Paste text or load by document ID to begin.",
  metaStats: { chunkCount: 0, lines: 0, chars: 0 },
};

let autosaveTimer = null;
const AUTOSAVE_DELAY = 1000;

function clampLine(line, total = null) {
  const max = total ?? Math.max(1, chunkState.lines.length || 1);
  return Math.min(Math.max(1, line), max);
}

function setStatus(msg) {
  chunkState.statusMessage = msg || "";
  renderStatusBar();
}

function toggleChunkSpinner(isLoading, message = "Contacting OpenAI…") {
  const el = qs("chunkSpinner");
  if (!el) return;
  const text = el.querySelector(".spinner-text");
  if (text) text.textContent = message;
  el.style.display = isLoading ? "inline-flex" : "none";
}

function computeOffsets(text) {
  const lines = text.split("\n");
  const offsets = [];
  let cursor = 0;
  for (let i = 0; i < lines.length; i++) {
    offsets.push(cursor);
    cursor += lines[i].length;
    if (i < lines.length - 1 || text.endsWith("\n")) cursor += 1;
  }
  offsets.push(cursor);
  return offsets;
}

function formatTime(dt) {
  if (!dt) return "Not yet saved";
  try {
    return dt.toLocaleTimeString();
  } catch (e) {
    return "Not yet saved";
  }
}

function renderStatusBar() {
  const statusEl = qs("chunkStatus");
  const statsEl = qs("chunkStats");
  if (!statusEl || !statsEl) return;
  const prefix = chunkState.isDirty ? "Unsaved changes" : "Draft saved";
  const version = chunkState.version ? `v${chunkState.version}` : "v?";
  const chunksPart = `${chunkState.metaStats.chunkCount || 0} chunk(s)`;
  const linesPart = `${chunkState.metaStats.lines || 0} line(s) · ${chunkState.metaStats.chars || 0} chars`;
  const lastSaved = formatTime(chunkState.lastSavedAt);
  const docPart = chunkState.docId ? `doc ${chunkState.docId}` : "no doc id";
  statusEl.textContent = [prefix, chunkState.statusMessage].filter(Boolean).join(" · ");
  statsEl.textContent = `${version} · ${chunksPart} · ${linesPart} · Saved ${lastSaved} · ${docPart}`;
}

function ensureDocId() {
  if (chunkState.docId) return chunkState.docId;
  const inputId = val("chunkDocId").trim();
  const resolved = inputId || `doc-${Date.now()}`;
  chunkState.docId = resolved;
  const input = qs("chunkDocId");
  if (input) input.value = resolved;
  return resolved;
}

function markDirtyAndScheduleSave() {
  chunkState.isDirty = true;
  setStatus("Pending autosave...");
  scheduleAutosave();
}

function scheduleAutosave() {
  if (autosaveTimer) clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(async () => {
    autosaveTimer = null;
    if (!chunkState.chunks.length) return;
    const docId = ensureDocId();
    if (!docId) return;
    await finalizeChunks(false, { silent: true, autosave: true });
  }, AUTOSAVE_DELAY);
}

function setDocumentText(text) {
  chunkState.text = text || "";
  chunkState.lines = chunkState.text.split("\n");
  chunkState.offsets = computeOffsets(chunkState.text);
  renderVirtualLines();
  updateStats();
}

function lineStartChar(line) {
  return chunkState.offsets[Math.max(0, line - 1)] ?? 0;
}

function lineEndChar(line) {
  return chunkState.offsets[Math.max(0, line)] ?? chunkState.text.length;
}

function recalcChunkBounds(chunk) {
  if (chunk.is_meta_chunk || chunk.chunk_kind === "document_meta") {
    const baseLength = chunk.length_chars ?? (chunk.text ? chunk.text.length : 0);
    const baseLines = chunk.length_lines ?? 0;
    return {
      ...chunk,
      doc_id: chunk.doc_id || chunkState.docId,
      start_line: chunk.start_line ?? 0,
      end_line: chunk.end_line ?? 0,
      start_char: chunk.start_char ?? 0,
      end_char: chunk.end_char ?? 0,
      length_lines: baseLines,
      length_chars: baseLength,
    };
  }

  const total = Math.max(1, chunkState.lines.length);
  const startLine = clampLine(chunk.start_line || 1, total);
  const endLine = clampLine(chunk.end_line || startLine, total);
  const startChar = lineStartChar(startLine);
  const endChar = lineEndChar(endLine);

  return {
    ...chunk,
    doc_id: chunk.doc_id || chunkState.docId,
    start_line: startLine,
    end_line: endLine,
    start_char: startChar,
    end_char: endChar,
    text: chunkState.text.slice(startChar, endChar),
    length_lines: endLine - startLine + 1,
    length_chars: Math.max(0, endChar - startChar),
  };
}

function normalizeChunks(chunks) {
  const cleaned = (chunks || []).map((c) => recalcChunkBounds(c));
  cleaned.sort((a, b) => a.start_line - b.start_line);
  chunkState.chunks = cleaned;
  renderChunkList();
  renderVirtualLines();
  updateStats();
  renderDocSummary();
}

function chunkIndexForLine(lineNumber) {
  return chunkState.chunks.findIndex(
    (c) => lineNumber >= c.start_line && lineNumber <= c.end_line,
  );
}

function chunkForLine(lineNumber) {
  const idx = chunkIndexForLine(lineNumber);
  return idx === -1 ? null : chunkState.chunks[idx];
}

function chunkColor(idx) {
  if (idx === -1) return "";
  return palette[idx % palette.length];
}

function renderVirtualLines() {
  const viewport = chunkState.viewport;
  if (!viewport) return;
  const totalLines = Math.max(chunkState.lines.length, 1);
  const approx = chunkState.approxLineHeight || 22;
  const visibleLines = Math.ceil((viewport.clientHeight || 400) / approx);
  const scrollTop = viewport.scrollTop || 0;
  const start = Math.max(0, Math.floor(scrollTop / approx) - 6);
  const end = Math.min(totalLines, start + visibleLines + 12);
  const paddingTop = start * approx;
  const paddingBottom = Math.max(0, (totalLines - end) * approx);

  const wrapper = document.createElement("div");
  wrapper.style.paddingTop = `${paddingTop}px`;
  wrapper.style.paddingBottom = `${paddingBottom}px`;

  for (let i = start; i < end; i++) {
    const lineNumber = i + 1;
    const chunkIdx = chunkIndexForLine(lineNumber);
    const chunk = chunkIdx !== -1 ? chunkState.chunks[chunkIdx] : null;
    const lineEl = document.createElement("div");
    lineEl.className = "chunk-line";
    lineEl.dataset.lineNumber = lineNumber.toString();

    if (chunkIdx !== -1) {
      lineEl.dataset.chunkId = chunk.chunk_id;
      lineEl.style.background = chunkColor(chunkIdx);
      lineEl.classList.add("has-chunk");
    }

    if (chunk && chunk.chunk_id === chunkState.selectedChunkId) {
      lineEl.classList.add("is-selected");
    }
    if (chunk && lineNumber === chunk.start_line) lineEl.classList.add("boundary-start");
    if (chunk && lineNumber === chunk.end_line) lineEl.classList.add("boundary-end");

    const text = chunkState.lines[i] === "" ? "&nbsp;" : escapeHtml(chunkState.lines[i] || "");
    lineEl.innerHTML = `
      <span class="line-number">${lineNumber}</span>
      <span class="line-text">${text}</span>
    `;
    wrapper.appendChild(lineEl);
  }

  viewport.innerHTML = "";
  viewport.appendChild(wrapper);

  if (!chunkState.approxLineHeight) {
    const sample = wrapper.querySelector(".chunk-line");
    if (sample) {
      chunkState.approxLineHeight = sample.getBoundingClientRect().height || 22;
    }
  }
}

function summaryLabel(chunk) {
  const reasons = (chunk.boundary_reasons || []).slice(0, 2).join(", ");
  const conf = chunk.confidence !== undefined ? `conf ${chunk.confidence}` : "";
  const reasonStr = [reasons, conf].filter(Boolean).join(" · ");
  return `Lines ${chunk.start_line}-${chunk.end_line} · ${chunk.length_chars} chars ${reasonStr ? `· ${reasonStr}` : ""}`;
}

function metaChunk() {
  return chunkState.chunks.find((c) => c.is_meta_chunk || c.chunk_kind === "document_meta");
}

function renderChunkList() {
  const container = qs("chunkList");
  if (!container) return;
  if (!chunkState.chunks.length) {
    container.innerHTML = `<div class="muted">Detect chunks to populate this list. Use selection in the viewport to craft manual chunks.</div>`;
    return;
  }

  const visibleChunks = chunkState.chunks.filter((c) => !(c.is_meta_chunk || c.chunk_kind === "document_meta"));

  container.innerHTML = visibleChunks
    .map((chunk, idx) => {
      const isSelected = chunk.chunk_id === chunkState.selectedChunkId;
      const reasons = (chunk.boundary_reasons || []).map((r) => `<span class="chip">${escapeHtml(r)}</span>`).join(" ");
      const tags = (chunk.tags || []).map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join(" ");
      const title = chunk.summary_title || `Chunk ${idx + 1}`;
      return `
        <div class="chunk-item ${isSelected ? "is-selected" : ""}" data-chunk-id="${escapeHtml(chunk.chunk_id)}">
          <div class="row space">
            <div>
              <div class="small-label">${escapeHtml(chunk.chunk_kind || "chunk")}</div>
              <div class="chunk-title">${escapeHtml(title)}</div>
              <div class="mini-text">${summaryLabel(chunk)}</div>
              ${tags ? `<div class="pill-row" style="margin-top:6px;">${tags}</div>` : ""}
            </div>
            <div class="chunk-actions">
              <button class="ghost" data-action="select" data-chunk-id="${escapeHtml(chunk.chunk_id)}">Focus</button>
              <button class="secondary" data-action="split" data-chunk-id="${escapeHtml(chunk.chunk_id)}">Split</button>
              <button class="secondary" data-action="merge-prev" data-chunk-id="${escapeHtml(chunk.chunk_id)}">Merge ↑</button>
              <button class="secondary" data-action="merge-next" data-chunk-id="${escapeHtml(chunk.chunk_id)}">Merge ↓</button>
            </div>
          </div>
          <div class="chunk-handles">
            <div class="handle-group">
              <div class="mini-text">Start</div>
              <div class="row">
                <button class="ghost" data-action="nudge" data-edge="start" data-step="-1" data-chunk-id="${escapeHtml(chunk.chunk_id)}">- line</button>
                <button class="ghost" data-action="nudge" data-edge="start" data-step="1" data-chunk-id="${escapeHtml(chunk.chunk_id)}">+ line</button>
                <button class="ghost" data-action="paragraph" data-edge="start" data-direction="up" data-chunk-id="${escapeHtml(chunk.chunk_id)}">↑ paragraph</button>
                <button class="ghost" data-action="paragraph" data-edge="start" data-direction="down" data-chunk-id="${escapeHtml(chunk.chunk_id)}">↓ paragraph</button>
              </div>
            </div>
            <div class="handle-group">
              <div class="mini-text">End</div>
              <div class="row">
                <button class="ghost" data-action="nudge" data-edge="end" data-step="-1" data-chunk-id="${escapeHtml(chunk.chunk_id)}">- line</button>
                <button class="ghost" data-action="nudge" data-edge="end" data-step="1" data-chunk-id="${escapeHtml(chunk.chunk_id)}">+ line</button>
                <button class="ghost" data-action="paragraph" data-edge="end" data-direction="up" data-chunk-id="${escapeHtml(chunk.chunk_id)}">↑ paragraph</button>
                <button class="ghost" data-action="paragraph" data-edge="end" data-direction="down" data-chunk-id="${escapeHtml(chunk.chunk_id)}">↓ paragraph</button>
              </div>
            </div>
          </div>
          <div class="pill-row" style="margin-top:6px;">${reasons}</div>
        </div>
      `;
    })
    .join("");
}

function renderDocSummary() {
  const panel = qs("docSummary");
  if (!panel) return;
  const meta = metaChunk();
  if (!meta) {
    panel.innerHTML = "";
    panel.style.display = "none";
    return;
  }
  const tags = (meta.tags || []).map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join(" ");
  panel.innerHTML = `
    <div class="doc-summary">
      <div class="row space" style="align-items: baseline;">
        <h3>${escapeHtml(meta.summary_title || "Document summary")}</h3>
        ${tags ? `<div class="tags">${tags}</div>` : ""}
      </div>
      <p>${escapeHtml(meta.text || "")}</p>
    </div>
  `;
  panel.style.display = "block";
}

function nudgeBoundary(chunkId, edge, delta) {
  const idx = chunkState.chunks.findIndex((c) => c.chunk_id === chunkId);
  if (idx === -1) return;
  const target = { ...chunkState.chunks[idx] };
  const total = chunkState.lines.length || 1;

  if (edge === "start") {
    let nextStart = clampLine(target.start_line + delta, total);
    const prev = chunkState.chunks[idx - 1];
    if (prev) nextStart = Math.max(prev.end_line + 1, nextStart);
    nextStart = Math.min(nextStart, target.end_line);
    target.start_line = nextStart;
  } else {
    let nextEnd = clampLine(target.end_line + delta, total);
    const next = chunkState.chunks[idx + 1];
    if (next) nextEnd = Math.min(next.start_line - 1, nextEnd);
    nextEnd = Math.max(nextEnd, target.start_line);
    target.end_line = nextEnd;
  }

  chunkState.chunks[idx] = recalcChunkBounds(target);
  normalizeChunks(chunkState.chunks);
  markDirtyAndScheduleSave();
}

function findParagraphBoundary(lineNumber, direction) {
  const step = direction === "up" ? -1 : 1;
  let cursor = lineNumber + step;
  const total = chunkState.lines.length || 1;
  while (cursor >= 1 && cursor <= total) {
    const val = (chunkState.lines[cursor - 1] || "").trim();
    if (!val) break;
    cursor += step;
  }
  return clampLine(cursor, total);
}

function paragraphAdjust(chunkId, edge, direction) {
  const idx = chunkState.chunks.findIndex((c) => c.chunk_id === chunkId);
  if (idx === -1) return;
  const chunk = { ...chunkState.chunks[idx] };
  if (edge === "start") {
    const targetLine = findParagraphBoundary(chunk.start_line, direction);
    chunk.start_line = direction === "up" ? targetLine : Math.min(targetLine, chunk.end_line);
  } else {
    const targetLine = findParagraphBoundary(chunk.end_line, direction);
    chunk.end_line = direction === "up" ? Math.max(targetLine, chunk.start_line) : targetLine;
  }
  chunkState.chunks[idx] = recalcChunkBounds(chunk);
  normalizeChunks(chunkState.chunks);
  markDirtyAndScheduleSave();
}

function mergeWithNeighbor(chunkId, direction) {
  const idx = chunkState.chunks.findIndex((c) => c.chunk_id === chunkId);
  if (idx === -1) return;
  const neighborIdx = direction === "next" ? idx + 1 : idx - 1;
  if (neighborIdx < 0 || neighborIdx >= chunkState.chunks.length) return;

  const a = chunkState.chunks[Math.min(idx, neighborIdx)];
  const b = chunkState.chunks[Math.max(idx, neighborIdx)];
  const merged = recalcChunkBounds({
    ...a,
    start_line: Math.min(a.start_line, b.start_line),
    end_line: Math.max(a.end_line, b.end_line),
    boundary_reasons: Array.from(new Set([...(a.boundary_reasons || []), ...(b.boundary_reasons || []), "merged"])),
    confidence: Math.min(a.confidence ?? 1, b.confidence ?? 1),
  });

  chunkState.chunks.splice(Math.min(idx, neighborIdx), 2, merged);
  chunkState.selectedChunkId = merged.chunk_id;
  normalizeChunks(chunkState.chunks);
  markDirtyAndScheduleSave();
}

function splitChunk(chunkId) {
  const idx = chunkState.chunks.findIndex((c) => c.chunk_id === chunkId);
  if (idx === -1) return;
  const chunk = chunkState.chunks[idx];
  if (chunk.start_line === chunk.end_line) {
    setStatus("Chunk is a single line; cannot split further.");
    return;
  }
  const midpoint = Math.floor((chunk.start_line + chunk.end_line) / 2);
  const first = recalcChunkBounds({ ...chunk, end_line: midpoint });
  const second = recalcChunkBounds({
    ...chunk,
    start_line: midpoint + 1,
    chunk_id: `${chunk.chunk_id}-b-${Date.now()}`,
    boundary_reasons: ["manual split"],
  });
  chunkState.chunks.splice(idx, 1, first, second);
  chunkState.selectedChunkId = first.chunk_id;
  normalizeChunks(chunkState.chunks);
  markDirtyAndScheduleSave();
}

function selectionLines() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed) return null;
  const anchorLine = sel.anchorNode?.parentElement?.closest(".chunk-line");
  const focusLine = sel.focusNode?.parentElement?.closest(".chunk-line");
  if (!anchorLine || !focusLine) return null;
  const start = Number(anchorLine.dataset.lineNumber || "0");
  const end = Number(focusLine.dataset.lineNumber || "0");
  if (!start || !end) return null;
  return { startLine: Math.min(start, end), endLine: Math.max(start, end) };
}

function createChunkFromSelection() {
  const range = selectionLines();
  if (!range) {
    setStatus("Highlight lines in the viewport to create a chunk.");
    return;
  }
  const { startLine, endLine } = range;
  const docId = val("chunkDocId").trim() || chunkState.docId || `doc-${Date.now()}`;
  chunkState.docId = docId;
  qs("chunkDocId").value = docId;

  const updated = [];
  chunkState.chunks.forEach((c) => {
    if (c.end_line < startLine || c.start_line > endLine) {
      updated.push(c);
    } else {
      if (c.start_line < startLine) {
        updated.push(recalcChunkBounds({ ...c, end_line: startLine - 1 }));
      }
      if (c.end_line > endLine) {
        updated.push(recalcChunkBounds({ ...c, start_line: endLine + 1 }));
      }
    }
  });

  const newChunk = recalcChunkBounds({
    doc_id: docId,
    chunk_id: `manual-${Date.now()}`,
    start_line: startLine,
    end_line: endLine,
    boundary_reasons: ["manual selection"],
    confidence: 1,
    overlap: 0,
    version: 1,
    finalized: false,
  });
  updated.push(newChunk);
  chunkState.selectedChunkId = newChunk.chunk_id;
  normalizeChunks(updated);
  setStatus(`Created chunk from lines ${startLine}-${endLine}.`);
  markDirtyAndScheduleSave();
}

function handleChunkListClick(event) {
  const actionBtn = event.target.closest("[data-action]");
  if (!actionBtn) return;
  const chunkId = actionBtn.dataset.chunkId;
  const action = actionBtn.dataset.action;
  if (!chunkId || !action) return;

  if (action === "select") {
    selectChunk(chunkId, true);
  } else if (action === "split") {
    splitChunk(chunkId);
  } else if (action === "merge-prev") {
    mergeWithNeighbor(chunkId, "prev");
  } else if (action === "merge-next") {
    mergeWithNeighbor(chunkId, "next");
  } else if (action === "nudge") {
    const edge = actionBtn.dataset.edge;
    const step = Number(actionBtn.dataset.step || "0");
    nudgeBoundary(chunkId, edge === "end" ? "end" : "start", step);
  } else if (action === "paragraph") {
    const edge = actionBtn.dataset.edge === "end" ? "end" : "start";
    const direction = actionBtn.dataset.direction === "up" ? "up" : "down";
    paragraphAdjust(chunkId, edge, direction);
  }
}

function selectChunk(chunkId, scroll = false) {
  chunkState.selectedChunkId = chunkId;
  renderChunkList();
  renderVirtualLines();
  if (scroll) {
    const chunk = chunkState.chunks.find((c) => c.chunk_id === chunkId);
    if (chunk) scrollToLine(chunk.start_line);
  }
}

function scrollToLine(lineNumber) {
  if (!chunkState.viewport) return;
  const approx = chunkState.approxLineHeight || 22;
  chunkState.viewport.scrollTop = Math.max(0, (lineNumber - 1) * approx);
}

function handleViewportClick(event) {
  const lineEl = event.target.closest(".chunk-line");
  if (!lineEl) return;
  const lineNumber = Number(lineEl.dataset.lineNumber || "0");
  if (!lineNumber) return;
  const chunk = chunkForLine(lineNumber);
  if (chunk) selectChunk(chunk.chunk_id, false);
}

function updateStats() {
  const chunkCount = chunkState.chunks.length;
  const lines = chunkState.lines.length || 0;
  const chars = chunkState.text.length || 0;
  chunkState.metaStats = { chunkCount, lines, chars };
  renderStatusBar();
}

async function detectChunks() {
  const docId = val("chunkDocId").trim() || `doc-${Date.now()}`;
  const text = val("chunkDocText");
  const min_chars = Number(val("chunkMinChars") || "400");
  const target_chars = Number(val("chunkTargetChars") || "800");
  const max_chars = Number(val("chunkMaxChars") || "1200");
  const overlap = Number(val("chunkOverlap") || "0");

  if (!text.trim()) {
    setStatus("Provide document text to detect chunks.");
    return;
  }

  chunkState.docId = docId;
  qs("chunkDocId").value = docId;
  setDocumentText(text);
  setStatus("Detecting chunks...");
  toggleChunkSpinner(true);

  const payload = { doc_id: docId, text, min_chars, target_chars, max_chars, overlap };
  let res;
  try {
    res = await fetch("/api/chunking/detect?persist=true", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    console.error("Chunk detect failed", e);
    setStatus("Network error calling /api/chunking/detect.");
    toggleChunkSpinner(false);
    return;
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(data?.detail || "Chunk detection failed.");
    toggleChunkSpinner(false);
    return;
  }

  const chunks = Array.isArray(data) ? data : (data.chunks || []);
  const resolvedDocId = data.doc_id || docId;
  chunkState.docId = resolvedDocId;
  qs("chunkDocId").value = resolvedDocId;
  chunkState.version = data.version ?? chunkState.version;

  normalizeChunks(chunks || []);
  setStatus(`Detected ${chunks.length || 0} chunk(s). v${data.version || 1} ${data.persisted ? "draft saved" : ""}`.trim());
  chunkState.isDirty = true;
  renderStatusBar();
  await finalizeChunks(false, { silent: true, autosave: true });
  toggleChunkSpinner(false);
}

async function loadDocumentById(docIdOverride = null) {
  const docId = docIdOverride || val("chunkDocId").trim();
  if (!docId) {
    setStatus("Enter a document ID to load.");
    return;
  }
  setStatus("Loading draft...");
  let res;
  try {
    res = await fetch(`/api/chunking/documents/${encodeURIComponent(docId)}`);
  } catch (e) {
    console.error("Load doc failed", e);
    setStatus("Network error loading document.");
    return;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(data?.detail || "Document not found.");
    return;
  }

  chunkState.docId = docId;
  qs("chunkDocId").value = docId;
  const text = data.text || "";
  qs("chunkDocText").value = text;
  setDocumentText(text);
  normalizeChunks(data.chunks || []);
  chunkState.version = data.version ?? chunkState.version;
  chunkState.lastSavedAt = data.updated_at ? new Date(data.updated_at) : new Date();
  chunkState.isDirty = false;
  renderStatusBar();
  setStatus(`Loaded doc ${docId}. Finalized: ${data.finalized ? "yes" : "no"} · version ${data.version || 1}.`);
}

async function finalizeChunks(finalized = true, options = {}) {
  const { silent = false, autosave = false } = options;
  const docId = chunkState.docId || ensureDocId();
  if (!docId) {
    setStatus("Provide a document ID before saving.");
    return false;
  }
  chunkState.docId = docId;
  if (!chunkState.chunks.length) {
    setStatus("No chunks to save.");
    return false;
  }
  const payload = {
    doc_id: docId,
    text: chunkState.text,
    finalized,
    chunks: chunkState.chunks.map((c) => recalcChunkBounds(c)),
  };
  if (!silent) {
    setStatus(finalized ? "Saving finalized chunks..." : "Saving chunk draft...");
  } else {
    setStatus("Autosaving draft...");
  }
  let res;
  try {
    res = await fetch("/api/chunking/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    console.error("Finalize failed", e);
    setStatus("Network error saving chunks.");
    return false;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(data?.detail || "Failed to save chunk draft.");
    return false;
  }
  if (autosaveTimer) {
    clearTimeout(autosaveTimer);
    autosaveTimer = null;
  }
  chunkState.version = data.version ?? chunkState.version;
  chunkState.lastSavedAt = new Date();
  chunkState.isDirty = false;
  renderStatusBar();
  setStatus(autosave ? "Draft saved automatically." : `Saved ${payload.chunks.length} chunk(s). Finalized: ${data.finalized ? "yes" : "no"}.`);
  if (typeof window.loadDocLibrary === "function") {
    window.loadDocLibrary();
  }
  return true;
}

async function embedChunks() {
  const collection = chunkState.collection || "";
  if (!collection) {
    setStatus("Collection name missing; cannot index.");
    return false;
  }
  if (!chunkState.chunks.length) {
    setStatus("No chunks to index.");
    return false;
  }

  const chunk_kind = val("chunkDefaultKind") || "chapter_text";
  const thing_id = val("chunkDefaultThingId").trim() || null;
  const thing_type = val("chunkDefaultThingType")?.trim() || (thing_id ? thing_id.split(".")[0] : null) || null;
  const tags = splitCsv(val("chunkDefaultTags"));

  const payload = {
    chunks: chunkState.chunks.map((chunk) => ({
      chunk_id: chunk.chunk_id,
      text: chunk.text,
      chunk_kind,
      thing_id,
      thing_type,
      source_file: chunkState.docId,
      source_section: `lines ${chunk.start_line}-${chunk.end_line}`,
      tags,
      entity_ids: thing_id ? [thing_id] : [],
    })),
  };

  setStatus(`Indexing ${payload.chunks.length} chunk(s) to ${collection}...`);
  let res;
  try {
    res = await fetch(`/api/collections/${encodeURIComponent(collection)}/chunks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    console.error("Embed failed", e);
    setStatus("Network error indexing chunks.");
    return false;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(data?.detail || "Indexing failed.");
    return false;
  }
  setStatus(`Indexed ${data.upserted || payload.chunks.length} chunk(s) to ${collection}.`);
  return true;
}

function wireEvents() {
  const viewport = qs("chunkViewport");
  chunkState.viewport = viewport;
  if (viewport) {
    viewport.addEventListener("scroll", renderVirtualLines);
    viewport.addEventListener("click", handleViewportClick);
  }
  qs("chunkDetectBtn")?.addEventListener("click", (e) => { e.preventDefault(); detectChunks(); });
  qs("chunkLoadBtn")?.addEventListener("click", (e) => { e.preventDefault(); loadDocumentById(); });
  qs("chunkSaveDraftBtn")?.addEventListener("click", async (e) => {
    e.preventDefault();
    await finalizeChunks(false);
  });
  qs("chunkSaveEmbedBtn")?.addEventListener("click", async (e) => {
    e.preventDefault();
    const saved = await finalizeChunks(true);
    if (saved) await embedChunks();
  });
  qs("chunkSelectionBtn")?.addEventListener("click", (e) => { e.preventDefault(); createChunkFromSelection(); });
  qs("chunkScrollTopBtn")?.addEventListener("click", (e) => { e.preventDefault(); scrollToLine(1); });
  qs("chunkList")?.addEventListener("click", handleChunkListClick);
  qs("chunkDocText")?.addEventListener("input", (e) => {
    setDocumentText(e.target.value);
    if (chunkState.chunks.length) normalizeChunks(chunkState.chunks);
    markDirtyAndScheduleSave();
  });
}

function initChunkReview() {
  const panel = qs("chunkReviewPanel");
  if (!panel) return;
  chunkState.collection = window.collectionName || null;
  setDocumentText(val("chunkDocText"));
  wireEvents();
  renderVirtualLines();
  renderStatusBar();
  setStatus("Paste text or load by document ID to begin.");
}

document.addEventListener("DOMContentLoaded", initChunkReview);

// Expose loader for other modules
window.loadDocumentById = loadDocumentById;
