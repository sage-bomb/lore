export function qs(id) { return document.getElementById(id); }
export function val(id) { const el = qs(id); return el ? el.value : ""; }
export function escapeJs(v) { return JSON.stringify(v ?? ""); }

export function splitCsv(s) {
  return (s || "")
    .split(",")
    .map(x => x.trim())
    .filter(Boolean);
}

export function parseOptionalInt(s) {
  if (s === null || s === undefined || s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

export function parseJsonObject(s) {
  const t = (s || "").trim();
  if (!t) return null;
  const obj = JSON.parse(t);
  if (obj && typeof obj === "object" && !Array.isArray(obj)) return obj;
  throw new Error("extra_json must be a JSON object");
}

export function toList(val) {
  if (Array.isArray(val)) return val;
  if (typeof val === "string" && val.trim()) {
    return val.split(",").map(s => s.trim()).filter(Boolean);
  }
  return [];
}

export function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
