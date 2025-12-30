"""
Chunking utilities that operate independently of any web interface.
"""

from .core import chunk_document, default_boundary_score, hash_chunk_id, parse_blocks
from .orchestrator import annotate_chunks, detect_or_reuse_chunks, derive_doc_id, slugify
from .pipeline import detect_chunks

__all__ = [
    "annotate_chunks",
    "chunk_document",
    "default_boundary_score",
    "detect_chunks",
    "detect_or_reuse_chunks",
    "derive_doc_id",
    "hash_chunk_id",
    "parse_blocks",
    "slugify",
]
