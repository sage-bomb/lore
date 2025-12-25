"""
Use OpenAI to extract lore elements from a document and upsert them into the demo stores.

Example:
  python scripts/openai_ingest.py --file sample.txt --collection demo_lore
"""

import argparse
import json
from pathlib import Path

from app.services.openai_ingest import ingest_lore_from_text


def load_text(path: Path, inline_text: str | None) -> str:
    if inline_text:
        return inline_text
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_text(encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract lore data using OpenAI and upsert into the stores.")
    parser.add_argument("--file", type=Path, help="Path to a document file")
    parser.add_argument("--text", type=str, help="Inline document text")
    parser.add_argument("--collection", default="demo_lore", help="Chroma collection name to upsert chunks into")
    parser.add_argument("--doc-id", dest="doc_id", default=None, help="Optional stable document id")
    args = parser.parse_args()

    doc_text = load_text(args.file, args.text) if (args.file or args.text) else ""
    if not doc_text.strip():
        raise SystemExit("Provide --file or --text with content.")

    extracted = ingest_lore_from_text(
        doc_text,
        args.collection,
        doc_id=args.doc_id,
        persist_chunks=True,
    )
    print("Extraction complete.")
    print(json.dumps({
        "things_added": extracted["counts"]["things"],
        "connections_added": extracted["counts"]["connections"],
        "chunks_added": extracted["counts"]["chunks_embedded"],
    }, indent=2))


if __name__ == "__main__":
    main()
