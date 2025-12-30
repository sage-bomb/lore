"""OpenAI-powered ingestion pipeline for extracting lore and chunk drafts."""

import json
import logging
import os
from typing import Any, Dict, List

from app.domain.collections import get_collection, normalize_collection_name, sanitize_metadatas
from app.domain.library import list_connections, list_things, upsert_connection, upsert_thing
from app.domain.chunking.orchestrator import annotate_chunks, detect_or_reuse_chunks, derive_doc_id
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


def _persist_chunk_draft(
    *,
    doc_id: str,
    text: str,
    source: Dict[str, Any] | None,
    collection: str,
) -> Dict[str, Any]:
    detection = detect_or_reuse_chunks(
        doc_id=doc_id,
        text=text,
        filename=(source or {}).get("filename"),
        url=(source or {}).get("url"),
    )

    base_meta = {
        "source_file": source.get("filename") if source else None,
        "source_url": source.get("url") if source else None,
        "source_file_id": source.get("file_id") if source else None,
        "doc_id": doc_id,
        "collection": collection,
    }
    base_meta = {k: v for k, v in base_meta.items() if v not in (None, "", [], {})}

    annotated = annotate_chunks(
        [c for c in detection["chunks"] if getattr(c, "finalized", False)],
        base_meta,
        chunk_kind="chapter_text",
    )

    serialized_chunks: List[Dict[str, Any]] = []
    for ch in detection["chunks"]:
        data = ch.model_dump(mode="json")
        merged = dict(base_meta)
        merged.update(data)
        serialized_chunks.append(merged)

    logger.info(
        "OpenAI ingest: chunk draft stored (doc_id=%s, version=%s, finalized=%s, reused=%s, finalized_chunks=%d)",
        doc_id,
        detection.get("version"),
        detection.get("finalized"),
        detection.get("reused"),
        len(annotated["ids"]),
    )

    return {
        "detection": detection,
        "annotated": annotated,
        "base_meta": base_meta,
        "chunks_json": serialized_chunks,
    }


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

    doc_id = derive_doc_id(
        explicit_doc_id=source.get("file_id") if source else None,
        source=source,
        text=text,
        collection=safe_collection,
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

    chunk_result = _persist_chunk_draft(doc_id=doc_id, text=text, source=source, collection=safe_collection)
    annotated_chunks = chunk_result["annotated"]

    if annotated_chunks["ids"]:
        col = get_collection(safe_collection)
        col.upsert(
            ids=annotated_chunks["ids"],
            documents=annotated_chunks["documents"],
            metadatas=sanitize_metadatas(annotated_chunks["metadatas"]),
        )
        logger.info(
            "OpenAI ingest: stored %d finalized chunk(s) in collection '%s'", len(annotated_chunks["ids"]), safe_collection
        )
    else:
        logger.info("OpenAI ingest: no finalized chunks to store (pending user edits)")

    return {
        "counts": {
            "things": len(new_things),
            "connections": len(new_conns),
            "chunks": len(annotated_chunks["ids"]),
        },
        "things": new_things,
        "connections": new_conns,
        "chunks": chunk_result["chunks_json"],
        "chunk_state": {
            "doc_id": doc_id,
            "version": chunk_result["detection"].get("version"),
            "finalized": chunk_result["detection"].get("finalized"),
            "reused": chunk_result["detection"].get("reused"),
        },
    }
