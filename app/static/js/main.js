import {
  qs, val, splitCsv, parseOptionalInt, parseJsonObject, safeJsonParse, toList, escapeHtml
} from "./helpers.js";
import { cardTemplate } from "./cards.js";

const docFindings = [];
const docSpinnerId = "docSpinner";
let docLibrary = [];

function formatTimestamp(value) {
  if (!value) return "";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return "";
  return dt.toLocaleString();
}

function extractDocId(payload) {
  if (!payload) return null;
  if (payload.chunk_state?.doc_id) return payload.chunk_state.doc_id;
  if (Array.isArray(payload.files) && payload.files.length) {
    const withChunk = payload.files.find((f) => f?.chunk_state?.doc_id);
    if (withChunk?.chunk_state?.doc_id) return withChunk.chunk_state.doc_id;
  }
  return payload.doc_id || null;
}

function toggleDocSpinner(isLoading, message = "Contacting OpenAI…") {
  const el = qs(docSpinnerId);
  if (!el) return;
  const text = el.querySelector(".spinner-text");
  if (text) text.textContent = message;
  el.style.display = isLoading ? "inline-flex" : "none";
}

// ---------------- Collections ----------------

async function createCollection() {
  const name = val("newCollectionName").trim();
  const msg = qs("createCollectionMsg");
  if (msg) msg.textContent = "";

  if (!name) {
    if (msg) msg.textContent = "Please enter a collection name.";
    return;
  }

  const res = await fetch("/api/collections", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ name })
  });

  const data = await res.json().catch(() => ({}));
  if (msg) msg.textContent = res.ok ? `Created: ${data.name}` : (data.detail || "Error creating collection");
  if (res.ok) window.location.reload();
}

// ---------------- Chunks ----------------

async function upsertChunk(collection) {
  const msg = qs("upsertMsg");
  if (msg) msg.textContent = "";

  const chunk_id = val("chunkId").trim();
  const text = val("chunkText").trim();

  if (!chunk_id || !text) {
    if (msg) msg.textContent = "chunk_id and text are required.";
    return;
  }

  const payload = {
    chunks: [{
      chunk_id,
      text,
      chunk_kind: val("chunkKind") || "thing_summary",
      thing_id: val("thingId").trim() || null,
      thing_type: val("thingType").trim() || null,
      edge_id: val("edgeId").trim() || null,
      source_file: val("sourceFile").trim() || null,
      source_section: val("sourceSection").trim() || null,
      chapter_number: parseOptionalInt(val("chapterNumber")),
      scene_id: val("sceneId").trim() || null,
      pov: val("pov").trim() || null,
      location_id: val("locationId").trim() || null,
      tags: splitCsv(val("tags")),
      entity_ids: splitCsv(val("entityIds")),
      extra: null
    }]
  };

  try {
    payload.chunks[0].extra = parseJsonObject(val("extraJson"));
  } catch (e) {
    if (msg) msg.textContent = `Extra JSON error: ${e.message}`;
    return;
  }

  const res = await fetch(`/api/collections/${encodeURIComponent(collection)}/chunks`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  });

  const data = await res.json().catch(() => ({}));
  if (msg) msg.textContent = res.ok ? `Upserted ${data.upserted} chunk(s).` : (data.detail || "Error upserting chunk");
  if (res.ok) {
    loadChunks(collection).catch(() => {});
    closeCardModal();
  }
}

async function queryChunks(collection) {
  const results = qs("cardsList");
  const status = qs("cardsStatus");
  if (status) status.textContent = "Searching...";
  results.innerHTML = "";

  const selectedKinds = Array.from(qs("queryChunkKind")?.selectedOptions || []).map(o => o.value);
  const selectedThingTypes = Array.from(qs("queryThingType")?.selectedOptions || []).map(o => o.value);
  const queryTags = splitCsv(val("queryTags"));
  const payload = {
    query_text: val("queryText").trim(),
    n_results: parseOptionalInt(val("topK")) || 8,
    chunk_kinds: selectedKinds.length ? selectedKinds : null,
    thing_types: selectedThingTypes.length ? selectedThingTypes : null,
    thing_id: val("queryThingId").trim() || null,
    tags: queryTags.length ? queryTags : null
  };

  if (!payload.query_text) {
    results.innerHTML = `<div class="muted">Enter a query.</div>`;
    if (status) status.textContent = "Enter a query.";
    return;
  }

  const res = await fetch(`/api/collections/${encodeURIComponent(collection)}/query`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  });

  const hits = await res.json().catch(() => []);
  if (!res.ok) {
    results.innerHTML = `<div class="muted">Error: ${(hits && hits.detail) ? hits.detail : "query failed"}</div>`;
    if (status) status.textContent = "";
    return;
  }

  if (!hits.length) {
    results.innerHTML = `<div class="muted">No results.</div>`;
    if (status) status.textContent = "No results.";
    return;
  }

  results.innerHTML = hits.map(h => cardTemplate({
    id: h.id,
    text: h.text,
    metadata: h.metadata
  }, { distance: h.distance, collection })).join("");
  if (status) status.textContent = `Showing ${hits.length} result(s).`;
}

