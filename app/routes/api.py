"""Public JSON API routes for Spellbinder.

These handlers translate HTTP requests into domain-layer calls and return
validated responses for the UI and API consumers. Keep the logic thin and
delegate to domain modules for data access and processing.
"""

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from app.domain.chunking import detect_chunks
from app.domain.chunking.orchestrator import detect_or_reuse_chunks, derive_doc_id, slugify
from app.domain.collections import (
    client,
    get_collection,
    list_collection_names,
    normalize_collection_name,
    sanitize_metadata,
    sanitize_metadatas,
)
from app.domain.chunks import get_chunks, list_docs, store_chunks
from app.domain.ingestion import ingest_lore_from_text, ingest_text
from app.domain.library import (
    delete_connection,
    delete_thing,
    get_connection,
    get_thing,
    list_connections,
    list_things,
    upsert_connection,
    upsert_thing,
)
from app.schemas import (
    ChunkDetectionRequest,
    ChunkFinalizeRequest,
    ChunkMetadata,
    ChunkOut,
    ChunkUpdate,
    ChunksUpsert,
    CollectionCreate,
    CollectionInfo,
    Connection,
    OpenAIIngestRequest,
    OpenAIIngestResponse,
    QueryHit,
    QueryRequest,
    Thing,
)
from app.upload_store import describe_upload, extract_text_from_bytes, save_upload

router = APIRouter(prefix="/api", tags=["api"])


# ---------------- Helpers ----------------

