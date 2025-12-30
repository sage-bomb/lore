import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.schemas import Thing, Connection

LIBRARY_PATH = os.getenv("LIBRARY_PATH", "./library.json")


def _default_state() -> Dict[str, Dict[str, dict]]:
    return {"things": {}, "connections": {}}


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def load_library() -> Dict[str, Dict[str, dict]]:
    if not os.path.exists(LIBRARY_PATH):
        return _default_state()
    try:
        with open(LIBRARY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Minimal resilience: fall back to empty if corrupted/unreadable
        return _default_state()


def save_library(data: Dict[str, Dict[str, dict]]) -> None:
    _ensure_parent_dir(LIBRARY_PATH)
    with open(LIBRARY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------- Things ----------------

def upsert_thing(thing: Thing) -> Thing:
    data = load_library()
    existing_raw = data.get("things", {}).get(thing.thing_id)
    existing = Thing.model_validate(existing_raw) if existing_raw else None

    created_at = existing.created_at if existing else thing.created_at
    updated = thing.model_copy(update={"created_at": created_at, "updated_at": datetime.now(timezone.utc)})

    data.setdefault("things", {})[updated.thing_id] = updated.model_dump(mode="json")
    save_library(data)
    return updated


def get_thing(thing_id: str) -> Optional[Thing]:
    data = load_library()
    raw = data.get("things", {}).get(thing_id)
    if not raw:
        return None
    return Thing.model_validate(raw)


def list_things(thing_type: Optional[str] = None, tag: Optional[str] = None, q: Optional[str] = None) -> List[Thing]:
    data = load_library()
    things: List[Thing] = []
    for raw in data.get("things", {}).values():
        t = Thing.model_validate(raw)
        if thing_type and t.thing_type != thing_type:
            continue
        if tag and tag not in t.tags:
            continue
        if q:
            haystack = " ".join([t.name, " ".join(t.aliases), t.summary or "", t.description or ""]).lower()
            if q.lower() not in haystack:
                continue
        things.append(t)
    return things


def delete_thing(thing_id: str) -> bool:
    data = load_library()
    removed = bool(data.get("things", {}).pop(thing_id, None))
    if removed:
        save_library(data)
    return removed


# ---------------- Connections ----------------

def upsert_connection(edge: Connection) -> Connection:
    data = load_library()
    existing_raw = data.get("connections", {}).get(edge.edge_id)
    existing = Connection.model_validate(existing_raw) if existing_raw else None

    created_at = existing.created_at if existing else edge.created_at
    updated = edge.model_copy(update={"created_at": created_at, "updated_at": datetime.now(timezone.utc)})

    data.setdefault("connections", {})[updated.edge_id] = updated.model_dump(mode="json")
    save_library(data)
    return updated


def get_connection(edge_id: str) -> Optional[Connection]:
    data = load_library()
    raw = data.get("connections", {}).get(edge_id)
    if not raw:
        return None
    return Connection.model_validate(raw)


def list_connections(thing_id: Optional[str] = None) -> List[Connection]:
    data = load_library()
    edges: List[Connection] = []
    for raw in data.get("connections", {}).values():
        edge = Connection.model_validate(raw)
        if thing_id and thing_id not in (edge.src_id, edge.dst_id):
            continue
        edges.append(edge)
    return edges


def delete_connection(edge_id: str) -> bool:
    data = load_library()
    removed = bool(data.get("connections", {}).pop(edge_id, None))
    if removed:
        save_library(data)
    return removed
