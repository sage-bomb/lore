"""
Seed a demo lore collection with sample things, connections, and chunks.

Usage:
  python scripts/create_demo_dataset.py --name demo_lore
"""
import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone

# Ensure repo root on path when invoked as a script (python scripts/create_demo_dataset.py ...)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.collections import client, get_collection
from app.domain.library import upsert_connection, upsert_thing
from app.schemas import Connection, Thing


def normalize_metadata(md: dict) -> dict:
    """Chroma versions can reject list metadata; convert lists to comma strings."""
    normalized: dict = {}
    for key, val in md.items():
        if val is None:
            continue
        if isinstance(val, list):
            normalized[key] = ", ".join(str(v) for v in val)
        else:
            normalized[key] = val
    return normalized


def seed_library() -> None:
    things = [
        Thing(
            thing_id="character.sahla",
            thing_type="character",
            name="Sahla Nareth",
            aliases=["Sahla of the Tides"],
            summary="Exiled navigator who hears tidesong magic.",
            description="Steers skyships with whispered star maps; trusts few.",
            tags=["navigator", "protagonist", "magic"],
        ),
        Thing(
            thing_id="place.kaar",
            thing_type="place",
            name="Kaar Archipelago",
            summary="Shattered islands connected by storm-bridges.",
            description="Home to tide temples and secret coves for smugglers.",
            tags=["archipelago", "storm"],
        ),
        Thing(
            thing_id="artifact.compass",
            thing_type="artifact",
            name="Aster Compass",
            summary="Broken compass that still points toward forbidden routes.",
            description="Rumored to bend toward memories instead of north.",
            tags=["artifact", "navigation"],
        ),
    ]

    for thing in things:
        upsert_thing(thing)

    upsert_connection(
        Connection(
            edge_id="edge.sahla.kaar.origin",
            src_id="character.sahla",
            dst_id="place.kaar",
            rel_type="origin_of",
            note="Sahla was born among the Kaar storm-bridges.",
            tags=["backstory", "home"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )


def seed_collection(name: str) -> None:
    # reset collection for a clean demo
    existing = {c.name for c in client().list_collections()}
    if name in existing:
        client().delete_collection(name)

    col = get_collection(name)

    texts = [
        "Sahla charts passage by reading tide-music that only she can hear.",
        "The Kaar Archipelago is stitched together by bridges that appear during storms.",
        "The Aster Compass is drawn toward intense memories, guiding its holder to pivotal scenes.",
        "Sahla feels bound to return to Kaar whenever the compass hums with stormlight.",
    ]
    ids = [
        "chunk.character.sahla.summary",
        "chunk.place.kaar.summary",
        "chunk.artifact.compass.summary",
        "chunk.connection.sahla.kaar.note",
    ]
    raw_metadatas = [
        {"chunk_kind": "thing_summary", "thing_id": "character.sahla", "thing_type": "character", "tags": ["navigator", "magic"]},
        {"chunk_kind": "thing_summary", "thing_id": "place.kaar", "thing_type": "place", "tags": ["storm", "archipelago"]},
        {"chunk_kind": "thing_summary", "thing_id": "artifact.compass", "thing_type": "artifact", "tags": ["navigation", "mystery"]},
        {
            "chunk_kind": "connection_note",
            "thing_id": "character.sahla",
            "edge_id": "edge.sahla.kaar.origin",
            "tags": ["backstory", "home"],
        },
    ]

    metadatas = [normalize_metadata(md) for md in raw_metadatas]
    col.upsert(ids=ids, documents=texts, metadatas=metadatas)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a demo lore collection.")
    parser.add_argument("--name", default="demo_lore", help="Name of the collection to seed.")
    args = parser.parse_args()

    seed_library()
    seed_collection(args.name)
    print(f"Seeded demo dataset into collection '{args.name}'.")


if __name__ == "__main__":
    main()
