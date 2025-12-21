from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.chroma_store import client, get_collection, list_collection_names
from app.library_store import (
    delete_connection, delete_thing,
    get_connection, get_thing,
    list_connections, list_things,
    upsert_connection, upsert_thing,
)
from app.schemas import (
    ChunkOut,
    ChunkUpdate,
    ChunksUpsert,
    CollectionCreate,
    CollectionInfo,
    Connection,
    QueryHit,
    QueryRequest,
    Thing,
)

router = APIRouter(prefix="/api", tags=["api"])


# ---------------- Helpers ----------------

def _merge_where(base: Optional[Dict[str, Any]], extra: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Merge two Chroma 'where' clauses using $and."""
    if base and extra:
        return {"$and": [base, extra]}
    return base or extra


def _apply_in_filter(where: Optional[Dict[str, Any]], field: str, values: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    if not values:
        return where
    if len(values) == 1:
        clause: Dict[str, Any] = {field: values[0]}
    else:
        clause = {field: {"$in": values}}
    return _merge_where(where, clause)


# ---------------- Collections CRUD ----------------

@router.get("/collections", response_model=List[CollectionInfo])
def collections_list():
    return [{"name": n} for n in list_collection_names()]


@router.post("/collections", response_model=CollectionInfo)
def collections_create(payload: CollectionCreate):
    col = get_collection(payload.name)
    return {"name": col.name}


@router.get("/collections/{name}", response_model=CollectionInfo)
def collections_get(name: str):
    existing = set(list_collection_names())
    if name not in existing:
        raise HTTPException(status_code=404, detail="Collection not found")
    return {"name": name}


@router.delete("/collections/{name}")
def collections_delete(name: str):
    existing = set(list_collection_names())
    if name not in existing:
        raise HTTPException(status_code=404, detail="Collection not found")
    client().delete_collection(name=name)
    return {"ok": True, "deleted": name}


# ---------------- Things ----------------

@router.post("/things", response_model=Thing)
def things_upsert(payload: Thing):
    stored = upsert_thing(payload)
    return stored


@router.get("/things/{thing_id}", response_model=Thing)
def things_get(thing_id: str):
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
    return list_things(thing_type=thing_type, tag=tag, q=q)


@router.delete("/things/{thing_id}")
def things_delete(thing_id: str):
    removed = delete_thing(thing_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Thing not found")
    return {"ok": True, "deleted": thing_id}


# ---------------- Connections ----------------

@router.post("/connections", response_model=Connection)
def connections_upsert(payload: Connection):
    stored = upsert_connection(payload)
    return stored


@router.get("/connections/{edge_id}", response_model=Connection)
def connections_get(edge_id: str):
    got = get_connection(edge_id)
    if not got:
        raise HTTPException(status_code=404, detail="Connection not found")
    return got


@router.get("/connections", response_model=List[Connection])
def connections_list(
    thing_id: Optional[str] = Query(default=None, description="Return connections involving the thing_id"),
):
    return list_connections(thing_id=thing_id)


@router.delete("/connections/{edge_id}")
def connections_delete(edge_id: str):
    removed = delete_connection(edge_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"ok": True, "deleted": edge_id}


# ---------------- Chunks CRUD (preferred) ----------------

@router.post("/collections/{name}/chunks")
def chunks_upsert(name: str, payload: ChunksUpsert):
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

    col.upsert(ids=ids, documents=docs, metadatas=metas)
    return {"ok": True, "upserted": len(ids), "collection": name}


@router.get("/collections/{name}/chunks/{chunk_id}", response_model=ChunkOut)
def chunks_get(name: str, chunk_id: str):
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

    col.upsert(ids=[chunk_id], documents=[new_text], metadatas=[new_meta])
    return {"ok": True, "updated": chunk_id, "collection": name}


@router.delete("/collections/{name}/chunks/{chunk_id}")
def chunks_delete(name: str, chunk_id: str):
    col = get_collection(name)
    col.delete(ids=[chunk_id])
    return {"ok": True, "deleted": chunk_id, "collection": name}


@router.get("/collections/{name}/chunks")
def chunks_list(name: str, limit: int = 25):
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


# -----------------------------------------------------------------------------
# Back-compat routes: keep /documents working by delegating to /chunks.
# -----------------------------------------------------------------------------

@router.post("/collections/{name}/documents")
def documents_upsert(name: str, payload: ChunksUpsert):
    return chunks_upsert(name, payload)

@router.get("/collections/{name}/documents/{doc_id}", response_model=ChunkOut)
def documents_get(name: str, doc_id: str):
    return chunks_get(name, doc_id)

@router.put("/collections/{name}/documents/{doc_id}")
def documents_update(name: str, doc_id: str, payload: ChunkUpdate):
    return chunks_update(name, doc_id, payload)

@router.delete("/collections/{name}/documents/{doc_id}")
def documents_delete(name: str, doc_id: str):
    return chunks_delete(name, doc_id)

@router.get("/collections/{name}/documents")
def documents_list(name: str, limit: int = 25):
    return chunks_list(name, limit=limit)
