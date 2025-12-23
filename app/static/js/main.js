import {
  qs, val, splitCsv, parseOptionalInt, parseJsonObject, toList, escapeHtml
} from "./helpers.js";
import { cardTemplate } from "./cards.js";

const docFindings = [];

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
    return `
      <div class="card">
        <div class="card-header">
          <div>
            <div class="small-label">Edge</div>
            <div style="font-weight:700;">${escapeHtml(edge.edge_id)}</div>
          </div>
          <div class="card-actions">
            <button class="ghost" onclick="fillEdgeForm(${JSON.stringify(edge.edge_id)}, ${JSON.stringify(edge.src_id)}, ${JSON.stringify(edge.dst_id)}, ${JSON.stringify(edge.rel_type)}, ${JSON.stringify(edge.tags || [])}, ${JSON.stringify(edge.note || "")})">Edit</button>
            <button class="danger" onclick="deleteConnectionById(${JSON.stringify(edge.edge_id)})">Delete</button>
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
    const meta = typeof metadataJson === "string" ? JSON.parse(metadataJson) : metadataJson || {};
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

// ---------------- Init ----------------
document.addEventListener("DOMContentLoaded", () => {
  if (qs("connectionsList")) {
    loadConnections().catch(() => {});
  }
  if (qs("docResults")) {
    renderDocFindings();
  }
  if (window.collectionName && qs("cardsList")) {
    loadChunks(window.collectionName).catch(() => {});
  }
  document.addEventListener("click", handleEditClick);
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
window.mockAnalyzeDocument = mockAnalyzeDocument;
window.markFinding = markFinding;
window.switchTab = switchTab;
window.openCardModal = openCardModal;
window.closeCardModal = closeCardModal;
window.upsertDoc = upsertDoc;
window.queryDocs = queryDocs;
window.loadDocs = loadDocs;
window.deleteChunk = deleteChunk;
window.fillFormFromCard = fillFormFromCard;
