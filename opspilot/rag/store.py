"""Qdrant-backed knowledge store with access-aware (role + team) filtering inside the vector search."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from qdrant_client import QdrantClient, models

from opspilot.config import Settings
from opspilot.rag.chunking import Chunk, chunk_document, parse_document
from opspilot.rag.embeddings import Embedder
from opspilot.security.identity import (
    ROLE_AUTOMATION,
    ROLE_EMPLOYEE,
    ROLE_IT_ADMIN,
    ROLE_TEAM_LEAD,
    Principal,
)

log = logging.getLogger("opspilot.rag")

_NS = uuid.UUID("6f1c6b7e-0a53-4a46-9c3e-0b8e9d3f6a11")


def effective_audiences(principal: Principal) -> list[str]:
    """Role hierarchy for document audiences: it_admin > team_lead > employee."""
    out = {ROLE_EMPLOYEE}
    if ROLE_TEAM_LEAD in principal.roles or ROLE_IT_ADMIN in principal.roles:
        out.add(ROLE_TEAM_LEAD)
    if ROLE_IT_ADMIN in principal.roles:
        out.add(ROLE_IT_ADMIN)
    if ROLE_AUTOMATION in principal.roles:
        out = {ROLE_EMPLOYEE}
    return sorted(out)


def access_filter(principal: Principal) -> models.Filter:
    """Applied *inside* Qdrant so unauthorised chunks can never occupy a top-K slot."""
    return models.Filter(
        must=[
            models.FieldCondition(key="audience", match=models.MatchAny(any=effective_audiences(principal))),
            models.FieldCondition(key="teams", match=models.MatchAny(any=["all", principal.team])),
        ]
    )


@dataclass
class RawHit:
    chunk_id: str
    doc_id: str
    title: str
    section: str
    text: str
    source: str
    score: float


class KnowledgeStore:
    def __init__(self, settings: Settings, embedder: Embedder):
        self.settings = settings
        self.embedder = embedder
        self.collection = settings.qdrant_collection
        if settings.qdrant_url:
            self.client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=10)
        else:
            self.client = QdrantClient(":memory:")
        self.chunk_count = 0

    def load_chunks(self, kb_dir: Path) -> list[Chunk]:
        chunks: list[Chunk] = []
        for path in sorted(kb_dir.glob("*.md")):
            chunks.extend(chunk_document(parse_document(path)))
        return chunks

    def ingest(self, kb_dir: Path | None = None, chunks: list[Chunk] | None = None) -> int:
        """(Re)build the collection from markdown documents. Idempotent."""
        chunks = chunks if chunks is not None else self.load_chunks(kb_dir or self.settings.kb_dir)
        self.embedder.fit([c.embed_text for c in chunks])
        vectors = self.embedder.embed([c.embed_text for c in chunks])
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self.client.create_collection(
            self.collection,
            vectors_config=models.VectorParams(size=self.embedder.dim, distance=models.Distance.COSINE),
        )
        if chunks:
            self.client.upsert(
                self.collection,
                points=[
                    models.PointStruct(
                        id=str(uuid.uuid5(_NS, c.chunk_id)),
                        vector=v,
                        payload={
                            "chunk_id": c.chunk_id,
                            "doc_id": c.doc_id,
                            "title": c.title,
                            "section": c.section,
                            "text": c.text,
                            "audience": c.audience,
                            "teams": c.teams,
                            "source": c.source,
                        },
                    )
                    for c, v in zip(chunks, vectors, strict=True)
                ],
            )
        self.chunk_count = len(chunks)
        log.info("ingested %d chunks", len(chunks))
        return len(chunks)

    def search(self, query: str, principal: Principal | None, limit: int = 4) -> list[RawHit]:
        if not self.client.collection_exists(self.collection):
            return []
        vec = self.embedder.embed([query])[0]
        res = self.client.query_points(
            self.collection,
            query=vec,
            query_filter=access_filter(principal) if principal else None,
            limit=limit,
            with_payload=True,
        )
        return [
            RawHit(
                chunk_id=p.payload["chunk_id"],
                doc_id=p.payload["doc_id"],
                title=p.payload["title"],
                section=p.payload["section"],
                text=p.payload["text"],
                source=p.payload["source"],
                score=round(float(p.score), 4),
            )
            for p in res.points
        ]
