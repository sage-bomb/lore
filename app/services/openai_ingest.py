import json
from typing import Any, Dict, List, Tuple

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


def normalize_chunks(chunks: List[Dict[str, Any]], collection_name: str) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    col = get_collection(collection_name)
    ids: List[str] = []
    texts: List[str] = []
    metas: List[Dict[str, Any]] = []

    for ch in chunks or []:
        cid = ch.get("chunk_id")
        text = ch.get("text")
        if not cid or not text:
            continue

        existing = col.get(ids=[cid])
        existing_ids = existing.get("ids") or []
        if existing_ids:
            continue

        md = {
            "chunk_kind": ch.get("chunk_kind") or "thing_summary",
            "thing_id": ch.get("thing_id"),
            "thing_type": ch.get("thing_type"),
            "edge_id": ch.get("edge_id"),
            "tags": ch.get("tags") or [],
        }
        ids.append(cid)
        texts.append(text)
        metas.append(md)

    return ids, texts, metas


def ingest_lore_from_text(text: str, collection: str, notes: str | None = None) -> Dict[str, Any]:
    if not text or not text.strip():
        raise ValueError("text must be provided")

    safe_collection = normalize_collection_name(collection)
    extracted = call_openai(text, notes)

    # Things
    new_things = dedupe_things(extracted.get("things") or [])
    for t in new_things:
        upsert_thing(Thing.model_validate(t))

    # Connections
    new_conns = dedupe_connections(extracted.get("connections") or [])
    for c in new_conns:
        upsert_connection(Connection.model_validate(c))

    # Chunks
    ids, texts, metas = normalize_chunks(extracted.get("chunks") or [], safe_collection)
    if ids:
        col = get_collection(safe_collection)
        col.upsert(ids=ids, documents=texts, metadatas=metas)

    return {
        "counts": {
            "things": len(new_things),
            "connections": len(new_conns),
            "chunks": len(ids),
        },
        "things": new_things,
        "connections": new_conns,
        "chunks": extracted.get("chunks") or [],
    }
