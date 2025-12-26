import hashlib
import importlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence

from app.chroma_store import _embed_fn
from app.schemas import ChunkDetectionRequest, ChunkMetadata

logger = logging.getLogger(__name__)


@dataclass
class ParsedBlock:
    text: str
    start_line: int
    end_line: int
    start_char: int
    end_char: int
    cues: set[str] = field(default_factory=set)
    leading_blank_lines: int = 0
    trailing_blank_lines: int = 0


def _classify_line(line: str) -> set[str]:
    cues: set[str] = set()
    stripped = line.strip()
    if stripped.startswith("#"):
        cues.add("heading")
    if re.match(r"^\s*[-*+]\s+", stripped) or re.match(r"^\s*\d+\.\s+", stripped):
        cues.add("list")
    if stripped.startswith(("```", "~~~")):
        cues.add("fence")
    if stripped.startswith(">"):  # block quotes
        cues.add("quote")
    return cues


def parse_blocks(text: str) -> List[ParsedBlock]:
    lines = text.splitlines(keepends=True)
    blocks: List[ParsedBlock] = []

    buf: List[str] = []
    cues: set[str] = set()
    start_line = 0
    start_char = 0
    blank_streak = 0
    char_cursor = 0

    for idx, raw_line in enumerate(lines):
        line_start = char_cursor
        char_cursor += len(raw_line)

        if not raw_line.strip():
            blank_streak += 1
            if buf:
                # End current block; trailing blanks counted on flush
                block_text = "".join(buf)
                end_char = line_start
                end_line = idx
                blocks.append(
                    ParsedBlock(
                        text=block_text,
                        start_line=start_line + 1,
                        end_line=end_line + 1,
                        start_char=start_char,
                        end_char=end_char,
                        cues=set(cues),
                        trailing_blank_lines=blank_streak,
                    )
                )
                buf = []
                cues = set()
            continue

        line_cues = _classify_line(raw_line)

        if not buf:
            start_line = idx
            start_char = line_start
            if blank_streak:
                cues.add("leading_blank")
            blank_streak = 0

        buf.append(raw_line)
        cues.update(line_cues)

    # Flush remainder
    if buf:
        block_text = "".join(buf)
        end_line = len(lines)
        blocks.append(
            ParsedBlock(
                text=block_text,
                start_line=start_line + 1,
                end_line=end_line,
                start_char=start_char,
                end_char=char_cursor,
                cues=set(cues),
                trailing_blank_lines=blank_streak,
            )
        )

    return blocks


def _cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    if vec_a is None or vec_b is None:
        return 0.0

    try:
        seq_a = list(vec_a)
        seq_b = list(vec_b)
    except TypeError:
        return 0.0

    if len(seq_a) == 0 or len(seq_b) == 0:
        return 0.0
    dot = sum(a * b for a, b in zip(seq_a, seq_b))
    norm_a = math.sqrt(sum(a * a for a in seq_a))
    norm_b = math.sqrt(sum(b * b for b in seq_b))
    denom = norm_a * norm_b
    if denom == 0:
        return 0.0
    return float(dot / denom)


def embed_blocks(blocks: Iterable[ParsedBlock]) -> List[List[float]]:
    texts = [b.text for b in blocks]
    if not texts:
        return []
    embeddings = _embed_fn(texts)
    # The embedding function may return numpy arrays; coerce to plain lists for
    # predictable truthiness/len behavior downstream.
    return [list(vec) for vec in embeddings]


def _boundary_score(left: ParsedBlock, right: ParsedBlock, similarity: float) -> tuple[float, List[str]]:
    reasons: List[str] = []
    structural_score = 0.0

    if "heading" in right.cues:
        structural_score += 0.4
        reasons.append("heading start")
    if left.trailing_blank_lines or "leading_blank" in right.cues:
        structural_score += 0.15
        reasons.append("blank line gap")
    if "fence" in left.cues or "fence" in right.cues:
        structural_score += 0.25
        reasons.append("code/quote fence")
    if ("list" in left.cues) != ("list" in right.cues):
        structural_score += 0.2
        reasons.append("list boundary")
    if ("quote" in left.cues) != ("quote" in right.cues):
        structural_score += 0.15
        reasons.append("quote boundary")

    structural_score = min(structural_score, 1.0)

    semantic_drop = max(0.0, 1.0 - similarity)
    if semantic_drop > 0.4:
        reasons.append(f"semantic drop {semantic_drop:.2f}")

    combined = min(1.0, 0.6 * semantic_drop + 0.4 * structural_score)
    return combined, reasons