async function loadChunks(collection) {
  const docsEl = qs("cardsList");
  const msg = qs("cardsStatus");
  if (msg) msg.textContent = "Loading...";
  docsEl.innerHTML = "";

  const limit = parseOptionalInt(val("browseLimit")) || 25;

  const res = await fetch(`/api/collections/${encodeURIComponent(collection)}/chunks?limit=${limit}`);
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    if (msg) msg.textContent = data.detail || "Error loading chunks";
    return;
  }

  if (msg) msg.textContent = `Loaded ${data.count} chunk(s).`;

  const items = data.items || [];
  if (!items.length) {
    docsEl.innerHTML = `<div class="muted">No chunks yet.</div>`;
    return;
  }

  docsEl.innerHTML = items.map(it => cardTemplate({
    id: it.id,
    text: it.text,
    metadata: it.metadata
  }, { collection })).join("");
}

// ---------------- Connections ----------------

async function saveConnection() {
  const msg = qs("connectionsMsg");
  if (msg) msg.textContent = "";

  const edge_id = val("edgeFormId").trim();
  const src_id = val("edgeSrc").trim();
  const dst_id = val("edgeDst").trim();
  const rel_type = val("edgeRelType").trim();

  if (!edge_id || !src_id || !dst_id || !rel_type) {
    if (msg) msg.textContent = "Edge id, source, destination, and type are required.";
    return;
  }

  const payload = {
    edge_id,
    src_id,
    dst_id,
    rel_type,
    tags: splitCsv(val("edgeTags")),
    note: val("edgeNote").trim() || null
  };

  const res = await fetch(`/api/connections`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  });
  const data = await res.json().catch(() => ({}));
  if (msg) msg.textContent = res.ok ? `Saved ${data.edge_id}` : (data.detail || "Error saving connection");
  if (res.ok) loadConnections().catch(() => {});
}

