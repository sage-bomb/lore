import itertools
import re
import uuid
from typing import Dict, List, Optional, Tuple, get_args

from app.domain import library
from app.domain.chunking.orchestrator import derive_doc_id, detect_or_reuse_chunks
from app.domain.ingestion.openip_client import extract_lore
from app.schemas import ChunkKind, ChunkMetadata, Connection, SearchChunk, Thing

CHUNK_KIND_OPTIONS = set(get_args(ChunkKind))

def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", ".", value).strip(".")
    return value or str(uuid.uuid4())


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _existing_lookup(things: List[Thing]) -> Dict[Tuple[str, str], Thing]:
    """Map (thing_type, normalized name or alias) -> Thing for quick matching."""
    mapping: Dict[Tuple[str, str], Thing] = {}
    for thing in things:
        names = [thing.name] + thing.aliases
        for name in names:
            key = (thing.thing_type, _normalize_name(name))
            mapping[key] = thing
    return mapping


def _merge_lists(existing: List[str], incoming: List[str]) -> List[str]:
    seen = set(existing)
    merged = list(existing)
    for item in incoming:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    return merged


def _merge_data(existing: Dict, incoming: Dict) -> Dict:
    merged = dict(existing)
    for key, value in incoming.items():
        if key not in merged:
            merged[key] = value
    return merged


def _reconcile_thing(
    candidate: Thing,
    existing_by_id: Dict[str, Thing],
    existing_by_name: Dict[Tuple[str, str], Thing],
) -> Thing:
    if candidate.thing_id in existing_by_id:
        base = existing_by_id[candidate.thing_id]
    else:
        base = None
        for name in [candidate.name] + candidate.aliases:
            key = (candidate.thing_type, _normalize_name(name))
            if key in existing_by_name:
                base = existing_by_name[key]
                break

    if not base:
        return candidate

    aliases = _merge_lists(base.aliases, candidate.aliases)
    tags = _merge_lists(base.tags, candidate.tags)
    data = _merge_data(base.data, candidate.data)

    return base.model_copy(update={
        "aliases": aliases,
        "tags": tags,
        "data": data,
        "summary": base.summary or candidate.summary,
        "description": base.description or candidate.description,
    })


def _make_thing(payload: Dict) -> Thing:
    thing_id = payload.get("thing_id")
    name = payload.get("name") or thing_id or ""
    thing_type = payload.get("thing_type") or "other"

    if not thing_id:
        thing_id = f"{thing_type}.{_slugify(name or thing_type)}"

    return Thing(
        thing_id=thing_id,
        thing_type=thing_type,
        name=name or thing_id,
        aliases=payload.get("aliases") or [],
        summary=payload.get("summary"),
        description=payload.get("description"),
        tags=payload.get("tags") or [],
        data=payload.get("data") or {},
    )


def _make_connection(payload: Dict) -> Connection:
    edge_id = payload.get("edge_id") or str(uuid.uuid4())
    return Connection(
        edge_id=edge_id,
        src_id=payload.get("src_id") or payload.get("source_id"),
        dst_id=payload.get("dst_id") or payload.get("target_id"),
        rel_type=payload.get("rel_type") or payload.get("relationship") or "related_to",
        note=payload.get("note") or payload.get("description"),
        tags=payload.get("tags") or [],
    )


def _chunks_for_things(
    things: List[Thing],
    source_file: Optional[str],
    source_section: Optional[str],
) -> List[SearchChunk]:
    chunks: List[SearchChunk] = []
    for thing in things:
        if thing.summary:
            chunks.append(SearchChunk(
                chunk_id=f"chunk.{thing.thing_id}.summary",
                text=thing.summary,
                chunk_kind="thing_summary",
                thing_id=thing.thing_id,
                thing_type=thing.thing_type,
                entity_ids=[thing.thing_id],
                tags=thing.tags,
                source_file=source_file,
                source_section=source_section,
            ))
        notes = []
        if thing.description:
            notes.append(thing.description)
        if thing.data:
            notes.extend([f"{k}: {v}" for k, v in thing.data.items()])
        if notes:
            chunks.append(SearchChunk(
                chunk_id=f"chunk.{thing.thing_id}.notes",
                text="\n".join(notes),
                chunk_kind="thing_notes",
                thing_id=thing.thing_id,
                thing_type=thing.thing_type,
                entity_ids=[thing.thing_id],
                tags=thing.tags,
                source_file=source_file,
                source_section=source_section,
            ))
    return chunks


