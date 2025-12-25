import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.chroma_store import get_collection
from app.schemas import (
    ChunkBoundary,
    ChunkingDetectResponse,
    ChunkingFinalizeResponse,
    ChunkKind,
    ChunksUpsert,
    SearchChunk,
)


_draft_store: Dict[str, Dict[str, Any]] = {}


def _draft_key(collection: Optional[str], doc_id: Optional[str]) -> str:
    safe_collection = collection or "default"
    safe_doc = doc_id or "adhoc"
    return f"{safe_collection}::{safe_doc}"


def _normalize_chunk_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", ".", value.strip())
    return cleaned or f"chunk.{uuid.uuid4()}"


def _split_into_chunks(lines: List[str], chunk_kind: Optional[ChunkKind], doc_id: Optional[str]) -> List[ChunkBoundary]:
    chunks: List[ChunkBoundary] = []
    start: Optional[int] = None

    for idx, line in enumerate(lines + [""]):  # sentinel for flush
        if line.strip():
            if start is None:
                start = idx
        elif start is not None:
            end = idx - 1
            text = "\n".join(lines[start : end + 1]).strip("\n")
            if text:
                chunks.append(ChunkBoundary(
                    chunk_id=f"chunk.{_normalize_chunk_id(doc_id or 'draft')}.{len(chunks) + 1}",
                    start_line=start,
                    end_line=end,
                    chunk_kind=chunk_kind or "chapter_text",
                    text=text,
                ))
            start = None

    if not chunks and lines:
        text = "\n".join(lines).strip("\n")
        if text:
            chunks.append(ChunkBoundary(
                chunk_id=f"chunk.{_normalize_chunk_id(doc_id or 'draft')}.1",
                start_line=0,
                end_line=len(lines) - 1,
                chunk_kind=chunk_kind or "chapter_text",
                text=text,
            ))

    return chunks


def detect(text: str, doc_id: Optional[str], chunk_kind: Optional[ChunkKind]) -> ChunkingDetectResponse:
    normalized = text.replace("\r\n", "\n")
    lines = normalized.split("\n")
    chunks = _split_into_chunks(lines, chunk_kind, doc_id)
    return ChunkingDetectResponse(text=normalized, line_count=len(lines), chunks=chunks)


def save_draft(collection: Optional[str], doc_id: Optional[str], text: str, chunks: List[ChunkBoundary]) -> str:
    key = _draft_key(collection, doc_id)
    _draft_store[key] = {
        "text": text,
        "chunks": [c.model_dump(mode="json") for c in chunks],
    }
    return key


def load_text_from_doc_id(doc_id: str, collection: Optional[str]) -> str:
    draft = _draft_store.get(_draft_key(collection, doc_id))
    if draft and draft.get("text"):
        return str(draft["text"])

    if collection:
        col = get_collection(collection)
        got = col.get(ids=[doc_id])
        docs = got.get("documents") or []
        if docs and docs[0]:
            return str(docs[0])

    raise ValueError("Document not found")


def finalize(
    collection: str,
    text: str,
    chunks: List[ChunkBoundary],
    embed: bool,
    default_kind: Optional[ChunkKind],
    doc_id: Optional[str],
) -> ChunkingFinalizeResponse:
    normalized = text.replace("\r\n", "\n")
    lines = normalized.split("\n")

    prepared: List[SearchChunk] = []
    chunk_ids: List[str] = []

    for idx, chunk in enumerate(chunks):
        start = max(chunk.start_line, 0)
        end = min(chunk.end_line, len(lines) - 1)
        if end < start:
            continue

        chunk_text = "\n".join(lines[start : end + 1]).strip()
        if not chunk_text:
            continue

        cid = _normalize_chunk_id(chunk.chunk_id or f"chunk.{doc_id or collection}.{idx + 1}")
        prepared.append(SearchChunk(
            chunk_id=cid,
            text=chunk_text,
            chunk_kind=chunk.chunk_kind or default_kind or "chapter_text",
            thing_id=chunk.thing_id,
            thing_type=chunk.thing_type,
            edge_id=chunk.edge_id,
            chapter_number=chunk.chapter_number,
            scene_id=chunk.scene_id,
            pov=chunk.pov,
            location_id=chunk.location_id,
            entity_ids=chunk.entity_ids or [],
            tags=chunk.tags or [],
            source_file=chunk.source_file,
            source_section=chunk.source_section,
            extra=chunk.extra,
        ))
        chunk_ids.append(cid)

    if embed and prepared:
        payload = ChunksUpsert(chunks=prepared)
        col = get_collection(collection)
        ids = [c.chunk_id for c in payload.chunks]
        docs = [c.text for c in payload.chunks]
        metas: List[Dict[str, Any]] = []
        for c in payload.chunks:
            meta: Dict[str, Any] = {
                "chunk_kind": c.chunk_kind,
                "thing_id": c.thing_id,
                "thing_type": c.thing_type,
                "edge_id": c.edge_id,
                "source_file": c.source_file,
                "source_section": c.source_section,
                "chapter_number": c.chapter_number,
                "scene_id": c.scene_id,
                "pov": c.pov,
                "location_id": c.location_id,
                "entity_ids": c.entity_ids,
                "tags": c.tags,
            }
            if c.extra:
                meta.update({f"extra.{k}": v for k, v in c.extra.items()})
            metas.append({k: v for k, v in meta.items() if v is not None})

        col.upsert(ids=ids, documents=docs, metadatas=metas)

    draft_key = save_draft(collection, doc_id, normalized, list(chunks))
    return ChunkingFinalizeResponse(
        saved=len(prepared),
        embedded=bool(embed and prepared),
        collection=collection,
        chunk_ids=chunk_ids,
        draft_key=draft_key,
    )

