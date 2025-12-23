// Updated front-end helpers for chunk-based API.
// Drop this in your /static/app.js (or copy pieces into your existing file).

function qs(id) { return document.getElementById(id); }
function val(id) { const el = qs(id); return el ? el.value : ""; }
function escapeJs(v) { return JSON.stringify(v ?? ""); }
const docFindings = [];

function toList(val) {
  if (Array.isArray(val)) return val;
  if (typeof val === "string" && val.trim()) {
    return val.split(",").map(s => s.trim()).filter(Boolean);
  }
  return [];
}

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
    msg.textContent = `Extra JSON error: ${e.message}`;
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

  results.innerHTML = hits.map(h => {
    const md = h.metadata ? renderMeta(h.metadata) : "";
    const txt = h.text ? `<p class="mini-text">${escapeHtml(h.text)}</p>` : "";
    const dist = (h.distance === null || h.distance === undefined) ? "" : `<div class="muted">distance: ${h.distance.toFixed(4)}</div>`;
    const chipKind = h.metadata?.chunk_kind ? `<span class="chip">${escapeHtml(h.metadata.chunk_kind)}</span>` : "";
    const chipThing = h.metadata?.thing_id ? `<span class="chip">${escapeHtml(h.metadata.thing_id)}</span>` : "";
    return `
      <div class="card">
        <div class="card-header">
          <div>
            <div class="small-label">Card ID</div>
            <div style="font-weight:700;">${escapeHtml(h.id)}</div>
            <div class="pill-row" style="margin-top:6px; gap:6px;">${chipKind} ${chipThing}</div>
            ${dist}
          </div>
          <div class="card-actions">
            <button class="ghost" onclick="fillFormFromCard(${escapeJs(h.id)}, ${escapeJs(h.text || "")}, ${escapeJs(h.metadata || {})})">Edit</button>
          </div>
        </div>
        ${txt}
        ${md}
      </div>
    `;
  }).join("");
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
    msg.textContent = data.detail || "Error loading chunks";
    return;
  }

  if (msg) msg.textContent = `Loaded ${data.count} chunk(s).`;

  const items = data.items || [];
  if (!items.length) {
    docsEl.innerHTML = `<div class="muted">No chunks yet.</div>`;
    return;
  }

  docsEl.innerHTML = items.map(it => {
    const md = it.metadata ? renderMeta(it.metadata) : "";
    const txt = it.text ? `<p class="mini-text">${escapeHtml(it.text)}</p>` : "";
    const chipKind = it.metadata?.chunk_kind ? `<span class="chip">${escapeHtml(it.metadata.chunk_kind)}</span>` : "";
    const chipThing = it.metadata?.thing_id ? `<span class="chip">${escapeHtml(it.metadata.thing_id)}</span>` : "";
    return `
      <div class="card">
        <div class="card-header">
          <div>
            <div class="small-label">Card ID</div>
            <div style="font-weight:700;">${escapeHtml(it.id)}</div>
            <div class="pill-row" style="margin-top:6px; gap:6px;">${chipKind} ${chipThing}</div>
          </div>
          <div class="card-actions">
            <button class="ghost" onclick="fillFormFromCard(${escapeJs(it.id)}, ${escapeJs(it.text || "")}, ${escapeJs(it.metadata || {})})">Edit</button>
            <button class="danger" onclick="deleteChunk('${collection}', ${escapeJs(it.id)})">Delete</button>
          </div>
        </div>
        ${txt}
        ${md}
      </div>
    `;
  }).join("");
}