def _chunks_for_connections(
    connections: List[Connection],
    source_file: Optional[str],
    source_section: Optional[str],
) -> List[SearchChunk]:
    chunks: List[SearchChunk] = []
    for edge in connections:
        if not edge.note:
            continue
        chunks.append(SearchChunk(
            chunk_id=f"chunk.{edge.edge_id}.note",
            text=edge.note,
            chunk_kind="connection_note",
            edge_id=edge.edge_id,
            entity_ids=[edge.src_id, edge.dst_id],
            tags=edge.tags,
            source_file=source_file,
            source_section=source_section,
        ))
    return chunks


def _resolve_chunk_kind(value: Optional[str]) -> str:
    if value and value in CHUNK_KIND_OPTIONS:
        return value
    return "misc"


def _chunk_meta_to_search_chunk(
    chunk: ChunkMetadata,
    base_metadata: Dict[str, str],
) -> SearchChunk:
    resolved_kind = _resolve_chunk_kind(chunk.chunk_kind)
    meta = {k: v for k, v in base_metadata.items() if v not in (None, "", [], {})}
    meta.update(
        {
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "start_char": chunk.start_char,
            "end_char": chunk.end_char,
            "version": chunk.version,
            "finalized": chunk.finalized,
            "boundary_reasons": chunk.boundary_reasons,
            "overlap": chunk.overlap,
            "confidence": chunk.confidence,
            "tags": chunk.tags,
            "thing_type": chunk.thing_type,
            "summary_title": chunk.summary_title,
            "parent_chunk_id": chunk.parent_chunk_id,
            "child_chunk_ids": chunk.child_chunk_ids,
            "is_meta_chunk": chunk.is_meta_chunk,
            "chunk_kind": resolved_kind,
        }
    )

    return SearchChunk(
        chunk_id=chunk.chunk_id,
        text=chunk.text,
        chunk_kind=resolved_kind,
        thing_type=chunk.thing_type,
        tags=chunk.tags,
        **meta,
    )


def _reconcile_items(things: List[Thing], connections: List[Connection]) -> Tuple[List[Thing], List[Connection]]:
    existing_things = library.list_things()
    existing_by_id = {t.thing_id: t for t in existing_things}
    existing_by_name = _existing_lookup(existing_things)

    reconciled_things: List[Thing] = []
    for thing in things:
        reconciled_things.append(_reconcile_thing(thing, existing_by_id, existing_by_name))

    existing_edges = {c.edge_id: c for c in library.list_connections()}
    reconciled_connections: List[Connection] = []
    for conn in connections:
        existing = existing_edges.get(conn.edge_id)
        if existing:
            reconciled_connections.append(existing)
        else:
            reconciled_connections.append(conn)

    return reconciled_things, reconciled_connections


def ingest_text(
    text: str,
    collection: Optional[str] = None,
    source_file: Optional[str] = None,
    source_section: Optional[str] = None,
):
    extracted = extract_lore(text)

    raw_things = extracted.get("things") or []
    raw_connections = extracted.get("connections") or extracted.get("relationships") or []

    things = [_make_thing(t) for t in raw_things]
    connections = [_make_connection(c) for c in raw_connections]

    things, connections = _reconcile_items(things, connections)

    # Detect document chunks using the OpenAI-backed pipeline used by the chunking UI.
    source_hint = {"filename": source_file} if source_file else None
    doc_id = derive_doc_id(explicit_doc_id=None, source=source_hint, text=text, collection=collection or "default")
    detection = detect_or_reuse_chunks(doc_id=doc_id, text=text, filename=source_file, url=None)

    base_meta = {
        "doc_id": doc_id,
        "collection": collection,
        "source_file": source_file,
        "source_section": source_section,
    }

    detected_chunks = [
        _chunk_meta_to_search_chunk(chunk, base_meta)
        for chunk in detection["chunks"]
    ]

    # Preserve legacy thing/connection chunk generation for compatibility.
    chunks = list(itertools.chain(
        detected_chunks,
        _chunks_for_things(things, source_file, source_section),
        _chunks_for_connections(connections, source_file, source_section),
    ))

    return {
        "things": things,
        "connections": connections,
        "chunks": chunks,
        "collection": collection,
    }