def _merge_where(base: Optional[Dict[str, Any]], extra: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Merge two Chroma `where` clauses using an `$and` conjunction."""
    if base and extra:
        return {"$and": [base, extra]}
    return base or extra


def _apply_in_filter(where: Optional[Dict[str, Any]], field: str, values: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    """Apply an `$in` filter for the given field if one or more values are present."""
    if not values:
        return where
    if len(values) == 1:
        clause: Dict[str, Any] = {field: values[0]}
    else:
        clause = {field: {"$in": values}}
    return _merge_where(where, clause)


def _coerce_int(value: Optional[str]) -> Optional[int]:
    """Convert optional string query params to integers, returning None on failure."""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ---------------- Collections CRUD ----------------

@router.get("/collections", response_model=List[CollectionInfo])
def collections_list():
    """List available Chroma collections by name."""
    return [{"name": n} for n in list_collection_names()]


@router.post("/collections", response_model=CollectionInfo)
def collections_create(payload: CollectionCreate):
    """Create or return an existing Chroma collection with a normalized name."""
    try:
        safe_name = normalize_collection_name(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    col = get_collection(safe_name)
    return {"name": col.name}


@router.get("/collections/{name}", response_model=CollectionInfo)
def collections_get(name: str):
    """Fetch collection metadata for the provided name, returning 404 when missing."""
    try:
        safe_name = normalize_collection_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    existing = set(list_collection_names())
    if safe_name not in existing:
        raise HTTPException(status_code=404, detail="Collection not found")
    return {"name": safe_name}


@router.delete("/collections/{name}")
def collections_delete(name: str):
    """Delete a collection when it exists; reject invalid names or missing resources."""
    try:
        safe_name = normalize_collection_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    existing = set(list_collection_names())
    if safe_name not in existing:
        raise HTTPException(status_code=404, detail="Collection not found")
    client().delete_collection(name=safe_name)
    return {"ok": True, "deleted": safe_name}


# ---------------- OpenAI ingest ----------------

@router.post("/ingest/openai", response_model=OpenAIIngestResponse)
def ingest_openai(payload: OpenAIIngestRequest):
    """Run the OpenAI-backed ingestion pipeline and return extracted lore + chunk draft info."""
    try:
        source = {"url": payload.url} if payload.url else None
        result = ingest_lore_from_text(payload.text, payload.collection, payload.notes, source=source)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.post("/ingest/upload")
async def ingest_upload(
    collection: str = Form(...),
    notes: Optional[str] = Form(default=None),
    files: List[UploadFile] = File(...),
):
    """Accept uploaded files, extract text, run ingestion, and summarize results per file."""
    safe_collection = normalize_collection_name(collection)
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required.")

    totals = {"things": 0, "connections": 0, "chunks": 0}
    all_chunks: List[Dict[str, Any]] = []
    file_results: List[Dict[str, Any]] = []

    for f in files:
        data = await f.read()
        upload_meta = save_upload(f, data)
        text = extract_text_from_bytes(data)
        size_bytes = len(data) if data is not None else None

        if not text.strip():
            file_results.append({
                "file": describe_upload(upload_meta, size_bytes),
                "error": "File is empty or unreadable",
            })
            continue

        try:
            result = ingest_lore_from_text(
                text=text,
                collection=safe_collection,
                notes=notes,
                source={
                    "filename": upload_meta["filename"],
                    "file_id": upload_meta["file_id"],
                    "url": upload_meta["url"],
                },
            )
        except Exception as exc:
            file_results.append({
                "file": describe_upload(upload_meta, size_bytes),
                "error": str(exc),
            })
            continue

        totals["things"] += result["counts"]["things"]
        totals["connections"] += result["counts"]["connections"]
        totals["chunks"] += result["counts"]["chunks"]
        all_chunks.extend(result.get("chunks") or [])

        file_results.append({
            "file": describe_upload(upload_meta, size_bytes),
            "counts": result["counts"],
            "doc_id": result.get("chunk_state", {}).get("doc_id"),
            "chunk_state": result.get("chunk_state"),
            "things": result.get("things") or [],
            "connections": result.get("connections") or [],
            "chunks": result.get("chunks") or [],
        })

    return {
        "ok": True,
        "collection": safe_collection,
        "totals": totals,
        "files": file_results,
        "chunks": all_chunks,
    }


# ---------------- Things ----------------

@router.post("/things", response_model=Thing)
def things_upsert(payload: Thing):
    """Create or update a lore Thing and return the stored record."""
    stored = upsert_thing(payload)
    return stored


@router.get("/things/{thing_id}", response_model=Thing)
def things_get(thing_id: str):
    """Retrieve a Thing by ID or return 404 when absent."""
    got = get_thing(thing_id)
    if not got:
        raise HTTPException(status_code=404, detail="Thing not found")
    return got


@router.get("/things", response_model=List[Thing])
def things_list(
    thing_type: Optional[str] = Query(default=None, alias="type", description="Filter by thing_type"),
    tag: Optional[str] = Query(default=None, description="Filter by tag"),
    q: Optional[str] = Query(default=None, description="Simple substring match across name/aliases/summary/description"),
):
    """List Things with optional filters for type, tag, or free-text search."""
    return list_things(thing_type=thing_type, tag=tag, q=q)


@router.delete("/things/{thing_id}")
def things_delete(thing_id: str):
    """Delete a Thing and surface a 404 when no record is removed."""
    removed = delete_thing(thing_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Thing not found")
    return {"ok": True, "deleted": thing_id}


# ---------------- Connections ----------------

@router.post("/connections", response_model=Connection)
def connections_upsert(payload: Connection):
    """Create or update a connection edge and return the stored record."""
    stored = upsert_connection(payload)
    return stored


@router.get("/connections/{edge_id}", response_model=Connection)
def connections_get(edge_id: str):
    """Retrieve a connection by ID or raise 404 if missing."""
    got = get_connection(edge_id)
    if not got:
        raise HTTPException(status_code=404, detail="Connection not found")
    return got


@router.get("/connections", response_model=List[Connection])
def connections_list(
    thing_id: Optional[str] = Query(default=None, description="Return connections involving the thing_id"),
):
    """List connections, optionally filtering by an involved Thing ID."""
    return list_connections(thing_id=thing_id)


@router.delete("/connections/{edge_id}")
def connections_delete(edge_id: str):
    """Delete a connection and surface a 404 when the record does not exist."""
    removed = delete_connection(edge_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"ok": True, "deleted": edge_id}


# ---------------- Chunks CRUD (preferred) ----------------

@router.post("/collections/{name}/chunks")
def chunks_upsert(name: str, payload: ChunksUpsert):
    """Store or update chunks for a collection, flattening metadata for Chroma compatibility."""
    col = get_collection(name)

    ids = [c.chunk_id for c in payload.chunks]
    docs = [c.text for c in payload.chunks]

    # Put all filterable fields in metadata (flat dict)
    metas: List[Dict[str, Any]] = []
    for c in payload.chunks:
        chunk_kind = getattr(c, "chunk_kind", None) or getattr(c, "doc_kind", None) or "thing_summary"
        thing_type = c.thing_type or getattr(c, "record_type", None)
        thing_id = c.thing_id or getattr(c, "record_id", None)

        md: Dict[str, Any] = {
            "chunk_kind": chunk_kind,
            "thing_id": thing_id,
            "thing_type": thing_type,
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
            # keep it flat-ish; nested dicts may work but can make filtering harder
            md.update({f"extra.{k}": v for k, v in c.extra.items()})

        metas.append({k: v for k, v in md.items() if v is not None})

    col.upsert(ids=ids, documents=docs, metadatas=sanitize_metadatas(metas))
    return {"ok": True, "upserted": len(ids), "collection": name}


@router.get("/collections/{name}/chunks/{chunk_id}", response_model=ChunkOut)
def chunks_get(name: str, chunk_id: str):
    """Return a single chunk's text and metadata from a collection."""
    col = get_collection(name)
    got = col.get(ids=[chunk_id])

    ids = got.get("ids") or []
    if not ids:
        raise HTTPException(status_code=404, detail="Chunk not found")

    docs = (got.get("documents") or [None])
    metas = (got.get("metadatas") or [None])

    return {"id": chunk_id, "text": docs[0], "metadata": metas[0]}


@router.put("/collections/{name}/chunks/{chunk_id}")
def chunks_update(name: str, chunk_id: str, payload: ChunkUpdate):
    """Update a stored chunk's text and/or metadata; reject empty payloads."""
    if payload.text is None and payload.metadata is None:
        raise HTTPException(status_code=400, detail="Nothing to update")

    col = get_collection(name)

    existing = col.get(ids=[chunk_id])
    if not (existing.get("ids") or []):
        raise HTTPException(status_code=404, detail="Chunk not found")

    current_text = (existing.get("documents") or [None])[0]
    current_meta = (existing.get("metadatas") or [None])[0] or {}

    new_text = payload.text if payload.text is not None else (current_text or "")
    new_meta = payload.metadata if payload.metadata is not None else current_meta
    new_meta = sanitize_metadata(new_meta)

    col.upsert(ids=[chunk_id], documents=[new_text], metadatas=[new_meta])
    return {"ok": True, "updated": chunk_id, "collection": name}


@router.delete("/collections/{name}/chunks/{chunk_id}")
def chunks_delete(name: str, chunk_id: str):
    """Remove a chunk from a collection by ID."""
    col = get_collection(name)
    col.delete(ids=[chunk_id])
    return {"ok": True, "deleted": chunk_id, "collection": name}


@router.get("/collections/{name}/chunks")
def chunks_list(name: str, limit: int = 25):
    """List up to `limit` chunks from a collection in insertion order."""
    col = get_collection(name)
    limit = max(1, min(int(limit), 200))
    got = col.get(limit=limit)

    ids = got.get("ids") or []
    docs = got.get("documents") or []
    metas = got.get("metadatas") or []

    out = []
    for i, chunk_id in enumerate(ids):
        out.append({
            "id": chunk_id,
            "text": docs[i] if i < len(docs) else None,
            "metadata": metas[i] if i < len(metas) else None
        })
    return {"collection": name, "count": len(out), "items": out}


# ---------------- Query ----------------

@router.post("/collections/{name}/query", response_model=List[QueryHit])
def chunks_query(name: str, payload: QueryRequest):
    """Perform a semantic query with optional metadata filters against a collection."""
    col = get_collection(name)

    where = payload.where

    where = _apply_in_filter(where, "chunk_kind", payload.chunk_kinds)
    where = _apply_in_filter(where, "thing_type", payload.thing_types)
    if payload.thing_id:
        where = _merge_where(where, {"thing_id": payload.thing_id})
    if payload.tags:
        where = _apply_in_filter(where, "tags", payload.tags)

    res = col.query(
        query_texts=[payload.query_text],
        n_results=payload.n_results,
        where=where,
    )

    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]

    hits: List[QueryHit] = []
    for i, doc_id in enumerate(ids):
        hits.append({
            "id": doc_id,
            "text": docs[i] if i < len(docs) else None,
            "metadata": metas[i] if i < len(metas) else None,
            "distance": dists[i] if i < len(dists) else None,
        })
    return hits


