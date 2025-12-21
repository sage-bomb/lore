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
# Canon models (your "truth store")
# These objects are *not* required to live in Chroma; they can be stored in JSON,
# SQLite/Postgres, etc. Chroma can index derived "chunks" for retrieval.
# =============================================================================

RecordType = Literal[
    "character", "place", "faction", "culture", "race",
    "deity", "item", "artifact", "creature", "magic_concept",
    "ritual", "currency", "term", "event",
    "chapter", "scene", "style_rule", "lore_entry"
]

CanonStatus = Literal["draft", "canon", "deprecated", "disputed"]

class SourceRef(BaseModel):
    """Provenance pointer back to your raw files."""
    model_config = ConfigDict(extra="forbid")
    source_file: str
    source_section: Optional[str] = None
    locator: Optional[str] = None
    quoted_text: Optional[str] = None


class CanonClaim(BaseModel):
    """A single factual claim with provenance and confidence."""
    model_config = ConfigDict(extra="forbid")
    claim: str

    # Optional structure (lets you evolve into continuity checks later)
    subject_id: Optional[str] = None         # record_id
    predicate: Optional[str] = None          # e.g. "born_in", "rules", "has_symbol"
    object_id: Optional[str] = None          # record_id
    object_value: Optional[str] = None       # literal value

    sources: List[SourceRef] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    status: CanonStatus = "draft"
    notes: Optional[str] = None


class CanonRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.1"

    record_type: RecordType
    record_id: str = Field(min_length=1)     # stable, e.g. "place.valtara"
    name: str = Field(min_length=1)
    aliases: List[str] = Field(default_factory=list)

    summary: Optional[str] = None            # 1-3 sentences (great for embedding)
    description: Optional[str] = None        # richer prose

    tags: List[str] = Field(default_factory=list)
    related_ids: List[str] = Field(default_factory=list)

    # Record-level lifecycle/versioning
    status: CanonStatus = "draft"
    version: int = Field(default=1, ge=1)
    supersedes: Optional[str] = None         # record_id of previous version (if any)

    claims: List[CanonClaim] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RelationshipEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edge_id: str
    src_id: str
    dst_id: str

    # Keep flexible early on. If you prefer strict enums, swap to Literal[...] later.
    rel_type: str = Field(min_length=1)

    strength: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    summary: Optional[str] = None
    sources: List[SourceRef] = Field(default_factory=list)
    status: CanonStatus = "draft"


# ---------------- Typed records (examples) ----------------

class CharacterData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    age_range: Optional[str] = None
    appearance: List[str] = Field(default_factory=list)
    personality: List[str] = Field(default_factory=list)
    abilities: List[str] = Field(default_factory=list)
    flaws: List[str] = Field(default_factory=list)
    affiliations: List[str] = Field(default_factory=list)  # record_ids
    motifs: List[str] = Field(default_factory=list)
    arc_notes: Optional[str] = None

class CharacterRecord(CanonRecord):
    record_type: Literal["character"] = "character"
    data: CharacterData = Field(default_factory=CharacterData)


class PlaceData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    place_type: Literal["city", "region", "road", "ruin", "forest", "mountain", "sea", "shrine", "village"] = "region"
    region_id: Optional[str] = None
    climate: Optional[str] = None
    terrain: List[str] = Field(default_factory=list)
    governance: Optional[str] = None
    economy: List[str] = Field(default_factory=list)
    law: List[str] = Field(default_factory=list)
    factions_present: List[str] = Field(default_factory=list)
    notable_events: List[str] = Field(default_factory=list)

class PlaceRecord(CanonRecord):
    record_type: Literal["place"] = "place"
    data: PlaceData = Field(default_factory=PlaceData)


# =============================================================================
# Chroma index models (what you actually upsert/query against in the vector DB)
# =============================================================================

DocKind = Literal["record_chunk", "chapter_chunk", "rule_chunk", "edge_chunk"]

class ChromaChunk(BaseModel):
    """One embedding unit stored in Chroma. Keep metadata flat and filter-friendly."""
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)

    # flat metadata (filterable)
    doc_kind: DocKind = "record_chunk"
    record_type: RecordType
    record_id: str

    canon_status: CanonStatus = "draft"

    source_file: Optional[str] = None
    source_section: Optional[str] = None

    # filters commonly useful for manuscript retrieval
    chapter_number: Optional[int] = None
    pov: Optional[str] = None
    location_id: Optional[str] = None

    # NOTE: list filtering support varies by Chroma version. Keep for now, but if
    # where-filters can't do "contains", you may need alternate strategy.
    entity_ids: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)

    # Optional: any extra small flat fields
    extra: Optional[JsonDict] = None


class ChunksUpsert(BaseModel):
    chunks: List[ChromaChunk]


class ChunkOut(BaseModel):
    id: str
    text: Optional[str] = None
    metadata: Optional[JsonDict] = None


class ChunkUpdate(BaseModel):
    text: Optional[str] = None
    metadata: Optional[JsonDict] = None


class QueryRequest(BaseModel):
    query_text: str = Field(min_length=1)
    n_results: int = Field(default=10, ge=1, le=50)

    # Raw Chroma where clause (advanced)
    where: Optional[JsonDict] = None

    # Convenience filters (we'll merge these into the where clause server-side)
    doc_kinds: Optional[List[DocKind]] = None
    canon_only: bool = False


class QueryHit(BaseModel):
    id: str
    text: Optional[str] = None
    metadata: Optional[JsonDict] = None
    distance: Optional[float] = None
