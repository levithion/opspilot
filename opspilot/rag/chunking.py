"""Markdown parsing (front matter + heading-aware chunking)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$", re.M)


@dataclass
class Document:
    doc_id: str
    title: str
    audience: list[str]
    teams: list[str]
    body: str
    source: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    section: str
    text: str
    audience: list[str]
    teams: list[str]
    source: str

    @property
    def embed_text(self) -> str:
        return f"{self.title}. {self.section}. {self.text}"


def _as_list(v, default: list[str]) -> list[str]:
    if v is None:
        return default
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def parse_document(path: Path) -> Document:
    raw = path.read_text(encoding="utf-8")
    meta: dict = {}
    m = _FRONT.match(raw)
    body = raw
    if m:
        meta = yaml.safe_load(m.group(1)) or {}
        body = raw[m.end() :]
    title = str(meta.get("title") or path.stem.replace("-", " ").title())
    return Document(
        doc_id=str(meta.get("id") or path.stem),
        title=title,
        audience=_as_list(meta.get("audience"), ["employee"]),
        teams=_as_list(meta.get("teams"), ["all"]),
        body=body.strip(),
        source=path.name,
        meta=meta,
    )


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    out, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) + 1 > max_chars:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def chunk_document(doc: Document, max_chars: int = 700) -> list[Chunk]:
    """Split on headings, then pack paragraphs up to max_chars so a chunk stays on one topic."""
    sections: list[tuple[str, str]] = []
    matches = list(_HEADING.finditer(doc.body))
    if not matches:
        sections.append((doc.title, doc.body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(doc.body)
        sections.append((m.group(2).strip(), doc.body[m.end() : end].strip()))

    chunks: list[Chunk] = []
    for section, text in sections:
        if not text:
            continue
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        pieces = [piece for p in paragraphs for piece in _split_long(p, max_chars)]
        cur = ""
        packed: list[str] = []
        for piece in pieces:
            if cur and len(cur) + len(piece) + 2 > max_chars:
                packed.append(cur)
                cur = piece
            else:
                cur = f"{cur}\n\n{piece}".strip()
        if cur:
            packed.append(cur)
        for j, body in enumerate(packed):
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}#{len(chunks)}",
                    doc_id=doc.doc_id,
                    title=doc.title,
                    section=section if j == 0 else f"{section} (cont.)",
                    text=body,
                    audience=doc.audience,
                    teams=doc.teams,
                    source=doc.source,
                )
            )
    return chunks
