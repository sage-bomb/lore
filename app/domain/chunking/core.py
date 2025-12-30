"""Core chunk segmentation primitives: block parsing, embedding, and boundary scoring."""

import hashlib
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Sequence

from app.schemas import ChunkMetadata

logger = logging.getLogger(__name__)


@dataclass
class ParsedBlock:
    """
    Represents a contiguous block of text produced by `parse_blocks`.

    Attributes:
        text: Raw text contained in the block (including newlines).
        start_line: 1-based line number where the block starts.
        end_line: 1-based line number where the block ends.
        start_char: 0-based character offset where the block starts.
        end_char: 0-based character offset where the block ends.
        cues: Structural cues detected for the block (e.g., heading, list).
        leading_blank_lines: Count of blank lines preceding the block.
        trailing_blank_lines: Count of blank lines following the block.
    """

    text: str
    start_line: int
    end_line: int
    start_char: int
    end_char: int
    cues: set[str] = field(default_factory=set)
    leading_blank_lines: int = 0
    trailing_blank_lines: int = 0


NaturalBreakDetector = Callable[[ParsedBlock, ParsedBlock, float], tuple[float, List[str]]]
EmbeddingFunction = Callable[[Sequence[str]], Sequence[Sequence[float]]]


def _classify_line(line: str) -> set[str]:
    cues: set[str] = set()
    stripped = line.strip()
    if stripped.startswith("#"):
        cues.add("heading")
    if re.match(r"^\s*[-*+]\s+", stripped) or re.match(r"^\s*\d+\.\s+", stripped):
        cues.add("list")
    if stripped.startswith(("```", "~~~")):
        cues.add("fence")
    if stripped.startswith(">"):
        cues.add("quote")
    return cues


def parse_blocks(text: str) -> List[ParsedBlock]:
    """
    Split text into structural blocks using blank lines as separators and
    annotate each block with simple structural cues.
    """
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


def embed_blocks(blocks: Iterable[ParsedBlock], embed_fn: EmbeddingFunction | None) -> List[List[float]]:
    """
    Embed each parsed block with a pluggable embedding function.

    Returns plain Python lists for predictable downstream use even if the
    embedding function returns numpy arrays.
    """
    texts = [b.text for b in blocks]
    if not texts:
        return []
    if embed_fn is None:
        return [[] for _ in texts]
    embeddings = embed_fn(texts)
    return [list(vec) for vec in embeddings]


def default_boundary_score(left: ParsedBlock, right: ParsedBlock, similarity: float) -> tuple[float, List[str]]:
    """
    Estimate a boundary score between two adjacent blocks, combining structural
    cues and semantic similarity drop.
    """
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


def hash_chunk_id(doc_id: str, start_char: int, end_char: int) -> str:
    """Create a deterministic chunk identifier from document and offsets."""
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
    chunk_id = hash_chunk_id(doc_id, start_char, end_char)
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


def chunk_document(
    doc_id: str,
    text: str,
    min_chars: int,
    target_chars: int,
    max_chars: int,
    overlap: int = 0,
    *,
    embed_fn: EmbeddingFunction | None,
    break_detector: NaturalBreakDetector | None = None,
    chunk_kind: str = "chapter_text",
) -> List[ChunkMetadata]:
    """
    Segment text into topic-aware chunks using structural cues and embeddings.

    Args:
        doc_id: Identifier used when deriving deterministic chunk IDs.
        text: Raw document contents to segment.
        min_chars: Minimum chunk size before a split is allowed.
        target_chars: Preferred chunk size before considering a split.
        max_chars: Hard cap that forces a split.
        overlap: Optional character overlap between consecutive chunks.
        embed_fn: Embedding function returning vectors for block texts.
        break_detector: Optional boundary scoring function; defaults to
            `default_boundary_score`.
        chunk_kind: Semantic label assigned to produced chunks.

    Returns:
        Ordered list of `ChunkMetadata` representing the detected chunks.
    """
    if not text or not text.strip():
        return []

    blocks = parse_blocks(text)
    if not blocks:
        return []

    boundary_fn = break_detector or default_boundary_score

    embeddings = embed_blocks(blocks, embed_fn)
    boundary_scores: List[tuple[float, List[str]]] = []

    num_embeddings = len(embeddings)

    for i in range(len(blocks) - 1):
        sim = _cosine_similarity(embeddings[i], embeddings[i + 1]) if i + 1 < num_embeddings else 0.0
        score, reasons = boundary_fn(blocks[i], blocks[i + 1], sim)
        boundary_scores.append((score, reasons))

    min_chars = max(1, min_chars)
    target_chars = max(min_chars, target_chars)
    max_chars = max(target_chars, max_chars)
    overlap = max(0, overlap)

    chunks: List[ChunkMetadata] = []
    start_idx = 0
    start_char = blocks[0].start_char

    i = 0
    while i < len(blocks):
        if i == len(blocks) - 1:
            chunk = _make_chunk(
                doc_id,
                text,
                blocks[start_idx],
                blocks[i],
                start_char,
                blocks[i].end_char,
                boundary_reasons=["document end"],
                confidence=1.0,
                overlap=overlap,
                chunk_kind=chunk_kind,
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
                doc_id,
                text,
                blocks[start_idx],
                blocks[i],
                start_char,
                projected_end,
                boundary_reasons=reasons or ["size target"],
                confidence=max(score, 0.35),
                overlap=overlap,
                chunk_kind=chunk_kind,
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

    return chunks
