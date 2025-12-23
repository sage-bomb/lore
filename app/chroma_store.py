import os
import re
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")

_client = chromadb.PersistentClient(path=CHROMA_PATH)
_embed_fn = SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{1,510}[A-Za-z0-9])?$")

def client() -> chromadb.ClientAPI:
    return _client

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
