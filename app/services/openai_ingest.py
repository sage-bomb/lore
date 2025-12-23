import json
import logging
import os
from typing import Any, Dict, List, Tuple

from app.chroma_store import get_collection, normalize_collection_name, sanitize_metadata
from app.library_store import list_connections, list_things, upsert_connection, upsert_thing
from app.schemas import Connection, KNOWN_THING_TYPES, Thing

try:
    from openai import OpenAI
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("openai package is required for ingestion") from exc


logger = logging.getLogger(__name__)
ALLOWED_THING_TYPES = set(KNOWN_THING_TYPES)


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


def normalize_thing_type(value: Any) -> str:
    if not value:
        return "other"
    normalized = str(value).lower().strip()
    if normalized in ALLOWED_THING_TYPES:
        return normalized
    logger.info("OpenAI ingest: new thing_type '%s' accepted", normalized)
    return normalized or "other"


def call_openai(doc_text: str, notes: str | None = None) -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    logger.info(
        "OpenAI ingest: preparing request (model=%s, text_len=%d, notes_len=%d, api_key_present=%s)",
        "gpt-4o-mini",
        len(doc_text or ""),
        len(notes or ""),
        bool(api_key),
    )
    if api_key:
        logger.debug("OpenAI ingest: using API key prefix %s", api_key[:6])
    else:
        logger.warning("OpenAI ingest: OPENAI_API_KEY is not set; request will likely fail")

    client = OpenAI()
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=build_prompt(doc_text, notes),
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    except Exception:
        logger.exception("OpenAI ingest: request to OpenAI failed")
        raise

    logger.info("OpenAI ingest: received response with %d choice(s)", len(resp.choices))

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
    logger.info(
        "OpenAI ingest: deduped things (incoming=%d, kept=%d, existing_skipped=%d)",
        len(things or []),
        len(unique),
        len(things or []) - len(unique),
    )
    return list(unique.values())


def dedupe_connections(conns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    existing_ids = {c.edge_id for c in list_connections()}
    unique: Dict[str, Dict[str, Any]] = {}
    for c in conns or []:
        cid = c.get("edge_id")
        if not cid or cid in existing_ids:
            continue
        unique[cid] = c
    logger.info(
        "OpenAI ingest: deduped connections (incoming=%d, kept=%d, existing_skipped=%d)",
        len(conns or []),
        len(unique),
        len(conns or []) - len(unique),
    )
    return list(unique.values())


def normalize_chunks(
    chunks: List[Dict[str, Any]],
    collection_name: str,
    base_metadata: Dict[str, Any] | None = None,
) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
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
            "thing_type": normalize_thing_type(ch.get("thing_type")) if ch.get("thing_type") else None,
            "edge_id": ch.get("edge_id"),
            "tags": ch.get("tags") or [],
        }
        if base_metadata:
            md.update(base_metadata)
        md = {k: v for k, v in md.items() if v not in (None, [], {}, "")}
        ids.append(cid)
        texts.append(text)
        metas.append(sanitize_metadata(md))

    logger.info(
        "OpenAI ingest: normalized chunks (incoming=%d, ready_for_upsert=%d)",
        len(chunks or []),
        len(ids),
    )

    return ids, texts, metas


def ingest_lore_from_text(
    text: str,
    collection: str,
    notes: str | None = None,
    source: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if not text or not text.strip():
        raise ValueError("text must be provided")

    safe_collection = normalize_collection_name(collection)
    logger.info(
        "OpenAI ingest: starting ingestion (collection=%s, text_len=%d, notes_len=%d)",
        safe_collection,
        len(text.strip()),
        len(notes or ""),
    )
    extracted = call_openai(text, notes)

    logger.info(
        "OpenAI ingest: extraction returned counts (things=%d, connections=%d, chunks=%d)",
        len(extracted.get("things") or []),
        len(extracted.get("connections") or []),
        len(extracted.get("chunks") or []),
    )

    # Things
    raw_things = extracted.get("things") or []
    sanitized_things: List[Dict[str, Any]] = []
    for t in raw_things:
        copy = dict(t)
        copy["thing_type"] = normalize_thing_type(copy.get("thing_type"))
        sanitized_things.append(copy)

    new_things = dedupe_things(sanitized_things)
    for t in new_things:
        try:
            upsert_thing(Thing.model_validate(t))
        except Exception:
            logger.exception("OpenAI ingest: failed to store thing with id=%s", t.get("thing_id"))
            raise

    # Connections
    new_conns = dedupe_connections(extracted.get("connections") or [])
    for c in new_conns:
        try:
            upsert_connection(Connection.model_validate(c))
        except Exception:
            logger.exception("OpenAI ingest: failed to store connection with id=%s", c.get("edge_id"))
            raise

    # Chunks
    base_meta = {}
    if source:
        base_meta = {
            "source_file": source.get("filename"),
            "source_url": source.get("url"),
            "source_file_id": source.get("file_id"),
        }

    ids, texts, metas = normalize_chunks(extracted.get("chunks") or [], safe_collection, base_meta)
    if ids:
        col = get_collection(safe_collection)
        col.upsert(ids=ids, documents=texts, metadatas=metas)
        logger.info(
            "OpenAI ingest: stored %d chunk(s) in collection '%s'", len(ids), safe_collection
        )
    else:
        logger.info("OpenAI ingest: no new chunks to store")

    annotated_chunks = extracted.get("chunks") or []
    if base_meta:
        enriched: List[Dict[str, Any]] = []
        for ch in annotated_chunks:
            copy = dict(ch)
            for k, v in base_meta.items():
                copy.setdefault(k, v)
            enriched.append(copy)
        annotated_chunks = enriched

    return {
        "counts": {
            "things": len(new_things),
            "connections": len(new_conns),
            "chunks": len(ids),
        },
        "things": new_things,
        "connections": new_conns,
        "chunks": annotated_chunks,
    }
