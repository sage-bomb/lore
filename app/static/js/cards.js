import { escapeHtml, toList } from "./helpers.js";

export function renderMeta(meta) {
  if (!meta) return "";
  const rows = [];
  if (meta.thing_type) rows.push(`<div class="badge">${escapeHtml(meta.thing_type)}</div>`);

  const tags = toList(meta.tags);
  if (tags.length) rows.push(`<div class="mini-text">Tags: ${tags.map(t => `<span class="chip">${escapeHtml(t)}</span>`).join(" ")}</div>`);

  const entities = toList(meta.entity_ids);
  if (entities.length) rows.push(`<div class="mini-text">Entities: ${entities.map(t => `<span class="chip">${escapeHtml(t)}</span>`).join(" ")}</div>`);

  if (meta.source_file || meta.source_section) {
    rows.push(`<div class="mini-text">Source: ${escapeHtml(meta.source_file || "")}${meta.source_section ? ` · ${escapeHtml(meta.source_section)}` : ""}</div>`);
  }

  const extras = Object.entries(meta)
    .filter(([k]) => k.startsWith("extra."))
    .map(([k, v]) => `<div class="mini-text">${escapeHtml(k.replace("extra.", ""))}: ${escapeHtml(v)}</div>`)
    .join("");

  return `<div class="stack">${rows.join("")}${extras}</div>`;
}

export function cardTemplate(item, opts = {}) {
  const meta = item.metadata || {};
  const chipKind = meta.chunk_kind ? `<span class="chip">${escapeHtml(meta.chunk_kind)}</span>` : "";
  const chipThing = meta.thing_id ? `<span class="chip">${escapeHtml(meta.thing_id)}</span>` : "";
  const distance = opts.distance === null || opts.distance === undefined ? "" : `<div class="muted">distance: ${opts.distance.toFixed(4)}</div>`;
  const txt = item.text ? `<p class="mini-text">${escapeHtml(item.text)}</p>` : "";
  const md = meta ? renderMeta(meta) : "";
  const metaEncoded = encodeURIComponent(JSON.stringify(meta));

  return `
    <div class="card">
      <div class="card-header">
        <div>
          <div class="small-label">Card ID</div>
          <div style="font-weight:700;">${escapeHtml(item.id)}</div>
          <div class="pill-row" style="margin-top:6px; gap:6px;">${chipKind} ${chipThing}</div>
          ${distance}
        </div>
        <div class="card-actions">
          ${opts.hideActions ? "" : `
            <button class="ghost js-edit-card"
              data-id="${escapeHtml(item.id)}"
              data-text="${escapeHtml(item.text || "")}"
              data-meta="${metaEncoded}">Edit</button>
            ${opts.collection ? `<button class="danger" onclick="deleteChunk('${opts.collection}', ${JSON.stringify(item.id)})">Delete</button>` : ""}
          `}
        </div>
      </div>
      ${txt}
      ${md}
    </div>
  `;
}
