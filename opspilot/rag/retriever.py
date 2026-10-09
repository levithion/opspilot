"""Retriever: access-aware search + prompt-injection screening of retrieved text + citations."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from opspilot.config import Settings
from opspilot.rag.embeddings import tokenize
from opspilot.rag.store import KnowledgeStore
from opspilot.security.guardrails import sanitize_untrusted
from opspilot.security.identity import Principal


@dataclass
class Hit:
    doc_id: str
    title: str
    section: str
    text: str
    score: float
    source: str

    def as_dict(self) -> dict:
        return asdict(self)


class Retriever:
    def __init__(self, store: KnowledgeStore, settings: Settings):
        self.store, self.settings = store, settings
        self.quarantined = 0  # number of chunks withheld because they looked like injection

    # Weight of the lexical term-coverage signal added to the dense score (fixed a priori, not tuned on the eval set).
    LEXICAL_WEIGHT = 0.3

    def _rerank(self, query: str, raw: list) -> list[tuple[float, object]]:
        """Hybrid ranking: dense similarity plus the share of query terms the chunk actually contains."""
        terms = set(tokenize(query))

        def coverage(r) -> float:
            if not terms:
                return 0.0
            return len(terms & set(tokenize(f"{r.title} {r.section} {r.text}"))) / len(terms)

        scored = [(r.score + self.LEXICAL_WEIGHT * coverage(r), r) for r in raw]
        return sorted(scored, key=lambda t: t[0], reverse=True)

    def search(self, query: str, principal: Principal | None, k: int | None = None) -> list[Hit]:
        k = k or self.settings.rag_top_k
        raw = self._rerank(query, self.store.search(query, principal, limit=k * 4))
        hits: list[Hit] = []
        per_doc: dict[str, int] = {}
        for score, r in raw:
            if score < self.settings.rag_min_score or per_doc.get(r.doc_id, 0) >= 2:
                continue
            text, scan = sanitize_untrusted(r.text, source=r.source, threshold=self.settings.injection_block_threshold)
            if scan.flagged(self.settings.injection_block_threshold):
                self.quarantined += 1
                continue
            per_doc[r.doc_id] = per_doc.get(r.doc_id, 0) + 1
            hits.append(Hit(r.doc_id, r.title, r.section, text, round(score, 4), r.source))
            if len(hits) >= k:
                break
        return hits