// -- Connections UI helpers --
async function saveConnection() {
  const msg = qs("connectionsMsg");
  msg.textContent = "";

  const edge_id = val("edgeFormId").trim();
  const src_id = val("edgeSrc").trim();
  const dst_id = val("edgeDst").trim();
  const rel_type = val("edgeRelType").trim();

  if (!edge_id || !src_id || !dst_id || !rel_type) {
    msg.textContent = "Edge id, source, destination, and type are required.";
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
  msg.textContent = res.ok ? `Saved ${data.edge_id}` : (data.detail || "Error saving connection");
  if (res.ok) {
    loadConnections().catch(() => {});
  }
}

async function deleteConnection() {
  const msg = qs("connectionsMsg");
  msg.textContent = "";
  const edge_id = val("edgeFormId").trim();
  if (!edge_id) {
    msg.textContent = "Enter an edge id to delete.";
    return;
  }

  const res = await fetch(`/api/connections/${encodeURIComponent(edge_id)}`, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  msg.textContent = res.ok ? `Deleted ${edge_id}` : (data.detail || "Error deleting connection");
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
    const tags = (edge.tags || []).map(t => `<span class="chip">${escapeHtml(t)}</span>`).join(" ");
    return `
      <div class="card">
        <div class="card-header">
          <div>
            <div class="small-label">Edge</div>
            <div style="font-weight:700;">${escapeHtml(edge.edge_id)}</div>
          </div>
          <div class="card-actions">
            <button class="ghost" onclick="fillEdgeForm(${escapeJs(edge.edge_id)}, ${escapeJs(edge.src_id)}, ${escapeJs(edge.dst_id)}, ${escapeJs(edge.rel_type)}, ${escapeJs(edge.tags || [])}, ${escapeJs(edge.note || "")})">Edit</button>
            <button class="danger" onclick="deleteConnectionById(${escapeJs(edge.edge_id)})">Delete</button>
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
  msg.textContent = "";
  const res = await fetch(`/api/connections/${encodeURIComponent(edgeId)}`, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  msg.textContent = res.ok ? `Deleted ${edgeId}` : (data.detail || "Error deleting connection");
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
    const meta = typeof metadataJson === "string" ? JSON.parse(metadataJson) : metadataJson || {};
    qs("chunkId").value = chunkId || "";
    qs("chunkKind").value = meta.chunk_kind || "thing_summary";
    qs("thingId").value = meta.thing_id || "";
    qs("thingType").value = meta.thing_type || "";
    qs("edgeId").value = meta.edge_id || "";
    qs("chunkText").value = text || "";
    qs("tags").value = (meta.tags || []).join(", ");
    qs("entityIds").value = (meta.entity_ids || []).join(", ");
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
  } catch (e) {
    console.error("Failed to fill form", e);
  }
}

async function deleteChunk(collection, chunkId) {
  const msg = qs("upsertMsg");
  msg.textContent = "";
  const res = await fetch(`/api/collections/${encodeURIComponent(collection)}/chunks/${encodeURIComponent(chunkId)}`, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  msg.textContent = res.ok ? `Deleted ${chunkId}` : (data.detail || "Error deleting chunk");
  if (res.ok) loadChunks(collection).catch(() => {});
}

function renderMeta(meta) {
  if (!meta) return "";
  const rows = [];
  if (meta.thing_type) rows.push(`<div class="badge">${escapeHtml(meta.thing_type)}</div>`);
  const tags = toList(meta.tags);
  if (tags.length) rows.push(`<div class="mini-text">Tags: ${tags.map(t => `<span class="chip">${escapeHtml(t)}</span>`).join(" ")}</div>`);
  const entities = toList(meta.entity_ids);
  if (entities.length) rows.push(`<div class="mini-text">Entities: ${entities.map(t => `<span class="chip">${escapeHtml(t)}</span>`).join(" ")}</div>`);
  if (meta.source_file || meta.source_section) rows.push(`<div class="mini-text">Source: ${escapeHtml(meta.source_file || "")}${meta.source_section ? ` · ${escapeHtml(meta.source_section)}` : ""}</div>`);
  const extras = Object.entries(meta)
    .filter(([k]) => k.startsWith("extra."))
    .map(([k, v]) => `<div class="mini-text">${escapeHtml(k.replace("extra.", ""))}: ${escapeHtml(v)}</div>`)
    .join("");
  return `<div class="stack">${rows.join("")}${extras}</div>`;
}

document.addEventListener("DOMContentLoaded", () => {
  if (qs("connectionsList")) {
    loadConnections().catch(() => {});
  }
  if (qs("docResults")) {
    renderDocFindings();
  }
  if (window.collectionName && qs("docs")) {
    loadChunks(window.collectionName).catch(() => {});
  }
});

// ---------------- Document scout (UI-only) ----------------
function mockAnalyzeDocument() {
  const status = qs("docStatus");
  const fileInput = qs("docFile");
  const basis =
    (fileInput && fileInput.files && fileInput.files[0] && fileInput.files[0].name) ||
    val("docUrl").trim() ||
    "sample_chapter.txt";

  const notes = val("docNotes").trim() || "characters, places, rules";

  docFindings.length = 0;
  docFindings.push(
    {
      id: "finding.character",
      title: "Character: Sahla Nareth",
      excerpt: "Exiled navigator who hears tidesong magic and charts storm routes.",
      tags: ["character", "magic"],
      status: "pending",
    },
    {
      id: "finding.place",
      title: "Place: Kaar Archipelago",
      excerpt: "Storm-linked islands where bridges appear and vanish with the tides.",
      tags: ["place", "storm"],
      status: "pending",
    },
    {
      id: "finding.rule",
      title: "Rule: Tidesong Navigation",
      excerpt: "Pilots can follow harmonic tides to slip between islands faster than windships.",
      tags: ["rule", "travel"],
      status: "pending",
    }
  );

  renderDocFindings();
  if (status) {
    status.textContent = `Mocked analysis for "${basis}" with instructions: ${notes}`;
  }
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
      const chips = (finding.tags || []).map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join(" ");
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
              <button class="ghost" onclick="markFinding(${escapeJs(finding.id)}, 'accepted')">Accept</button>
              <button class="secondary" onclick="markFinding(${escapeJs(finding.id)}, 'rejected')">Reject</button>
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

// ---------------- Tabs ----------------
function switchTab(name) {
  document.querySelectorAll(".tab").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach(panel => {
    panel.style.display = panel.dataset.panel === name ? "block" : "none";
  });
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
