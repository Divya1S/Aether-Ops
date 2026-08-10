"""Chunking strategies (docs/06 §8, docs/17 acceptance #6).

Two deliberately different strategies, selectable by config so the retrieval
evaluation can compare them with numbers:

- fixed:      character windows with overlap — uniform size, ignores
              structure, never splits mid-word;
- paragraph:  blank-line boundaries with small-paragraph merging — respects
              authorship structure, variable size.

Every chunk carries its source attribution (doc_id + character offset),
which survives into Evidence citations as rag://doc#offset.
"""
from __future__ import annotations

from dataclasses import dataclass

from aetherops.rag.corpus import Document


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    doc_title: str
    doc_kind: str
    index: int          # chunk ordinal within the document
    start: int          # character offset in the source document
    text: str

    @property
    def ref(self) -> str:
        return f"rag://{self.doc_id}#{self.start}"


def chunk_fixed(doc: Document, size: int = 400, overlap: int = 80) -> list[Chunk]:
    if size <= overlap:
        raise ValueError("chunk size must exceed overlap")
    chunks: list[Chunk] = []
    start = 0
    while start < len(doc.text):
        end = min(start + size, len(doc.text))
        if end < len(doc.text):                    # don't split mid-word
            space = doc.text.rfind(" ", start, end)
            if space > start:
                end = space
        raw = doc.text[start:end]
        # Offset points at the actual (stripped) text, not the leading
        # whitespace the window happens to begin on (audit F5) — so
        # rag://doc#offset is accurate.
        lead = len(raw) - len(raw.lstrip())
        chunks.append(Chunk(doc.id, doc.title, doc.kind, len(chunks),
                            start + lead, raw.strip()))
        if end >= len(doc.text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c.text]


def _split_block(block: str, base: int, max_chars: int):
    """Yield (text, source_offset) for one paragraph block, hard-splitting at
    word boundaries if it exceeds max_chars so a document with no blank lines
    can't become one unbounded chunk (audit F4). A block is a source
    substring, so offsets stay exact."""
    if len(block.strip()) <= max_chars:
        yield block.strip(), base + (len(block) - len(block.lstrip()))
        return
    pos = 0
    while pos < len(block):
        end = min(pos + max_chars, len(block))
        if end < len(block):
            space = block.rfind(" ", pos, end)
            if space > pos:
                end = space
        raw = block[pos:end]
        if raw.strip():
            yield raw.strip(), base + pos + (len(raw) - len(raw.lstrip()))
        if end <= pos:
            break
        pos = end


def chunk_paragraph(doc: Document, min_chars: int = 120,
                    max_chars: int = 800) -> list[Chunk]:
    chunks: list[Chunk] = []
    offset = 0
    pending_text, pending_start = "", 0

    def flush():
        nonlocal pending_text
        if pending_text:
            chunks.append(Chunk(doc.id, doc.title, doc.kind, len(chunks),
                                pending_start, pending_text))
            pending_text = ""

    for block in doc.text.split("\n\n"):
        start = doc.text.index(block, offset)
        offset = start + len(block)
        if not block.strip():
            continue
        for piece, piece_start in _split_block(block, start, max_chars):
            # Flush before a merge that would push the chunk over max_chars.
            if pending_text and len(pending_text) + 2 + len(piece) > max_chars:
                flush()
            if pending_text:
                pending_text = f"{pending_text}\n\n{piece}"
            else:
                pending_text, pending_start = piece, piece_start
            if len(pending_text) >= min_chars:
                flush()
    flush()                                        # trailing small paragraph
    return chunks


CHUNKERS = {
    "fixed": chunk_fixed,
    "paragraph": chunk_paragraph,
}


def get_chunker(name: str):
    if name not in CHUNKERS:
        raise ValueError(f"unknown chunker {name!r} (known: {sorted(CHUNKERS)})")
    return CHUNKERS[name]
