"""Chunk detection pipeline with optional OpenAI-driven enrichment."""

import importlib
import json
import logging
import os
from typing import Any, Dict, List, Optional

from app.domain.chunking.core import chunk_document, default_boundary_score, hash_chunk_id
from app.domain.collections import embedding_function
from app.schemas import ChunkDetectionRequest, ChunkMetadata

logger = logging.getLogger(__name__)


def _get_openai_client():
    spec = importlib.util.find_spec("openai")
    if spec is None:
        logger.warning("Chunk enhancer: openai package not installed; skipping enrichment")
        return None
    openai_mod = importlib.import_module("openai")
    return openai_mod.OpenAI()


def _build_enhancement_messages(doc_id: str, text: str, chunks: List[ChunkMetadata]) -> list[dict[str, str]]:
    condensed_chunks = []
    for ch in chunks:
        condensed_chunks.append(
            {
                "chunk_id": ch.chunk_id,
                "start_line": ch.start_line,
                "end_line": ch.end_line,
                "text": ch.text[:1200],
            }
        )
    system = (
        "You enhance chunked document segments with structured annotations.\n"
        "Return JSON with keys: chunks (list) and document_summary (object).\n"
        "Each item in chunks must include chunk_id (string), summary_title (short title), "
        "tags (list of short labels), and thing_type (string describing primary entity type or 'other').\n"
        "document_summary must include title (short heading), summary (2-4 sentences), and tags (list).\n"
        "Keep responses concise, avoid markdown, and do not invent chunk IDs."
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                {"doc_id": doc_id, "document_preview": text[:2000], "chunks": condensed_chunks},
                ensure_ascii=False,
            ),
        },
    ]


def _enhance_with_openai(
    doc_id: str, text: str, chunks: List[ChunkMetadata]
) -> tuple[Dict[str, Dict[str, Any]], Optional[Dict[str, Any]]]:
    if not chunks:
        return {}, None

    client = _get_openai_client()
    if client is None:
        return {}, None

    api_key_present = bool(os.getenv("OPENAI_API_KEY"))
    logger.info(
        "Chunk enhancer: sending %d chunk(s) to OpenAI (api_key_present=%s)",
        len(chunks),
        api_key_present,
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=_build_enhancement_messages(doc_id, text, chunks),
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
    except Exception:
        logger.exception("Chunk enhancer: OpenAI request failed; returning base chunks")
        return {}, None

    chunk_map: Dict[str, Dict[str, Any]] = {}
    for item in data.get("chunks") or []:
        chunk_id = item.get("chunk_id")
        if not chunk_id:
            continue
        chunk_map[chunk_id] = {
            "summary_title": item.get("summary_title"),
            "tags": [t for t in (item.get("tags") or []) if t],
            "thing_type": item.get("thing_type"),
        }

    doc_summary = data.get("document_summary") if isinstance(data.get("document_summary"), dict) else None
    logger.info(
        "Chunk enhancer: received annotations (chunks=%d, has_document_summary=%s)",
        len(chunk_map),
        bool(doc_summary),
    )
    return chunk_map, doc_summary


def _make_meta_chunk(doc_id: str, text: str, summary: Dict[str, Any]) -> ChunkMetadata:
    summary_text = summary.get("summary") or ""
    summary_title = summary.get("title") or "Document Summary"
    tags = [t for t in (summary.get("tags") or []) if t]
    chunk_id = f"{hash_chunk_id(doc_id, 0, len(text))}-meta"
    return ChunkMetadata(
        doc_id=doc_id,
        chunk_id=chunk_id,
        text=summary_text,
        start_char=0,
        end_char=0,
        start_line=0,
        end_line=0,
        length_chars=len(summary_text),
        length_lines=max(1, summary_text.count("\n") + 1) if summary_text else 0,
        boundary_reasons=["document meta"],
        confidence=1.0,
        overlap=0,
        chunk_kind="document_meta",
        summary_title=summary_title,
        tags=tags,
        is_meta_chunk=True,
    )


def detect_chunks(payload: ChunkDetectionRequest) -> List[ChunkMetadata]:
    """
    Detect and enrich document chunks using the reusable chunking core and the
    app-layer OpenAI enhancer.
    """
    text = payload.text or ""
    chunks = chunk_document(
        payload.doc_id,
        text,
        payload.min_chars,
        payload.target_chars,
        payload.max_chars,
        overlap=payload.overlap,
        embed_fn=embedding_function(),
        break_detector=default_boundary_score,
    )

    enhancement_map, doc_summary = _enhance_with_openai(payload.doc_id, text, chunks)
    meta_chunk: ChunkMetadata | None = None

    if doc_summary:
        meta_chunk = _make_meta_chunk(payload.doc_id, text, doc_summary)
        child_ids: List[str] = []
        for ch in chunks:
            ch.parent_chunk_id = meta_chunk.chunk_id
            child_ids.append(ch.chunk_id)
        meta_chunk.child_chunk_ids = child_ids

    for ch in chunks:
        update = enhancement_map.get(ch.chunk_id, {})
        if update.get("summary_title"):
            ch.summary_title = update["summary_title"]
        if update.get("tags"):
            ch.tags = update["tags"]
        if update.get("thing_type"):
            ch.thing_type = update["thing_type"]

    if meta_chunk:
        return [meta_chunk, *chunks]
    return chunks