# ---------------- Ingest ----------------

@router.post("/ingest")
def ingest_api(payload: Dict[str, Any]):
    """Legacy ingestion endpoint that delegates to the OpenIP pipeline and stores results."""
    collection = payload.get("collection")
    text = payload.get("text") or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    source_file = payload.get("source_file")
    source_section = payload.get("source_section")

    result = ingest_text(text=text, collection=collection, source_file=source_file, source_section=source_section)

    stored_things = [upsert_thing(t) for t in result["things"]]
    stored_connections = [upsert_connection(c) for c in result["connections"]]

    chunks = result["chunks"]
    if collection and chunks:
        chunks_payload = ChunksUpsert(chunks=chunks)
        chunks_upsert(collection, chunks_payload)

    return {
        "ok": True,
        "things": [t.model_dump(mode="json") for t in stored_things],
        "connections": [c.model_dump(mode="json") for c in stored_connections],
        "chunks": [c.model_dump(mode="json") for c in chunks],
    }


# ---------------- Chunking ----------------

@router.post("/chunking/upload")
async def chunking_upload(
    files: List[UploadFile] = File(...),
    doc_id_prefix: Optional[str] = Form(default=None),
    collection: Optional[str] = Form(default=None),
    min_chars: Optional[str] = Form(default=None),
    target_chars: Optional[str] = Form(default=None),
    max_chars: Optional[str] = Form(default=None),
    overlap: Optional[str] = Form(default=None),
):
    """Upload one or more files and run chunk detection with optional parameter overrides."""
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required.")

    overrides: Dict[str, int] = {}
    for key, value in {
        "min_chars": _coerce_int(min_chars),
        "target_chars": _coerce_int(target_chars),
        "max_chars": _coerce_int(max_chars),
        "overlap": _coerce_int(overlap),
    }.items():
        if value is not None:
            overrides[key] = value

    existing_ids = {doc.get("doc_id") for doc in list_docs(limit=10000) if doc.get("doc_id")}
    results: List[Dict[str, Any]] = []
    primary_doc_id: Optional[str] = None

    for f in files:
        data = await f.read()
        upload_meta = save_upload(f, data)
        size_bytes = len(data) if data is not None else None
        text = extract_text_from_bytes(data)

        if not text.strip():
            results.append({
                "file": describe_upload(upload_meta, size_bytes),
                "error": "File is empty or unreadable",
            })
            continue

        filename_slug = slugify(os.path.splitext(upload_meta.get("filename") or "doc")[0])
        preferred_id = "-".join(filter(None, [doc_id_prefix, filename_slug])) if doc_id_prefix else filename_slug
        source_hint = {
            "filename": upload_meta.get("filename"),
            "file_id": upload_meta.get("file_id"),
            "url": upload_meta.get("url"),
        }
        doc_id = derive_doc_id(
            explicit_doc_id=preferred_id,
            source=source_hint,
            text=text,
            collection=collection or "chunking",
        )
        if doc_id in existing_ids:
            doc_id = f"{doc_id}-{upload_meta.get('file_id', '')[:8]}"
        existing_ids.add(doc_id)

        try:
            detection = detect_or_reuse_chunks(
                doc_id=doc_id,
                text=text,
                detection_overrides=overrides or None,
                filename=upload_meta.get("filename"),
                url=upload_meta.get("url"),
            )
        except Exception as exc:
            results.append({
                "file": describe_upload(upload_meta, size_bytes),
                "error": str(exc),
            })
            continue

        chunk_count = len(detection.get("chunks") or [])
        entry = {
            "file": describe_upload(upload_meta, size_bytes),
            "doc_id": detection.get("doc_id"),
            "chunk_state": {
                "doc_id": detection.get("doc_id"),
                "version": detection.get("version"),
                "finalized": detection.get("finalized"),
                "reused": detection.get("reused"),
            },
            "version": detection.get("version"),
            "finalized": detection.get("finalized"),
            "chunk_count": chunk_count,
            "text_length": len(text),
            "filename": upload_meta.get("filename"),
            "url": upload_meta.get("url"),
        }
        results.append(entry)
        if not primary_doc_id:
            primary_doc_id = detection.get("doc_id")

    return {
        "ok": True,
        "docs": results,
        "primary_doc_id": primary_doc_id,
    }


