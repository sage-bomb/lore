import os
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")

_client = chromadb.PersistentClient(path=CHROMA_PATH)
_embed_fn = SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)

def client() -> chromadb.ClientAPI:
    return _client

def get_collection(name: str):
    # Chroma collections need the embedding_function supplied at access time
    return _client.get_or_create_collection(
        name=name,
        embedding_function=_embed_fn,
        metadata={"hnsw:space": "cosine"},
    )

def list_collection_names() -> list[str]:
    cols = _client.list_collections()
    # different chroma versions return objects with .name
    return sorted([c.name for c in cols])