async function deleteConnection() {
  const msg = qs("connectionsMsg");
  if (msg) msg.textContent = "";
  const edge_id = val("edgeFormId").trim();
  if (!edge_id) {
    if (msg) msg.textContent = "Enter an edge id to delete.";
    return;
  }

  const res = await fetch(`/api/connections/${encodeURIComponent(edge_id)}`, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  if (msg) msg.textContent = res.ok ? `Deleted ${edge_id}` : (data.detail || "Error deleting connection");
  if (res.ok) loadConnections().catch(() => {});
}

async function loadConnections() {
  const list = qs("connectionsList");
  if (!list) return;
  list.innerHTML = "";

  const res = await fetch(`/api/connections`);
  const data = await res.json().catch(() => []);

  if (!res.ok) {
    list.innerHTML = `<div class="muted">Error loading connections.</div>`;
    return;
  }

  if (!data.length) {
    list.innerHTML = `<div class="muted">No connections yet.</div>`;
    return;
  }

  list.innerHTML = data.map(edge => {
    const tags = toList(edge.tags).map(t => `<span class="chip">${escapeHtml(t)}</span>`).join(" ");
    const edgePayload = encodeURIComponent(JSON.stringify(edge));
    return `
      <div class="card" data-edge="${edgePayload}">
        <div class="card-header">
          <div>
            <div class="small-label">Edge</div>
            <div style="font-weight:700;">${escapeHtml(edge.edge_id)}</div>
          </div>
          <div class="card-actions">
            <button class="ghost js-edit-connection" data-edge="${edgePayload}">Edit</button>
            <button class="danger js-delete-connection" data-edge-id="${encodeURIComponent(edge.edge_id)}">Delete</button>
          </div>
        </div>
        <div class="kv">
          <div class="label">Source</div><div>${escapeHtml(edge.src_id)}</div>
          <div class="label">Destination</div><div>${escapeHtml(edge.dst_id)}</div>
          <div class="label">Type</div><div>${escapeHtml(edge.rel_type)}</div>
        </div>
        ${edge.note ? `<p class="mini-text" style="margin-top:8px;">${escapeHtml(edge.note)}</p>` : ""}
        <div class="pill-row" style="margin-top:8px; gap:6px;">${tags}</div>
      </div>
    `;
  }).join("");
}

async function deleteConnectionById(edgeId) {
  const msg = qs("connectionsMsg");
  if (msg) msg.textContent = "";
  const res = await fetch(`/api/connections/${encodeURIComponent(edgeId)}`, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  if (msg) msg.textContent = res.ok ? `Deleted ${edgeId}` : (data.detail || "Error deleting connection");
  if (res.ok) loadConnections().catch(() => {});
}

function fillEdgeForm(edge_id, src_id, dst_id, rel_type, tags, note) {
  qs("edgeFormId").value = edge_id;
  qs("edgeSrc").value = src_id;
  qs("edgeDst").value = dst_id;
  qs("edgeRelType").value = rel_type;
  const tagList = Array.isArray(tags) ? tags : splitCsv(typeof tags === "string" ? tags : "");
  qs("edgeTags").value = tagList.join(", ");
  qs("edgeNote").value = note || "";
}

function fillFormFromCard(chunkId, text, metadataJson) {
  try {
    let meta = typeof metadataJson === "string" ? safeJsonParse(metadataJson, {}) : (metadataJson || {});

    qs("chunkId").value = chunkId || "";
    qs("chunkKind").value = meta.chunk_kind || "thing_summary";
    qs("thingId").value = meta.thing_id || "";
    qs("thingType").value = meta.thing_type || "";
    qs("edgeId").value = meta.edge_id || "";
    qs("chunkText").value = text || "";
    qs("tags").value = toList(meta.tags).join(", ");
    qs("entityIds").value = toList(meta.entity_ids).join(", ");
    qs("sourceFile").value = meta.source_file || "";
    qs("sourceSection").value = meta.source_section || "";
    qs("chapterNumber").value = meta.chapter_number ?? "";
    qs("sceneId").value = meta.scene_id || "";
    qs("pov").value = meta.pov || "";
    qs("locationId").value = meta.location_id || "";

    const extraEntries = Object.entries(meta)
      .filter(([k]) => k.startsWith("extra."))
      .reduce((acc, [k, v]) => {
        acc[k.replace("extra.", "")] = v;
        return acc;
      }, {});
    qs("extraJson").value = Object.keys(extraEntries).length ? JSON.stringify(extraEntries, null, 2) : "";
    openCardModal();
  } catch (e) {
    console.error("Failed to fill form", e);
  }
}

async function deleteChunk(collection, chunkId) {
  const msg = qs("upsertMsg");
  if (msg) msg.textContent = "";
  const res = await fetch(`/api/collections/${encodeURIComponent(collection)}/chunks/${encodeURIComponent(chunkId)}`, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  if (msg) msg.textContent = res.ok ? `Deleted ${chunkId}` : (data.detail || "Error deleting chunk");
  if (res.ok) loadChunks(collection).catch(() => {});
}

// ---------------- Document scout (UI-only) ----------------
async function analyzeDocument() {
  const status = qs("docStatus");
  const fileInput = qs("docFile");
  const collection = window.collectionName || "demo_lore";
  const notes = val("docNotes").trim();
  const pasted = val("docText").trim();
  logDoc("Starting document ingest...");

  if (status) status.textContent = "Reading document...";

  let text = pasted;
  const files = Array.from(fileInput?.files || []);
  if (!text && files.length === 1) {
    try {
      text = await readFileText(files[0]);
    } catch (e) {
      if (status) status.textContent = "Failed to read file.";
      logDoc("Failed to read file. See console for details.");
      console.error("File read failed", e);
      return;
    }
  }

  if (!text && !files.length) {
    if (status) status.textContent = "Provide a file or pasted document text.";
    logDoc("No content provided.");
    return;
  }

  if (files.length) {
    toggleDocSpinner(true, "Contacting OpenAI…");
    if (status) status.textContent = `Uploading ${files.length} file(s)...`;
    logDoc(`Sending ${files.length} file(s) to /api/ingest/upload...`);
  } else {
    if (status) status.textContent = "Sending to OpenAI...";
    toggleDocSpinner(true);
    logDoc("Sending request to /api/ingest/openai...");
  }

  let data = {};
  try {
    let res;
    if (files.length) {
      const form = new FormData();
      form.append("collection", collection);
      form.append("notes", notes);
      files.forEach((f) => form.append("files", f));
      res = await fetch("/api/ingest/upload", { method: "POST", body: form });
    } else {
      res = await fetch("/api/ingest/openai", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          collection,
          text,
          notes,
          url: val("docUrl").trim() || null
        })
      });
    }

    data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (status) status.textContent = data.detail || `Ingestion failed (HTTP ${res.status}).`;
      console.error("Ingestion failed", res.status, data);
      logDoc(`Ingestion failed: ${data.detail || res.status}`);
      toggleDocSpinner(false);
      return;
    }
  } catch (e) {
    if (status) status.textContent = "Network error sending to OpenAI.";
    toggleDocSpinner(false);
    console.error("Ingestion request error", e);
    logDoc("Network error sending to OpenAI.");
    return;
  }

  const chunks = data.chunks || [];
  docFindings.length = 0;
  docFindings.push(...chunks.map((c, idx) => ({
    id: c.chunk_id || `chunk-${idx}`,
    title: c.chunk_id || `Chunk ${idx + 1}`,
    excerpt: c.text || "",
    tags: c.tags || [],
    status: "pending"
  })));

  renderDocFindings();
  if (files.length) {
    const totals = data.totals || {};
    if (status) status.textContent = `Uploaded ${files.length} file(s). Added ${totals.things || 0} things, ${totals.connections || 0} connections, ${totals.chunks || 0} chunks.`;
    logDoc(`Upload ingest complete. Files: ${files.length}, Things: ${totals.things || 0}, Connections: ${totals.connections || 0}, Chunks: ${totals.chunks || 0}`);
    (data.files || []).forEach((f) => {
      if (f.error) {
        logDoc(`- ${f.file?.filename || "file"}: ERROR ${f.error}`);
      } else {
        logDoc(`- ${f.file?.filename || "file"} stored at ${f.file?.url || "n/a"} (chunks ${f.counts?.chunks || 0})`);
      }
    });
  } else {
    if (status) status.textContent = `Ingested. Added ${data.counts?.things || 0} things, ${data.counts?.connections || 0} connections, ${data.counts?.chunks || 0} chunks.`;
    logDoc(`Ingest complete. Things: ${data.counts?.things || 0}, Connections: ${data.counts?.connections || 0}, Chunks: ${data.counts?.chunks || 0}`);
  }
  toggleDocSpinner(false);

  const docId = extractDocId(data);
  if (docId) {
    const docInput = qs("chunkDocId");
    if (docInput) docInput.value = docId;
    if (window.loadDocumentById) {
      window.loadDocumentById(docId);
      switchTab("chunking");
    }
    if (status) status.textContent = `Processed doc ${docId}. You can adjust chunks below.`;
  }
  loadDocLibrary().catch(() => {});
  if (collection) {
    loadChunks(collection).catch(() => {});
    loadConnections().catch(() => {});
  }
}

