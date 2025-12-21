// Updated front-end helpers for chunk-based API.
// Drop this in your /static/app.js (or copy pieces into your existing file).

function qs(id) { return document.getElementById(id); }
function val(id) { const el = qs(id); return el ? el.value : ""; }

function splitCsv(s) {
  return (s || "")
    .split(",")
    .map(x => x.trim())
    .filter(Boolean);
}

function parseOptionalInt(s) {
  if (s === null || s === undefined || s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

function parseJsonObject(s) {
  const t = (s || "").trim();
  if (!t) return null;
  const obj = JSON.parse(t);
  if (obj && typeof obj === "object" && !Array.isArray(obj)) return obj;
  throw new Error("extra_json must be a JSON object");
}

// ---------------- Collections ----------------

async function createCollection() {
  const name = val("newCollectionName").trim();
  const msg = qs("createCollectionMsg");
  msg.textContent = "";

  if (!name) {
    msg.textContent = "Please enter a collection name.";
    return;
  }

  const res = await fetch("/api/collections", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ name })
  });

  const data = await res.json().catch(() => ({}));
  msg.textContent = res.ok ? `Created: ${data.name}` : (data.detail || "Error creating collection");
  if (res.ok) window.location.reload();
}

// ---------------- Chunks ----------------

async function upsertChunk(collection) {
  const msg = qs("upsertMsg");
  msg.textContent = "";

  const chunk_id = val("chunkId").trim();
  const text = val("chunkText").trim();

  if (!chunk_id || !text) {
    msg.textContent = "chunk_id and text are required.";
    return;
  }

  const payload = {
    chunks: [{
      chunk_id,
      text,
      doc_kind: val("docKind") || "record_chunk",
      canon_status: val("canonStatus") || "draft",
      record_type: val("recordType").trim(),
      record_id: val("recordId").trim(),
      source_file: val("sourceFile").trim() || null,
      source_section: val("sourceSection").trim() || null,
      chapter_number: parseOptionalInt(val("chapterNumber")),
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
    msg.textContent = `Extra JSON error: ${e.message}`;
    return;
  }

  // record_type/record_id are required by schema
  if (!payload.chunks[0].record_type || !payload.chunks[0].record_id) {
    msg.textContent = "record_type and record_id are required (e.g. character + character.sahla).";
    return;
  }

  const res = await fetch(`/api/collections/${encodeURIComponent(collection)}/chunks`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  });

  const data = await res.json().catch(() => ({}));
  msg.textContent = res.ok ? `Upserted ${data.upserted} chunk(s).` : (data.detail || "Error upserting chunk");
  if (res.ok) {
    // refresh browse view for convenience
    loadChunks(collection).catch(() => {});
  }
}

async function queryChunks(collection) {
  const results = qs("results");
  results.innerHTML = "";

  const selectedKinds = Array.from(qs("queryDocKind")?.selectedOptions || []).map(o => o.value);
  const payload = {
    query_text: val("queryText").trim(),
    n_results: parseOptionalInt(val("topK")) || 8,
    doc_kinds: selectedKinds.length ? selectedKinds : null,
    canon_only: !!qs("canonOnly")?.checked
  };

  if (!payload.query_text) {
    results.innerHTML = `<div class="muted">Enter a query.</div>`;
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
    return;
  }

  if (!hits.length) {
    results.innerHTML = `<div class="muted">No results.</div>`;
    return;
  }

  results.innerHTML = hits.map(h => {
    const md = h.metadata ? `<pre class="pre">${escapeHtml(JSON.stringify(h.metadata, null, 2))}</pre>` : "";
    const txt = h.text ? `<pre class="pre">${escapeHtml(h.text)}</pre>` : "";
    const dist = (h.distance === null || h.distance === undefined) ? "" : `<div class="muted">distance: ${h.distance.toFixed(4)}</div>`;
    return `
      <div class="card">
        <div><strong>${escapeHtml(h.id)}</strong></div>
        ${dist}
        ${txt}
        ${md}
      </div>
    `;
  }).join("");
}

async function loadChunks(collection) {
  const docsEl = qs("docs");
  const msg = qs("browseMsg");
  msg.textContent = "";
  docsEl.innerHTML = "";

  const limit = parseOptionalInt(val("browseLimit")) || 25;

  const res = await fetch(`/api/collections/${encodeURIComponent(collection)}/chunks?limit=${limit}`);
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    msg.textContent = data.detail || "Error loading chunks";
    return;
  }

  msg.textContent = `Loaded ${data.count} chunk(s).`;

  const items = data.items || [];
  if (!items.length) {
    docsEl.innerHTML = `<div class="muted">No chunks yet.</div>`;
    return;
  }

  docsEl.innerHTML = items.map(it => {
    const md = it.metadata ? `<pre class="pre">${escapeHtml(JSON.stringify(it.metadata, null, 2))}</pre>` : "";
    const txt = it.text ? `<pre class="pre">${escapeHtml(it.text)}</pre>` : "";
    return `
      <div class="card">
        <div><strong>${escapeHtml(it.id)}</strong></div>
        ${txt}
        ${md}
      </div>
    `;
  }).join("");
}

// ---------------- Back-compat aliases ----------------
function upsertDoc(collection) { return upsertChunk(collection); }
function queryDocs(collection) { return queryChunks(collection); }
function loadDocs(collection) { return loadChunks(collection); }

// ---------------- Small utility ----------------
function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
