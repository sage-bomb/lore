import hashlib
import logging
import re
from typing import Any, Dict, List, Optional

from app.chunk_store import get_chunks, store_chunks
from app.schemas import ChunkDetectionRequest, ChunkMetadata
from app.services.chunking import detect_chunks

logger = logging.getLogger(__name__)


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", value.strip().lower())
    cleaned = cleaned.strip("-._")
    return cleaned or "doc"


def slugify(value: str) -> str:
    """Public wrapper to produce URL-safe-ish doc_id fragments."""
    return _slugify(value)


def derive_doc_id(
    *,
    explicit_doc_id: Optional[str],
    source: Optional[Dict[str, Any]],
    text: str,
    collection: str,
) -> str:
    candidates = [explicit_doc_id]
    if source:
        candidates.extend([source.get("url"), source.get("filename"), source.get("file_id")])

    for candidate in candidates:
        if candidate and str(candidate).strip():
            slug = _slugify(str(candidate))
            if slug:
                return slug

    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"{_slugify(collection)}-{digest[:12]}"


def detect_or_reuse_chunks(
    *,
    doc_id: str,
    text: str,
    detection_overrides: Optional[Dict[str, int]] = None,
    filename: Optional[str] = None,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    existing = get_chunks(doc_id)
    if existing:
        logger.info(
            "Chunk orchestrator: reusing stored chunk set (doc_id=%s, version=%s, finalized=%s)",
            doc_id,
            existing.get("version"),
            existing.get("finalized"),
        )
        # Refresh stored text if new content is provided
        if text and text != (existing.get("text") or ""):
            version, finalized = store_chunks(
                doc_id,
                existing["chunks"],
                finalized=existing.get("finalized", False),
                text=text,
                filename=filename,
                url=url,
            )
            existing = get_chunks(doc_id) or {
                "chunks": existing["chunks"],
                "version": version,
                "finalized": finalized,
                "text": text,
            }
        return {
            "chunks": existing.get("chunks") or [],
            "version": existing.get("version", 1),
            "finalized": bool(existing.get("finalized", False)),
            "reused": True,
            "doc_id": doc_id,
            "filename": existing.get("filename"),
            "url": existing.get("url"),
        }

    payload_kwargs = detection_overrides or {}
    detection_request = ChunkDetectionRequest(doc_id=doc_id, text=text, **payload_kwargs)
    chunks = detect_chunks(detection_request)
    version, finalized = store_chunks(doc_id, chunks, finalized=False, text=text, filename=filename, url=url)
    logger.info(
        "Chunk orchestrator: detected %d chunk(s) (doc_id=%s, version=%d, finalized=%s)",
        len(chunks),
        doc_id,
        version,
        finalized,
    )
    return {
        "chunks": chunks,
        "version": version,
        "finalized": finalized,
        "reused": False,
        "doc_id": doc_id,
        "filename": filename,
        "url": url,
    }


def annotate_chunks(
    chunks: List[ChunkMetadata],
    base_metadata: Dict[str, Any],
    *,
    chunk_kind: str = "chapter_text",
) -> Dict[str, List[Any]]:
    ids: List[str] = []
    documents: List[str] = []
    metadatas: List[Dict[str, Any]] = []

    for chunk in chunks:
        ids.append(chunk.chunk_id)
        documents.append(chunk.text)
        resolved_chunk_kind = chunk.chunk_kind or chunk_kind
        meta = dict(base_metadata)
        meta.update(
            {
                "doc_id": chunk.doc_id,
                "chunk_kind": resolved_chunk_kind,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "start_char": chunk.start_char,
                "end_char": chunk.end_char,
                "version": chunk.version,
                "finalized": chunk.finalized,
                "length_chars": chunk.length_chars,
                "length_lines": chunk.length_lines,
                "boundary_reasons": chunk.boundary_reasons,
                "overlap": chunk.overlap,
                "tags": chunk.tags,
                "thing_type": chunk.thing_type,
                "summary_title": chunk.summary_title,
                "parent_chunk_id": chunk.parent_chunk_id,
                "child_chunk_ids": chunk.child_chunk_ids,
                "is_meta_chunk": chunk.is_meta_chunk,
            }
        )
        metadatas.append(meta)

    return {"ids": ids, "documents": documents, "metadatas": metadatas}
