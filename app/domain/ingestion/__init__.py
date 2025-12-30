"""
Document ingestion pipelines that convert raw text into stored data structures.
"""

from .openai_ingest import ingest_lore_from_text
from .pipeline import ingest_text

__all__ = [
    "ingest_lore_from_text",
    "ingest_text",
]