async function uploadDocsForChunking() {
  const status = qs("docStatus");
  const fileInput = qs("docFile");
  const files = Array.from(fileInput?.files || []);
  const minChars = val("chunkMinChars");
  const targetChars = val("chunkTargetChars");
  const maxChars = val("chunkMaxChars");
  const overlap = val("chunkOverlap");
  const collection = window.collectionName || null;

  if (status) status.textContent = "";
  if (!files.length) {
    if (status) status.textContent = "Select one or more files to upload.";
    return;
  }

  const form = new FormData();
  if (collection) form.append("collection", collection);
  if (minChars) form.append("min_chars", minChars);
  if (targetChars) form.append("target_chars", targetChars);
  if (maxChars) form.append("max_chars", maxChars);
  if (overlap) form.append("overlap", overlap);
  files.forEach((f) => form.append("files", f));

  toggleDocSpinner(true, "Uploading for chunking…");
  if (status) status.textContent = `Uploading ${files.length} file(s) for chunking...`;
  logDoc(`Uploading ${files.length} file(s) to /api/chunking/upload...`);

  let res;
  try {
    res = await fetch("/api/chunking/upload", { method: "POST", body: form });
  } catch (e) {
    console.error("Chunking upload failed", e);
    if (status) status.textContent = "Network error uploading files.";
    toggleDocSpinner(false);
    return;
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (status) status.textContent = data.detail || "Upload failed.";
    toggleDocSpinner(false);
    return;
  }

  const docs = data.docs || [];
  const primaryDocId = data.primary_doc_id;
  if (status) status.textContent = `Uploaded ${docs.length} file(s) for chunking.${primaryDocId ? ` Loaded ${primaryDocId}.` : ""}`;
  docs.forEach((d) => {
    if (d.error) {
      logDoc(`- ${d.file?.filename || "file"}: ERROR ${d.error}`);
    } else {
      logDoc(`- ${d.doc_id || d.file?.filename || "doc"}: ${d.chunk_count || 0} chunk(s), ${d.text_length || 0} chars`);
    }
  });
  toggleDocSpinner(false);

  loadDocLibrary().catch(() => {});
  if (primaryDocId && window.loadDocumentById) {
    const docInput = qs("chunkDocId");
    if (docInput) docInput.value = primaryDocId;
    window.loadDocumentById(primaryDocId);
    switchTab("chunking");
  }
}

