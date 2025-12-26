import json
import os
from typing import Dict, Optional, Tuple

from app.schemas import ChunkMetadata

CHUNK_STORE_PATH = os.getenv("CHUNK_STORE_PATH", "./chunks.json")


def _default_state() -> Dict[str, dict]:
    return {"docs": {}}


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def load_chunk_store() -> Dict[str, dict]:
    if not os.path.exists(CHUNK_STORE_PATH):
        return _default_state()
    try:
        with open(CHUNK_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return _default_state()


def save_chunk_store(data: Dict[str, dict]) -> None:
    _ensure_parent_dir(CHUNK_STORE_PATH)
    with open(CHUNK_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def store_chunks(
    doc_id: str,
    chunks: list[ChunkMetadata],
    finalized: bool = False,
    text: Optional[str] = None,
) -> Tuple[int, bool]:
    data = load_chunk_store()
    docs = data.setdefault("docs", {})
    existing = docs.get(doc_id, {})
    version = int(existing.get("version", 0)) + 1
    stored_chunks = [c.model_copy(update={"version": version, "finalized": finalized}) for c in chunks]
    docs[doc_id] = {
        "version": version,
        "finalized": finalized,
        "text": text if text is not None else existing.get("text"),
        "chunks": [c.model_dump(mode="json") for c in stored_chunks],
    }
    save_chunk_store(data)
    return version, finalized


def get_chunks(doc_id: str) -> Optional[dict]:
    data = load_chunk_store()
    doc = data.get("docs", {}).get(doc_id)
    if not doc:
        return None
    return {
        "doc_id": doc_id,
        "version": int(doc.get("version", 1)),
        "finalized": bool(doc.get("finalized", False)),
        "text": doc.get("text"),
        "chunks": [ChunkMetadata.model_validate(c) for c in doc.get("chunks", [])],
    }


def list_docs(limit: int = 100) -> list[dict]:
    data = load_chunk_store()
    docs = data.get("docs", {})
    items: list[dict] = []
    for doc_id, payload in docs.items():
        chunks = payload.get("chunks") or []
        text = payload.get("text") or ""
        items.append({
            "doc_id": doc_id,
            "version": int(payload.get("version", 1)),
            "finalized": bool(payload.get("finalized", False)),
            "chunk_count": len(chunks),
            "text_length": len(text),
        })
    items.sort(key=lambda x: x.get("doc_id", ""))
    return items[:max(1, limit)]
