import json
import os
import re
from typing import Any, Dict, List

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")

_client = chromadb.PersistentClient(path=CHROMA_PATH)
_embed_fn = SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{1,510}[A-Za-z0-9])?$")
_ALLOWED_META_TYPES = (str, int, float, bool, bytes, bytearray, type(None))

def client() -> chromadb.ClientAPI:
    return _client

def embedding_function() -> SentenceTransformerEmbeddingFunction:
    """
    Expose the configured embedding function so chunking logic can depend on the
    data layer without importing web-facing modules.
    """
    return _embed_fn

def normalize_collection_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise ValueError("Collection name cannot be empty.")
    name = name.lower().replace(" ", "_")
    name = re.sub(r"[^a-z0-9._-]+", "_", name)
    name = name.strip("._-")
    if len(name) < 3 or len(name) > 512 or not _NAME_PATTERN.match(name):
        raise ValueError("Collection name must be 3-512 chars of a-z, 0-9, . _ -, start/end alphanumeric.")
    return name

def get_collection(name: str):
    safe = normalize_collection_name(name)
    # Chroma collections need the embedding_function supplied at access time
    return _client.get_or_create_collection(
        name=safe,
        embedding_function=_embed_fn,
        metadata={"hnsw:space": "cosine"},
    )

def list_collection_names() -> list[str]:
    cols = _client.list_collections()
    # different chroma versions return objects with .name
    return sorted([c.name for c in cols])


def sanitize_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce metadata values to Chroma-safe primitives."""
    if not meta:
        return {}

    sanitized: Dict[str, Any] = {}
    for key, raw_val in meta.items():
        if key is None:
            continue
        key_str = str(key)
        val = raw_val
        if isinstance(raw_val, _ALLOWED_META_TYPES):
            sanitized[key_str] = raw_val
            continue
        if isinstance(raw_val, (list, tuple, set)):
            val = ", ".join(str(x) for x in raw_val)
        elif isinstance(raw_val, dict):
            val = json.dumps(raw_val, ensure_ascii=False)
        else:
            val = str(raw_val)
        sanitized[key_str] = val
    return sanitized


def sanitize_metadatas(metas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [sanitize_metadata(m) for m in metas]
