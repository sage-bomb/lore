from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field, ConfigDict

JsonDict = Dict[str, Any]

# ---------------- Collections ----------------

class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)

class CollectionInfo(BaseModel):
    name: str


# =============================================================================
# Library models (world entities + relationships)
# =============================================================================

ThingType = Literal[
    "character", "place", "faction", "culture", "race", "deity",
    "artifact", "creature", "magic_concept", "ritual", "currency",
    "term", "event", "chapter", "scene", "lore_entry",
    "style_rule", "other"
]

class Thing(BaseModel):
    thing_id: str = Field(min_length=1, description="Stable ID, e.g. character.sahla")
    thing_type: ThingType
    name: str
    aliases: List[str] = Field(default_factory=list)
    summary: Optional[str] = Field(default=None, description="1-3 sentences")
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    data: Dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    supersedes: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Connection(BaseModel):
    edge_id: str
    src_id: str
    dst_id: str
    rel_type: str = Field(min_length=1, description="Relationship type label")
    note: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Chroma index models (SearchChunks)
# =============================================================================

ChunkKind = Literal[
    "thing_summary", "thing_notes", "connection_note",
    "chapter_text", "scene_text", "rule_text", "misc"
]

class SearchChunk(BaseModel):
    """One embedding unit stored in Chroma. Keep metadata flat and filter-friendly."""
    model_config = ConfigDict(extra="allow")

    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)

    chunk_kind: ChunkKind = "thing_summary"
    thing_id: Optional[str] = None
    thing_type: Optional[str] = None
    edge_id: Optional[str] = None
    chapter_number: Optional[int] = None
    scene_id: Optional[str] = None
    pov: Optional[str] = None
    location_id: Optional[str] = None
    entity_ids: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    source_file: Optional[str] = None
    source_section: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


class ChunksUpsert(BaseModel):
    chunks: List[SearchChunk]


class ChunkOut(BaseModel):
    id: str
    text: Optional[str] = None
    metadata: Optional[JsonDict] = None


class ChunkUpdate(BaseModel):
    text: Optional[str] = None
    metadata: Optional[JsonDict] = None


class QueryRequest(BaseModel):
    query_text: str = Field(min_length=1)
    n_results: int = Field(default=8, ge=1, le=50)
    where: Optional[JsonDict] = None
    chunk_kinds: Optional[List[ChunkKind]] = None
    thing_types: Optional[List[str]] = None
    thing_id: Optional[str] = None
    tags: Optional[List[str]] = None


class QueryHit(BaseModel):
    id: str
    text: Optional[str] = None
    metadata: Optional[JsonDict] = None
    distance: Optional[float] = None