function readFileText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result?.toString() || "");
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

function renderDocFindings() {
  const list = qs("docResults");
  if (!list) return;

  if (!docFindings.length) {
    list.innerHTML = `<div class="muted">No findings yet. Use the demo button to populate mock results.</div>`;
    return;
  }

  list.innerHTML = docFindings
    .map((finding) => {
      const chips = toList(finding.tags || []).map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join(" ");
      const stateClass = finding.status === "accepted" ? "is-accepted" : finding.status === "rejected" ? "is-rejected" : "";
      const stateLabel =
        finding.status === "accepted"
          ? `<span class="badge success">Accepted</span>`
          : finding.status === "rejected"
            ? `<span class="badge danger">Rejected</span>`
            : `<span class="badge">Pending</span>`;

      return `
        <div class="card ${stateClass}">
          <div class="card-header">
            <div>
              <div class="small-label">Suggested lore</div>
              <div style="font-weight:700;">${escapeHtml(finding.title)}</div>
              <div class="pill-row" style="margin-top:6px;">${chips}</div>
            </div>
            <div class="card-actions">
              <button class="ghost" onclick="markFinding(${JSON.stringify(finding.id)}, 'accepted')">Accept</button>
              <button class="secondary" onclick="markFinding(${JSON.stringify(finding.id)}, 'rejected')">Reject</button>
            </div>
          </div>
          <p class="mini-text">${escapeHtml(finding.excerpt)}</p>
          <div style="margin-top:8px;">${stateLabel}</div>
        </div>
      `;
    })
    .join("");
}

function markFinding(id, status) {
  const idx = docFindings.findIndex((f) => f.id === id);
  if (idx === -1) return;
  docFindings[idx].status = status;
  renderDocFindings();
}

async function loadDocLibrary() {
  const limit = Number(val("docLibraryLimit") || "50") || 50;
  const container = qs("docLibrary");
  if (container) container.innerHTML = `<div class="muted">Loading documents...</div>`;
  let res;
  try {
    res = await fetch(`/api/chunking/documents?limit=${encodeURIComponent(limit)}`);
  } catch (e) {
    if (container) container.innerHTML = `<div class="muted">Network error loading documents.</div>`;
    return;
  }
  const data = await res.json().catch(() => []);
  if (!res.ok) {
    if (container) container.innerHTML = `<div class="muted">Failed to load documents.</div>`;
    return;
  }
  docLibrary = Array.isArray(data) ? data : [];
  renderDocLibrary();
}

function renderDocLibrary() {
  const container = qs("docLibrary");
  if (!container) return;
  if (!docLibrary.length) {
    container.innerHTML = `<div class="muted">No stored documents yet. Upload or paste a draft to get started.</div>`;
    return;
  }
  container.innerHTML = docLibrary
    .map((doc) => {
      const badge = doc.finalized ? `<span class="badge success">Finalized</span>` : `<span class="badge">Draft</span>`;
      const subtitleParts = [
        `v${doc.version || 1}`,
        `${doc.chunk_count || 0} chunk(s)`,
        `${doc.text_length || 0} chars`,
      ];
      if (doc.updated_at) subtitleParts.push(`updated ${formatTimestamp(doc.updated_at)}`);
      const sourceLabel = doc.filename || doc.url;
      return `
        <div class="card">
          <div class="row space" style="align-items:baseline;">
            <div>
              <div class="chunk-title">${escapeHtml(doc.doc_id || "")}</div>
              <div class="mini-text">${subtitleParts.join(" · ")}</div>
              ${sourceLabel ? `<div class="mini-text muted">${escapeHtml(sourceLabel)}</div>` : ""}
            </div>
            <div class="row" style="gap:8px; align-items:center;">
              ${badge}
              <button class="ghost js-doc-load" data-doc-id="${escapeHtml(doc.doc_id || "")}">Load</button>
            </div>
          </div>
        </div>
      `;
    })
    .join("");
}