def _hash_chunk_id(doc_id: str, start_char: int, end_char: int) -> str:
    payload = f"{doc_id}:{start_char}:{end_char}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def _make_chunk(
    doc_id: str,
    text: str,
    start_block: ParsedBlock,
    end_block: ParsedBlock,
    start_char: int,
    end_char: int,
    boundary_reasons: List[str],
    confidence: float,
    overlap: int,
    chunk_kind: str = "chapter_text",
    parent_chunk_id: str | None = None,
) -> ChunkMetadata:
    chunk_text = text[start_char:end_char]
    length_chars = len(chunk_text)
    length_lines = end_block.end_line - start_block.start_line + 1
    chunk_id = _hash_chunk_id(doc_id, start_char, end_char)
    return ChunkMetadata(
        doc_id=doc_id,
        chunk_id=chunk_id,
        text=chunk_text,
        start_char=start_char,
        end_char=end_char,
        start_line=start_block.start_line,
        end_line=end_block.end_line,
        length_chars=length_chars,
        length_lines=length_lines,
        boundary_reasons=boundary_reasons,
        confidence=round(confidence, 3),
        overlap=overlap,
        chunk_kind=chunk_kind,
        parent_chunk_id=parent_chunk_id,
    )


def _get_openai_client():
    spec = importlib.util.find_spec("openai")
    if spec is None:
        logger.warning("Chunk enhancer: openai package not installed; skipping enrichment")
        return None
    openai_mod = importlib.import_module("openai")
    return openai_mod.OpenAI()


def _build_enhancement_messages(doc_id: str, text: str, chunks: List[ChunkMetadata]) -> list[dict[str, str]]:
    condensed_chunks = []
    for ch in chunks:
        condensed_chunks.append(
            {
                "chunk_id": ch.chunk_id,
                "start_line": ch.start_line,
                "end_line": ch.end_line,
                "text": ch.text[:1200],
            }
        )
    system = (
        "You enhance chunked document segments with structured annotations.\n"
        "Return JSON with keys: chunks (list) and document_summary (object).\n"
        "Each item in chunks must include chunk_id (string), summary_title (short title), "
        "tags (list of short labels), and thing_type (string describing primary entity type or 'other').\n"
        "document_summary must include title (short heading), summary (2-4 sentences), and tags (list).\n"
        "Keep responses concise, avoid markdown, and do not invent chunk IDs."
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                {"doc_id": doc_id, "document_preview": text[:2000], "chunks": condensed_chunks},
                ensure_ascii=False,
            ),
        },
    ]