@router.post("/chunking/detect")
def chunking_detect(payload: ChunkDetectionRequest, persist: bool = Query(default=False, description="Store detected chunks as a draft")):
    """Detect chunks for provided text; optionally persist the draft state to disk."""
    chunks = detect_chunks(payload)
    if persist:
        version, finalized = store_chunks(payload.doc_id, chunks, finalized=False, text=payload.text)
        return {
            "doc_id": payload.doc_id,
            "version": version,
            "finalized": finalized,
            "chunks": chunks,
            "persisted": True,
        }

    version = max((getattr(c, "version", None) or 0) for c in chunks) if chunks else 1
    return {
        "doc_id": payload.doc_id,
        "version": max(1, version),
        "finalized": False,
        "chunks": chunks,
        "persisted": False,
    }


@router.post("/chunking/finalize")
def chunking_finalize(payload: ChunkFinalizeRequest):
    """Persist finalized chunk sets for a document, ensuring doc_id consistency."""
    if any(c.doc_id != payload.doc_id for c in payload.chunks):
        raise HTTPException(status_code=400, detail="All chunks must share the doc_id provided.")
    version, finalized = store_chunks(payload.doc_id, payload.chunks, finalized=payload.finalized, text=payload.text)
    return {
        "ok": True,
        "doc_id": payload.doc_id,
        "version": version,
        "finalized": finalized,
        "count": len(payload.chunks),
    }