// ---------------- Tabs & Modal ----------------
function switchTab(name) {
  document.querySelectorAll(".tab").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach(panel => {
    panel.style.display = panel.dataset.panel === name ? "block" : "none";
  });
}

function openCardModal() {
  const modal = qs("cardModal");
  if (!modal) return;
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
}

function closeCardModal() {
  const modal = qs("cardModal");
  if (!modal) return;
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
}

function handleEditClick(event) {
  const btn = event.target.closest(".js-edit-card");
  if (!btn) return;
  try {
    const metaRaw = btn.dataset.meta ? decodeURIComponent(btn.dataset.meta) : "{}";
    fillFormFromCard(btn.dataset.id || "", btn.dataset.text || "", metaRaw);
  } catch (e) {
    console.error("Failed to edit card", e);
  }
}

function handleConnectionAction(event) {
  const deleteBtn = event.target.closest(".js-delete-connection");
  const editBtn = event.target.closest(".js-edit-connection");

  if (deleteBtn?.dataset?.edgeId) {
    const edgeId = decodeURIComponent(deleteBtn.dataset.edgeId);
    deleteConnectionById(edgeId);
    return;
  }

  if (editBtn?.dataset?.edge) {
    const decoded = decodeURIComponent(editBtn.dataset.edge);
    const edge = safeJsonParse(decoded, {});
    fillEdgeForm(edge.edge_id || "", edge.src_id || "", edge.dst_id || "", edge.rel_type || "", edge.tags || [], edge.note || "");
    return;
  }
}

// ---------------- Init ----------------
document.addEventListener("DOMContentLoaded", () => {
  if (qs("connectionsList")) {
    loadConnections().catch(() => {});
  }
  if (qs("docResults")) {
    renderDocFindings();
    loadDocLibrary().catch(() => {});
  }
  if (window.collectionName && qs("cardsList")) {
    loadChunks(window.collectionName).catch(() => {});
  }
  document.addEventListener("click", handleEditClick);
  document.addEventListener("click", handleConnectionAction);
  const analyzeBtn = qs("analyzeDocBtn");
  if (analyzeBtn) {
    analyzeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      analyzeDocument();
    });
  }
  const chunkUploadBtn = qs("chunkUploadDetectBtn");
  if (chunkUploadBtn) {
    chunkUploadBtn.addEventListener("click", (e) => {
      e.preventDefault();
      uploadDocsForChunking();
    });
  }
  const docRefresh = qs("docLibraryRefreshBtn");
  if (docRefresh) {
    docRefresh.addEventListener("click", (e) => {
      e.preventDefault();
      loadDocLibrary();
    });
  }
  document.addEventListener("click", (e) => {
    const loadBtn = e.target.closest(".js-doc-load");
    if (loadBtn?.dataset?.docId && window.loadDocumentById) {
      const docId = loadBtn.dataset.docId;
      const target = qs("chunkDocId");
      if (target) target.value = docId;
      window.loadDocumentById(docId);
      switchTab("chunking");
    }
  });
});

// ---------------- Back-compat aliases ----------------
function upsertDoc(collection) { return upsertChunk(collection); }
function queryDocs(collection) { return queryChunks(collection); }
function loadDocs(collection) { return loadChunks(collection); }

// expose for inline handlers
window.createCollection = createCollection;
window.upsertChunk = upsertChunk;
window.queryChunks = queryChunks;
window.loadChunks = loadChunks;
window.saveConnection = saveConnection;
window.deleteConnection = deleteConnection;
window.loadConnections = loadConnections;
window.fillEdgeForm = fillEdgeForm;
window.deleteConnectionById = deleteConnectionById;
window.analyzeDocument = analyzeDocument;
window.uploadDocsForChunking = uploadDocsForChunking;
window.markFinding = markFinding;
window.switchTab = switchTab;
window.openCardModal = openCardModal;
window.closeCardModal = closeCardModal;
window.upsertDoc = upsertDoc;
window.queryDocs = queryDocs;
window.loadDocs = loadDocs;
window.loadDocLibrary = loadDocLibrary;
window.deleteChunk = deleteChunk;
window.fillFormFromCard = fillFormFromCard;

function logDoc(message) {
  const el = qs("docLog");
  if (!el) return;
  const ts = new Date().toLocaleTimeString();
  el.textContent = `[${ts}] ${message}\n` + el.textContent;
}