def _enhance_with_openai(doc_id: str, text: str, chunks: List[ChunkMetadata]) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Any] | None]:
    if not chunks:
        return {}, None

    client = _get_openai_client()
    if client is None:
        return {}, None

    api_key_present = bool(os.getenv("OPENAI_API_KEY"))
    logger.info(
        "Chunk enhancer: sending %d chunk(s) to OpenAI (api_key_present=%s)",
        len(chunks),
        api_key_present,
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=_build_enhancement_messages(doc_id, text, chunks),
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
    except Exception:
        logger.exception("Chunk enhancer: OpenAI request failed; returning base chunks")
        return {}, None

    chunk_map: Dict[str, Dict[str, Any]] = {}
    for item in data.get("chunks") or []:
        chunk_id = item.get("chunk_id")
        if not chunk_id:
            continue
        chunk_map[chunk_id] = {
            "summary_title": item.get("summary_title"),
            "tags": [t for t in (item.get("tags") or []) if t],
            "thing_type": item.get("thing_type"),
        }

    doc_summary = data.get("document_summary") if isinstance(data.get("document_summary"), dict) else None
    logger.info(
        "Chunk enhancer: received annotations (chunks=%d, has_document_summary=%s)",
        len(chunk_map),
        bool(doc_summary),
    )
    return chunk_map, doc_summary


def _make_meta_chunk(doc_id: str, text: str, summary: Dict[str, Any]) -> ChunkMetadata:
    summary_text = summary.get("summary") or ""
    summary_title = summary.get("title") or "Document Summary"
    tags = [t for t in (summary.get("tags") or []) if t]
    chunk_id = f"{_hash_chunk_id(doc_id, 0, len(text))}-meta"
    return ChunkMetadata(
        doc_id=doc_id,
        chunk_id=chunk_id,
        text=summary_text,
        start_char=0,
        end_char=0,
        start_line=0,
        end_line=0,
        length_chars=len(summary_text),
        length_lines=max(1, summary_text.count("\n") + 1) if summary_text else 0,
        boundary_reasons=["document meta"],
        confidence=1.0,
        overlap=0,
        chunk_kind="document_meta",
        summary_title=summary_title,
        tags=tags,
        is_meta_chunk=True,
    )


def detect_chunks(payload: ChunkDetectionRequest) -> List[ChunkMetadata]:
    text = payload.text or ""
    if not text.strip():
        return []

    blocks = parse_blocks(text)
    if not blocks:
        return []

    embeddings = embed_blocks(blocks)
    boundary_scores: List[tuple[float, List[str]]] = []

    num_embeddings = len(embeddings)

    for i in range(len(blocks) - 1):
        sim = _cosine_similarity(embeddings[i], embeddings[i + 1]) if i + 1 < num_embeddings else 0.0
        score, reasons = _boundary_score(blocks[i], blocks[i + 1], sim)
        boundary_scores.append((score, reasons))

    min_chars = max(1, payload.min_chars)
    target_chars = max(min_chars, payload.target_chars)
    max_chars = max(target_chars, payload.max_chars)
    overlap = max(0, payload.overlap)

    chunks: List[ChunkMetadata] = []
    start_idx = 0
    start_char = blocks[0].start_char

    i = 0
    while i < len(blocks):
        if i == len(blocks) - 1:
            # Always close on the final block
            chunk = _make_chunk(
                payload.doc_id,
                text,
                blocks[start_idx],
                blocks[i],
                start_char,
                blocks[i].end_char,
                boundary_reasons=["document end"],
                confidence=1.0,
                overlap=overlap,
            )
            chunks.append(chunk)
            break

        score, reasons = boundary_scores[i]
        projected_end = blocks[i].end_char
        current_length = projected_end - start_char
        next_length = blocks[i + 1].end_char - start_char

        must_split = next_length > max_chars
        can_split = must_split or (current_length >= min_chars and (current_length >= target_chars or score >= 0.55))

        if can_split:
            chunk = _make_chunk(
                payload.doc_id,
                text,
                blocks[start_idx],
                blocks[i],
                start_char,
                projected_end,
                boundary_reasons=reasons or ["size target"],
                confidence=max(score, 0.35),
                overlap=overlap,
                chunk_kind="chapter_text",
            )
            chunks.append(chunk)

            if overlap:
                next_start_char = max(0, projected_end - overlap)
                next_start_idx = start_idx
                while next_start_idx < len(blocks) and blocks[next_start_idx].end_char <= next_start_char:
                    next_start_idx += 1
                start_idx = min(next_start_idx, len(blocks) - 1)
                start_char = max(blocks[start_idx].start_char, next_start_char)
            else:
                start_idx = i + 1
                start_char = blocks[start_idx].start_char
        i += 1

    enhancement_map, doc_summary = _enhance_with_openai(payload.doc_id, text, chunks)
    meta_chunk: ChunkMetadata | None = None

    if doc_summary:
        meta_chunk = _make_meta_chunk(payload.doc_id, text, doc_summary)
        child_ids: List[str] = []
        for ch in chunks:
            ch.parent_chunk_id = meta_chunk.chunk_id
            child_ids.append(ch.chunk_id)
        meta_chunk.child_chunk_ids = child_ids

    for ch in chunks:
        update = enhancement_map.get(ch.chunk_id, {})
        if update.get("summary_title"):
            ch.summary_title = update["summary_title"]
        if update.get("tags"):
            ch.tags = update["tags"]
        if update.get("thing_type"):
            ch.thing_type = update["thing_type"]

    if meta_chunk:
        return [meta_chunk, *chunks]
    return chunks