@router.get("/chunking/documents/{doc_id}")
def chunking_document(doc_id: str):
    """Fetch chunk state for a specific document by ID."""
    doc = get_chunks(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/chunking/documents")
def chunking_document_list(limit: int = 100):
    """List stored chunk documents ordered by most recent update."""
    return list_docs(limit=limit)


# -----------------------------------------------------------------------------
# Back-compat routes: keep /documents working by delegating to /chunks.
# -----------------------------------------------------------------------------

@router.post("/collections/{name}/documents")
def documents_upsert(name: str, payload: ChunksUpsert):
    """Back-compat: delegate document upsert calls to chunk storage."""
    return chunks_upsert(name, payload)

@router.get("/collections/{name}/documents/{doc_id}", response_model=ChunkOut)
def documents_get(name: str, doc_id: str):
    """Back-compat: fetch a document via the chunk retrieval endpoint."""
    return chunks_get(name, doc_id)

@router.put("/collections/{name}/documents/{doc_id}")
def documents_update(name: str, doc_id: str, payload: ChunkUpdate):
    """Back-compat: update a document using the chunk update logic."""
    return chunks_update(name, doc_id, payload)

@router.delete("/collections/{name}/documents/{doc_id}")
def documents_delete(name: str, doc_id: str):
    """Back-compat: delete a document by delegating to chunk deletion."""
    return chunks_delete(name, doc_id)

@router.get("/collections/{name}/documents")
def documents_list(name: str, limit: int = 25):
    """Back-compat: list documents via the chunk listing endpoint."""
    return chunks_list(name, limit=limit)
