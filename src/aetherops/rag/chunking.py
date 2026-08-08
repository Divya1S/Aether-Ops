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
        chunks.append(Chunk(doc.id, doc.title, doc.kind, len(chunks),
                            start, doc.text[start:end].strip()))
        if end >= len(doc.text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c.text]


def chunk_paragraph(doc: Document, min_chars: int = 120) -> list[Chunk]:
    chunks: list[Chunk] = []
    offset = 0
    pending_text, pending_start = "", 0
    for block in doc.text.split("\n\n"):
        stripped = block.strip()
        start = doc.text.index(block, offset)
        offset = start + len(block)
        if not stripped:
            continue
        if pending_text:
            pending_text = f"{pending_text}\n\n{stripped}"
        else:
            pending_text, pending_start = stripped, start
        if len(pending_text) >= min_chars:
            chunks.append(Chunk(doc.id, doc.title, doc.kind, len(chunks),
                                pending_start, pending_text))
            pending_text = ""
    if pending_text:                               # trailing small paragraph
        chunks.append(Chunk(doc.id, doc.title, doc.kind, len(chunks),
                            pending_start, pending_text))
    return chunks


CHUNKERS = {
    "fixed": chunk_fixed,
    "paragraph": chunk_paragraph,
}


def get_chunker(name: str):
    if name not in CHUNKERS:
        raise ValueError(f"unknown chunker {name!r} (known: {sorted(CHUNKERS)})")
    return CHUNKERS[name]
