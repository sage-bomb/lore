import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from app.chroma_store import get_collection, normalize_collection_name
from app.library_store import list_connections, list_things, upsert_connection, upsert_thing
from app.schemas import Connection, Thing

try:
    from openai import OpenAI
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("openai package is required for ingestion") from exc


def build_prompt(doc_text: str, notes: str | None = None) -> list[dict[str, str]]:
    system = (
        "You extract structured lore data from a document. "
        "Return JSON only with keys: things, connections, chunks. "
        "Each thing must include: thing_id (slug-like), thing_type, name, summary (1-2 sentences), tags. "
        "Each connection must include: edge_id, src_id, dst_id, rel_type, note, tags. "
        "Each chunk must include: chunk_id, text, chunk_kind (e.g., thing_summary, connection_note), "
        "thing_id, thing_type, edge_id (optional), tags. "
        "Prefer concise IDs and avoid duplicates. "
        "Avoid redundant entries; merge duplicates when possible."
    )
    if notes:
        system += f" Follow these notes: {notes}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Document:\n{doc_text}"},
    ]


def call_openai(doc_text: str, notes: str | None = None) -> Dict[str, Any]:
    client = OpenAI()
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=build_prompt(doc_text, notes),
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from OpenAI")
    return data


def dedupe_things(things: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    existing_ids = {t.thing_id for t in list_things()}
    unique: Dict[str, Dict[str, Any]] = {}
    for t in things or []:
        tid = t.get("thing_id")
        if not tid or tid in existing_ids:
            continue
        unique[tid] = t
    return list(unique.values())


def dedupe_connections(conns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    existing_ids = {c.edge_id for c in list_connections()}
    unique: Dict[str, Dict[str, Any]] = {}
    for c in conns or []:
        cid = c.get("edge_id")
        if not cid or cid in existing_ids:
            continue
        unique[cid] = c
    return list(unique.values())


def _resolve_doc_id(text: str, url: Optional[str], provided: Optional[str]) -> str:
    if provided:
        return provided
    if url:
        digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
        return f"url_{digest}"
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
    return f"doc_{digest}"


def _attach_chunk_defaults(
    chunk: Dict[str, Any],
    doc_id: str,
    source_file: Optional[str],
    source_section: Optional[str],
    url: Optional[str],
) -> Dict[str, Any]:
    ch = dict(chunk)
    ch.setdefault("doc_id", doc_id)
    ch.setdefault("doc_url", url)
    ch.setdefault("source_file", source_file)
    ch.setdefault("source_section", source_section)
    return ch


def _saved_chunks_for_doc(collection_name: str, doc_id: str) -> List[Dict[str, Any]]:
    col = get_collection(collection_name)
    got = col.get(where={"doc_id": doc_id}, include=["documents", "metadatas", "ids"], limit=500)
    ids = got.get("ids") or []
    docs = got.get("documents") or []
    metas = got.get("metadatas") or []

    restored: List[Dict[str, Any]] = []
    for idx, cid in enumerate(ids):
        meta = metas[idx] if idx < len(metas) else {}
        restored.append({
            "chunk_id": cid,
            "text": docs[idx] if idx < len(docs) else "",
            "chunk_kind": (meta or {}).get("chunk_kind"),
            "thing_id": (meta or {}).get("thing_id"),
            "thing_type": (meta or {}).get("thing_type"),
            "edge_id": (meta or {}).get("edge_id"),
            "entity_ids": (meta or {}).get("entity_ids") or [],
            "tags": (meta or {}).get("tags") or [],
            "doc_id": (meta or {}).get("doc_id") or doc_id,
            "doc_url": (meta or {}).get("doc_url"),
            "source_file": (meta or {}).get("source_file"),
            "source_section": (meta or {}).get("source_section"),
        })
    return restored


def normalize_chunks(
    chunks: List[Dict[str, Any]],
    collection_name: str,
    doc_id: str,
    source_file: Optional[str],
    source_section: Optional[str],
    url: Optional[str],
) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    ids: List[str] = []
    texts: List[str] = []
    metas: List[Dict[str, Any]] = []

    for ch in chunks or []:
        cid = ch.get("chunk_id")
        text = ch.get("text")
        if not cid or not text:
            continue

        md = {
            "doc_id": ch.get("doc_id") or doc_id,
            "doc_url": ch.get("doc_url") or url,
            "chunk_kind": ch.get("chunk_kind") or "thing_summary",
            "thing_id": ch.get("thing_id"),
            "thing_type": ch.get("thing_type"),
            "edge_id": ch.get("edge_id"),
            "tags": ch.get("tags") or [],
            "entity_ids": ch.get("entity_ids") or [],
            "source_file": ch.get("source_file") or source_file,
            "source_section": ch.get("source_section") or source_section,
        }
        ids.append(cid)
        texts.append(text)
        metas.append({k: v for k, v in md.items() if v is not None})

    return ids, texts, metas


def ingest_lore_from_text(
    text: str,
    collection: str,
    notes: str | None = None,
    *,
    doc_id: Optional[str] = None,
    source_file: Optional[str] = None,
    source_section: Optional[str] = None,
    url: Optional[str] = None,
    persist_chunks: bool = False,
    reuse_saved: bool = True,
) -> Dict[str, Any]:
    if not text or not text.strip():
        raise ValueError("text must be provided")

    safe_collection = normalize_collection_name(collection)
    resolved_doc_id = _resolve_doc_id(text, url, doc_id)

    saved_chunks: List[Dict[str, Any]] = []
    if reuse_saved:
        saved_chunks = _saved_chunks_for_doc(safe_collection, resolved_doc_id)

    extracted = call_openai(text, notes) if not saved_chunks else {}

    # Things
    new_things = dedupe_things(extracted.get("things") or [])
    for t in new_things:
        upsert_thing(Thing.model_validate(t))

    # Connections
    new_conns = dedupe_connections(extracted.get("connections") or [])
    for c in new_conns:
        upsert_connection(Connection.model_validate(c))

    # Chunks
    incoming_chunks = saved_chunks or (extracted.get("chunks") or [])
    augmented_chunks = [
        _attach_chunk_defaults(ch, resolved_doc_id, source_file, source_section, url)
        for ch in incoming_chunks
    ]
    ids: List[str] = []
    if persist_chunks and augmented_chunks:
        ids, texts, metas = normalize_chunks(
            augmented_chunks, safe_collection, resolved_doc_id, source_file, source_section, url
        )
        if ids:
            col = get_collection(safe_collection)
            col.upsert(ids=ids, documents=texts, metadatas=metas)

    return {
        "counts": {
            "things": len(new_things),
            "connections": len(new_conns),
            "chunks_detected": len(augmented_chunks),
            "chunks_embedded": len(ids),
            "chunks": len(ids),
        },
        "things": new_things,
        "connections": new_conns,
        "chunks": augmented_chunks,
        "doc_id": resolved_doc_id,
        "used_saved_chunks": bool(saved_chunks),
    }


def finalize_chunks_for_doc(
    collection: str,
    chunks: List[Dict[str, Any]],
    *,
    doc_id: str,
    source_file: Optional[str] = None,
    source_section: Optional[str] = None,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    if not doc_id:
        raise ValueError("doc_id is required to finalize chunks")
    safe_collection = normalize_collection_name(collection)

    normalized_chunks = [
        _attach_chunk_defaults(ch, doc_id, source_file, source_section, url)
        for ch in chunks or []
    ]
    ids, texts, metas = normalize_chunks(
        normalized_chunks, safe_collection, doc_id, source_file, source_section, url
    )
    upserted = 0
    if ids:
        col = get_collection(safe_collection)
        col.upsert(ids=ids, documents=texts, metadatas=metas)
        upserted = len(ids)

    return {
        "collection": safe_collection,
        "doc_id": doc_id,
        "upserted": upserted,
    }
