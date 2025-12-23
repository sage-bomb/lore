"""
Use OpenAI to extract lore elements from a document and upsert them into the demo stores.

Example:
  python scripts/openai_ingest.py --file sample.txt --collection demo_lore
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.chroma_store import get_collection
from app.library_store import list_connections, list_things, upsert_connection, upsert_thing
from app.schemas import Connection, Thing

try:
    from openai import OpenAI
except ImportError as exc:
    raise SystemExit("openai package is required. Install with `pip install openai`.") from exc


MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))


def load_text(path: Path, inline_text: str | None) -> str:
    if inline_text:
        return inline_text
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_text(encoding="utf-8")


def build_prompt(doc_text: str) -> List[Dict[str, str]]:
    system = (
        "You extract structured lore data from a document. "
        "Return JSON only with keys: things, connections, chunks. "
        "Each thing must include: thing_id (slug-like), thing_type, name, summary (1-2 sentences), tags. "
        "Each connection must include: edge_id, src_id, dst_id, rel_type, note, tags. "
        "Each chunk must include: chunk_id, text, chunk_kind (e.g., thing_summary, connection_note), "
        "thing_id, thing_type, edge_id (optional), tags. "
        "Prefer concise IDs and avoid duplicates. "
        "Avoid redundant entries; merge duplicates when possible."
    )
    user = f"Document:\n{doc_text}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def call_openai(doc_text: str) -> Dict[str, Any]:
    client = OpenAI()
    messages = build_prompt(doc_text)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=TEMPERATURE,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from OpenAI")
    return data


def dedupe_things(things: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    existing_ids = {t.thing_id for t in list_things()}
    unique: Dict[str, Dict[str, Any]] = {}
    for t in things or []:
        tid = t.get("thing_id")
        if not tid or tid in existing_ids:
            continue
        unique[tid] = t
    return list(unique.values())


def dedupe_connections(conns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    existing_ids = {c.edge_id for c in list_connections()}
    unique: Dict[str, Dict[str, Any]] = {}
    for c in conns or []:
        cid = c.get("edge_id")
        if not cid or cid in existing_ids:
            continue
        unique[cid] = c
    return list(unique.values())


def normalize_chunks(chunks: List[Dict[str, Any]], collection_name: str) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    col = get_collection(collection_name)
    ids: List[str] = []
    texts: List[str] = []
    metas: List[Dict[str, Any]] = []

    for ch in chunks or []:
        cid = ch.get("chunk_id")
        text = ch.get("text")
        if not cid or not text:
            continue

        existing = col.get(ids=[cid])
        existing_ids = existing.get("ids") or []
        if existing_ids:
            continue

        md = {
            "chunk_kind": ch.get("chunk_kind") or "thing_summary",
            "thing_id": ch.get("thing_id"),
            "thing_type": ch.get("thing_type"),
            "edge_id": ch.get("edge_id"),
            "tags": ch.get("tags") or [],
        }
        ids.append(cid)
        texts.append(text)
        metas.append(md)

    return ids, texts, metas


def upsert_all(data: Dict[str, Any], collection_name: str) -> None:
    # Things
    new_things = dedupe_things(data.get("things") or [])
    for t in new_things:
        upsert_thing(Thing.model_validate(t))

    # Connections
    new_conns = dedupe_connections(data.get("connections") or [])
    for c in new_conns:
        upsert_connection(Connection.model_validate(c))

    # Chunks
    ids, texts, metas = normalize_chunks(data.get("chunks") or [], collection_name)
    if ids:
        col = get_collection(collection_name)
        col.upsert(ids=ids, documents=texts, metadatas=metas)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract lore data using OpenAI and upsert into the stores.")
    parser.add_argument("--file", type=Path, help="Path to a document file")
    parser.add_argument("--text", type=str, help="Inline document text")
    parser.add_argument("--collection", default="demo_lore", help="Chroma collection name to upsert chunks into")
    args = parser.parse_args()

    doc_text = load_text(args.file, args.text) if (args.file or args.text) else ""
    if not doc_text.strip():
        raise SystemExit("Provide --file or --text with content.")

    extracted = call_openai(doc_text)
    upsert_all(extracted, args.collection)
    print("Extraction complete.")
    print(json.dumps({
        "things_added": len(dedupe_things(extracted.get("things") or [])),
        "connections_added": len(dedupe_connections(extracted.get("connections") or [])),
        "chunks_added": len(normalize_chunks(extracted.get("chunks") or [], args.collection)[0]),
    }, indent=2))


if __name__ == "__main__":
    main()
